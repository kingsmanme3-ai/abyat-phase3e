import os

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..audit import audit
from ..config import settings
from ..database import get_db
from ..deps import require_admin
from ..models import ReportRun, ReportSchedule, Site, User, utcnow
from ..realtime import manager
from ..report_runner import MIME, run_and_store, smtp_configured
from ..reports import REPORTS
from ..schemas import ScheduleIn, ScheduleUpdate
from ..scheduling import compute_next_run

router = APIRouter(prefix="/api/report-schedules", tags=["reports"])
runs_router = APIRouter(prefix="/api/report-runs", tags=["reports"])


def _recipients(s: ReportSchedule) -> list[str]:
    return [x for x in (s.recipients or "").split(",") if x]


def _run_out(r: ReportRun, names: dict[int, str] | None = None) -> dict:
    label = REPORTS[r.report_type].label if r.report_type in REPORTS else r.report_type
    return {"id": r.id, "schedule_id": r.schedule_id,
            "schedule_name": (names or {}).get(r.schedule_id) if r.schedule_id else None,
            "report_type": r.report_type, "label": label, "site_id": r.site_id, "fmt": r.fmt,
            "download_name": r.download_name, "row_count": r.row_count, "status": r.status,
            "error": r.error, "trigger": r.trigger, "emailed": r.emailed,
            "email_note": r.email_note, "created_at": r.created_at}


def _out(s: ReportSchedule, last: ReportRun | None) -> dict:
    return {"id": s.id, "name": s.name, "report_type": s.report_type, "site_id": s.site_id,
            "status": s.status, "days": s.days, "fmt": s.fmt, "frequency": s.frequency, "hour": s.hour,
            "weekday": s.weekday, "day_of_month": s.day_of_month, "recipients": _recipients(s),
            "is_active": s.is_active, "last_run_at": s.last_run_at, "next_run_at": s.next_run_at,
            "last_status": last.status if last else None,
            "last_error": last.error if last else None,
            "last_note": last.email_note if last else None}


def _last_runs(db: Session, ids: list[int]) -> dict[int, ReportRun]:
    out: dict[int, ReportRun] = {}
    if not ids:
        return out
    for r in db.scalars(select(ReportRun).where(ReportRun.schedule_id.in_(ids))
                        .order_by(ReportRun.id.desc())):
        out.setdefault(r.schedule_id, r)
    return out


def _validate(data: dict) -> ScheduleIn:
    try:
        return ScheduleIn.model_validate(data)
    except ValidationError as e:
        msg = str(e.errors()[0]["msg"]).replace("Value error, ", "")
        raise HTTPException(422, msg)


def _check_site(db: Session, site_id: int | None) -> None:
    if site_id is not None and not db.get(Site, site_id):
        raise HTTPException(404, "الموقع غير موجود")


def _get(db: Session, schedule_id: int) -> ReportSchedule:
    s = db.get(ReportSchedule, schedule_id)
    if not s:
        raise HTTPException(404, "الجدولة غير موجودة")
    return s


def _next(v: ScheduleIn | ReportSchedule):
    return compute_next_run(v.frequency, v.hour, v.weekday, v.day_of_month, utcnow(), settings.tz_offset_hours)


@router.get("/config")
def config(_: User = Depends(require_admin)):
    return {"smtp_configured": smtp_configured(), "tz_offset_hours": settings.tz_offset_hours}


@router.get("")
def list_schedules(db: Session = Depends(get_db), _: User = Depends(require_admin)):
    rows = db.scalars(select(ReportSchedule).order_by(ReportSchedule.id)).all()
    last = _last_runs(db, [s.id for s in rows])
    return [_out(s, last.get(s.id)) for s in rows]


@router.post("", status_code=201)
def create_schedule(body: ScheduleIn, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    _check_site(db, body.site_id)
    s = ReportSchedule(
        name=body.name, report_type=body.report_type, site_id=body.site_id,
        status=body.status.value if body.status else None, days=body.days, fmt=body.fmt,
        frequency=body.frequency, hour=body.hour, weekday=body.weekday, day_of_month=body.day_of_month,
        recipients=",".join(body.recipients) or None, is_active=body.is_active, created_by=admin.id,
        next_run_at=_next(body) if body.is_active else None)
    db.add(s)
    db.flush()
    audit(db, admin, "create", "report_schedule", s.id, f"{s.name} ({s.report_type}, {s.frequency})")
    db.commit()
    return _out(s, None)


@router.patch("/{schedule_id}")
def update_schedule(schedule_id: int, body: ScheduleUpdate, admin: User = Depends(require_admin),
                    db: Session = Depends(get_db)):
    s = _get(db, schedule_id)
    cur = {"name": s.name, "report_type": s.report_type, "site_id": s.site_id, "status": s.status,
           "days": s.days, "fmt": s.fmt, "frequency": s.frequency, "hour": s.hour, "weekday": s.weekday,
           "day_of_month": s.day_of_month, "recipients": _recipients(s), "is_active": s.is_active}
    v = _validate({**cur, **body.model_dump(exclude_unset=True)})
    _check_site(db, v.site_id)

    timing_changed = (s.frequency, s.hour, s.weekday, s.day_of_month) != (
        v.frequency, v.hour, v.weekday, v.day_of_month)
    reactivated = v.is_active and not s.is_active
    s.name, s.report_type, s.site_id = v.name, v.report_type, v.site_id
    s.status = v.status.value if v.status else None
    s.days, s.fmt, s.frequency, s.hour = v.days, v.fmt, v.frequency, v.hour
    s.weekday, s.day_of_month = v.weekday, v.day_of_month
    s.recipients = ",".join(v.recipients) or None
    s.is_active = v.is_active
    if not v.is_active:
        s.next_run_at = None
    elif timing_changed or reactivated or s.next_run_at is None:
        s.next_run_at = _next(v)
    audit(db, admin, "update", "report_schedule", s.id, ", ".join(sorted(body.model_dump(exclude_unset=True))))
    db.commit()
    return _out(s, _last_runs(db, [s.id]).get(s.id))


@router.delete("/{schedule_id}", status_code=204)
def delete_schedule(schedule_id: int, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    s = _get(db, schedule_id)
    for r in db.scalars(select(ReportRun).where(ReportRun.schedule_id == s.id)):
        r.schedule_id = None            # تبقى الملفات المولّدة متاحة للتنزيل
    audit(db, admin, "delete", "report_schedule", s.id, s.name)
    db.delete(s)
    db.commit()


@router.post("/{schedule_id}/run")
def run_now(schedule_id: int, background: BackgroundTasks, admin: User = Depends(require_admin),
            db: Session = Depends(get_db)):
    """تشغيل فوري للجدولة (يُرسل بالبريد إن كان مضبوطاً) دون تغيير موعدها التالي."""
    s = _get(db, schedule_id)
    run = run_and_store(db, report_type=s.report_type, site_id=s.site_id, status=s.status, days=s.days,
                        fmt=s.fmt, schedule=s, user=admin, trigger="manual")
    audit(db, admin, "run", "report_schedule", s.id, run.status)
    db.commit()
    background.add_task(manager.broadcast, {"type": "reports"})
    return _run_out(run, {s.id: s.name})


@runs_router.get("")
def list_runs(schedule_id: int | None = None, limit: int = Query(default=30, ge=1, le=200),
              db: Session = Depends(get_db), _: User = Depends(require_admin)):
    stmt = select(ReportRun).order_by(ReportRun.id.desc()).limit(limit)
    if schedule_id:
        stmt = stmt.where(ReportRun.schedule_id == schedule_id)
    runs = db.scalars(stmt).all()
    names = {s.id: s.name for s in db.scalars(select(ReportSchedule))}
    return [_run_out(r, names) for r in runs]


@runs_router.get("/{run_id}/download")
def download_run(run_id: int, db: Session = Depends(get_db), _: User = Depends(require_admin)):
    r = db.get(ReportRun, run_id)
    path = os.path.join(settings.reports_dir, r.file_name) if r and r.file_name else None
    if not r or r.status != "ok" or not path or not os.path.isfile(path):
        raise HTTPException(404, "الملف غير موجود (ربما حُذف بعد انتهاء مدة الاحتفاظ)")
    return FileResponse(path, media_type=MIME[r.fmt], filename=r.download_name or os.path.basename(path))
