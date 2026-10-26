"""تشغيل التقارير وحفظها وإرسالها بالبريد، وتنظيف القديم، ومُجدوِل التقارير (المرحلة 3)."""
import asyncio
import logging
import os
import smtplib
import uuid
from datetime import timedelta
from email.message import EmailMessage

from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from .config import settings
from .database import SessionLocal
from .models import ReportRun, ReportSchedule, User, utcnow
from .realtime import manager
from .report_engine import to_csv, to_xlsx
from .reports import REPORTS, build_report
from .scheduling import compute_next_run
from .snapshots import today

log = logging.getLogger("abyat.reports")

MIME = {"xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "csv": "text/csv; charset=utf-8"}


def render(db: Session, report_type: str, *, site_id=None, status=None, days=None, fmt="xlsx"):
    """يبني التقرير ويعيد (البايتات، عدد الصفوف، عنوان التقرير)."""
    d = REPORTS[report_type]
    columns, rows = build_report(db, report_type, site_id=site_id, status=status, days=days)
    data = to_xlsx(d.label, columns, rows) if fmt == "xlsx" else to_csv(columns, rows)
    return data, len(rows), d.label


def smtp_configured() -> bool:
    return bool(settings.smtp_host and settings.smtp_from)


def send_email(recipients: list[str], subject: str, body: str, data: bytes, filename: str, fmt: str) -> None:
    msg = EmailMessage()
    msg["From"] = settings.smtp_from
    msg["To"] = ", ".join(recipients)
    msg["Subject"] = subject
    msg.set_content(body)
    maintype, subtype = MIME[fmt].split(";")[0].split("/")
    msg.add_attachment(data, maintype=maintype, subtype=subtype, filename=filename)
    if settings.smtp_port == 465:
        smtp = smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port, timeout=30)
    else:
        smtp = smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=30)
    with smtp:
        if settings.smtp_port != 465 and settings.smtp_starttls:
            smtp.starttls()
        if settings.smtp_user:
            smtp.login(settings.smtp_user, settings.smtp_password)
        smtp.send_message(msg)


def run_and_store(db: Session, *, report_type: str, site_id=None, status=None, days=None, fmt="xlsx",
                  schedule: ReportSchedule | None = None, user: User | None = None,
                  trigger: str = "manual") -> ReportRun:
    """يولّد التقرير ويحفظ الملف ويسجّل التشغيل (ويرسله بالبريد إن وُجد مستلمون). لا يرفع استثناءً للفشل."""
    run = ReportRun(schedule_id=schedule.id if schedule else None, report_type=report_type,
                    site_id=site_id, fmt=fmt, trigger=trigger, created_by=user.id if user else None)
    try:
        data, count, label = render(db, report_type, site_id=site_id, status=status, days=days, fmt=fmt)
        os.makedirs(settings.reports_dir, exist_ok=True)
        fname = f"{uuid.uuid4().hex}.{fmt}"
        with open(os.path.join(settings.reports_dir, fname), "wb") as f:
            f.write(data)
        run.file_name, run.row_count, run.status = fname, count, "ok"
        run.download_name = f"{label}-{today().isoformat()}.{fmt}"

        recipients = [x for x in ((schedule.recipients if schedule else "") or "").split(",") if x]
        if recipients:
            if not smtp_configured():
                run.email_note = "لم يُرسل: إعدادات البريد (SMTP) غير مضبوطة"
            else:
                try:
                    send_email(recipients, f"أسوار بلس — {label} ({today().isoformat()})",
                               f"مرفق {label} بتاريخ {today().isoformat()} ({count} صف).\nمنظومة أسوار بلس للحصر والإشغال.",
                               data, f"{report_type}-{today().isoformat()}.{fmt}", fmt)
                    run.emailed = True
                    run.email_note = f"أُرسل إلى {len(recipients)} مستلم"
                except Exception as e:      # فشل البريد لا يُفشل التقرير
                    log.exception("فشل إرسال التقرير بالبريد")
                    run.email_note = f"فشل الإرسال: {str(e)[:200]}"
    except Exception as e:
        log.exception("فشل توليد التقرير")
        db.rollback()
        run.status, run.error = "failed", str(e)[:500]
        run.download_name = ""
    db.add(run)
    db.commit()
    return run


def cleanup_old_runs(db: Session) -> int:
    cutoff = utcnow() - timedelta(days=settings.reports_keep_days)
    old = db.scalars(select(ReportRun).where(ReportRun.created_at < cutoff)).all()
    for r in old:
        if r.file_name:
            try:
                os.remove(os.path.join(settings.reports_dir, r.file_name))
            except OSError:
                pass
        db.delete(r)
    db.commit()
    return len(old)


# ---------------- المُجدوِل ----------------
def run_due_schedules() -> int:
    """يشغّل كل الجداول التي حان موعدها (مرة واحدة لكل جدول حتى لو فاتته مواعيد)، ويحسب الموعد التالي."""
    with SessionLocal() as db:
        now = utcnow()
        due = db.scalars(select(ReportSchedule).where(
            ReportSchedule.is_active.is_(True), ReportSchedule.next_run_at.is_not(None),
            ReportSchedule.next_run_at <= now)).all()
        for s in due:
            run_and_store(db, report_type=s.report_type, site_id=s.site_id, status=s.status, days=s.days,
                          fmt=s.fmt, schedule=s, trigger="schedule")
            s.last_run_at = now
            s.next_run_at = compute_next_run(s.frequency, s.hour, s.weekday, s.day_of_month,
                                             now, settings.tz_offset_hours)
            db.commit()
        return len(due)


def _cleanup_job() -> None:
    with SessionLocal() as db:
        cleanup_old_runs(db)


async def scheduler_loop() -> None:
    """يفحص كل دقيقة؛ ينظّف التقارير القديمة مرة يومياً."""
    last_cleanup = None
    while True:
        await asyncio.sleep(60)
        try:
            if await run_in_threadpool(run_due_schedules):
                await manager.broadcast({"type": "reports"})
            day = today()
            if day != last_cleanup:
                await run_in_threadpool(_cleanup_job)
                last_cleanup = day
        except Exception:
            log.exception("فشل دورة مُجدوِل التقارير")
