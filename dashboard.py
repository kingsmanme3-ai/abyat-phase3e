from datetime import timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import require_cap
from ..models import Building, DailySnapshot, Site, Unit, UnitStatus, User
from ..snapshots import STATUS_KEYS, empty_counts, status_counts, today

router = APIRouter(prefix="/api", tags=["dashboard"])


@router.get("/dashboard")
def dashboard(
    site_id: int | None = None,
    days: int = Query(default=30, ge=7, le=180),
    db: Session = Depends(get_db),
    _: User = Depends(require_cap("view_dashboard")),
):
    """كل بيانات لوحة الإشغال في طلب واحد: الأعداد، المواقع، السلسلة الزمنية، أشرطة العقود."""
    sites = db.scalars(select(Site).where(Site.is_active.is_(True)).order_by(Site.name)).all()
    counts = status_counts(db)

    rows, totals = [], empty_counts()
    for s in sites:
        c = counts.get(s.id) or empty_counts()
        rows.append({"id": s.id, "name": s.name, "total": sum(c.values()), "counts": c})
        if site_id is None or s.id == site_id:
            for k in STATUS_KEYS:
                totals[k] += c[k]

    # الرسم المساحي: مجموع اللقطات اليومية
    start = today() - timedelta(days=days - 1)
    cols = [func.sum(getattr(DailySnapshot, k)).label(k) for k in STATUS_KEYS]
    hstmt = (select(DailySnapshot.day, *cols)
             .where(DailySnapshot.day >= start)
             .group_by(DailySnapshot.day).order_by(DailySnapshot.day))
    if site_id is not None:
        hstmt = hstmt.where(DailySnapshot.site_id == site_id)
    history = [{"day": r.day.isoformat(), **{k: int(getattr(r, k) or 0) for k in STATUS_KEYS}}
               for r in db.execute(hstmt).all()]

    # أشرطة العقود: الوحدات المؤجرة حسب قرب انتهاء العقد
    t = today()
    cstmt = (select(Unit, Building.name, Site.name)
             .join(Building, Building.id == Unit.building_id)
             .join(Site, Site.id == Building.site_id)
             .where(Unit.status == UnitStatus.rented, Site.is_active.is_(True)))
    if site_id is not None:
        cstmt = cstmt.where(Site.id == site_id)
    buckets = {"expired": 0, "d30": 0, "d60": 0, "d90": 0, "later": 0, "no_date": 0}
    expiring = []
    for unit, bname, sname in db.execute(cstmt).all():
        if unit.contract_end is None:
            buckets["no_date"] += 1
            continue
        left = (unit.contract_end - t).days
        if left < 0:
            buckets["expired"] += 1
        elif left <= 30:
            buckets["d30"] += 1
        elif left <= 60:
            buckets["d60"] += 1
        elif left <= 90:
            buckets["d90"] += 1
        else:
            buckets["later"] += 1
        if left <= 90:
            expiring.append({"site": sname, "building": bname, "unit": unit.number,
                             "tenant": unit.tenant_name,
                             "contract_end": unit.contract_end.isoformat(), "days_left": left})
    expiring.sort(key=lambda x: x["days_left"])

    return {
        "totals": {"total": sum(totals.values()), "counts": totals},
        "sites": rows,
        "history": history,
        "contracts": buckets,
        "expiring": expiring[:15],
    }
