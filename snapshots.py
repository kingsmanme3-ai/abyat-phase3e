from datetime import date, datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .config import settings
from .models import (Building, DailySnapshot, Site, Unit, UnitStatus,
                     UnitStatusHistory)

STATUS_KEYS = [s.value for s in UnitStatus]


def empty_counts() -> dict:
    return {k: 0 for k in STATUS_KEYS}


def _tz() -> timezone:
    return timezone(timedelta(hours=settings.tz_offset_hours))


def today() -> date:
    return datetime.now(_tz()).date()


def to_local_date(dt: datetime) -> date:
    if dt.tzinfo is None:           # SQLite يعيد تواريخ بلا منطقة زمنية (UTC)
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(_tz()).date()


def _key(st) -> str:
    return st.value if isinstance(st, UnitStatus) else st


def status_counts(db: Session, site_id: int | None = None) -> dict:
    """{site_id: {status: count}} للمواقع التي فيها وحدات."""
    stmt = (
        select(Building.site_id, Unit.status, func.count(Unit.id))
        .select_from(Unit)
        .join(Building, Building.id == Unit.building_id)
        .group_by(Building.site_id, Unit.status)
    )
    if site_id is not None:
        stmt = stmt.where(Building.site_id == site_id)
    out: dict = {}
    for sid, st, n in db.execute(stmt).all():
        out.setdefault(sid, empty_counts())[_key(st)] = n
    return out


def _write_snapshot(db: Session, day: date, site_id: int, counts: dict) -> None:
    snap = db.scalar(select(DailySnapshot).where(
        DailySnapshot.day == day, DailySnapshot.site_id == site_id))
    if snap is None:
        snap = DailySnapshot(day=day, site_id=site_id)
        db.add(snap)
    snap.total = sum(counts.values())
    for k in STATUS_KEYS:
        setattr(snap, k, counts.get(k, 0))


def refresh_snapshot(db: Session, site_id: int) -> None:
    """يحدّث لقطة اليوم للموقع. استدعِ db.flush() قبلها (الجلسة بلا autoflush). لا يعمل commit."""
    counts = status_counts(db, site_id).get(site_id) or empty_counts()
    _write_snapshot(db, today(), site_id, counts)


def refresh_all_snapshots(db: Session) -> None:
    counts = status_counts(db)
    for sid in db.scalars(select(Site.id)).all():
        _write_snapshot(db, today(), sid, counts.get(sid) or empty_counts())
    db.commit()


def backfill_history(db: Session, days: int = 90) -> None:
    """عند أول تشغيل للمرحلة 2 (جدول اللقطات فارغ): يعيد بناء التاريخ من unit_status_history."""
    if db.scalar(select(func.count(DailySnapshot.id))):
        return
    rows = db.execute(
        select(UnitStatusHistory.unit_id, UnitStatusHistory.new_status,
               UnitStatusHistory.changed_at, Building.site_id)
        .select_from(UnitStatusHistory)
        .join(Unit, Unit.id == UnitStatusHistory.unit_id)
        .join(Building, Building.id == Unit.building_id)
        .order_by(UnitStatusHistory.changed_at, UnitStatusHistory.id)
    ).all()
    if not rows:
        return
    events = [(to_local_date(r.changed_at), r.unit_id, r.site_id, _key(r.new_status)) for r in rows]
    end = today()
    start = max(events[0][0], end - timedelta(days=days - 1))
    state: dict = {}          # unit_id -> (site_id, status)
    i, n = 0, len(events)
    day = start
    while day <= end:
        while i < n and events[i][0] <= day:
            _, uid, sid, st = events[i]
            state[uid] = (sid, st)
            i += 1
        per_site: dict = {}
        for sid, st in state.values():
            per_site.setdefault(sid, empty_counts())[st] += 1
        for sid, counts in per_site.items():
            _write_snapshot(db, day, sid, counts)
        db.flush()
        day += timedelta(days=1)
    db.commit()
