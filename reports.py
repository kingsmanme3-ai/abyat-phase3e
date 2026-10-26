from datetime import date, datetime
from typing import Literal
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.orm import Session

from ..audit import audit
from ..database import get_db
from ..deps import get_current_user
from ..models import Role, Site, UnitStatus, User
from ..report_runner import MIME, render
from ..reports import REPORTS, UPCOMING, build_report
from ..snapshots import today

router = APIRouter(prefix="/api/reports", tags=["reports"])


def _jsonable(rows: list[dict]) -> list[dict]:
    return [{k: (v.isoformat() if isinstance(v, (date, datetime)) else v) for k, v in r.items()} for r in rows]


@router.get("/types")
def report_types(user: User = Depends(get_current_user)):
    """التقارير المتاحة للمستخدم الحالي (تقرير المستخدمين للمدير فقط) مع فلاتر كل تقرير."""
    items = [{"key": d.key, "label": d.label, "description": d.description,
              "filters": list(d.filters), "available": True}
             for d in REPORTS.values() if user.role == Role.admin or (not d.admin_only and user.visibility != "assigned")]
    return items + UPCOMING


@router.get("/{key}")
def get_report(
    key: str,
    site_id: int | None = None,
    status: UnitStatus | None = None,
    days: int | None = Query(default=None, ge=0, le=3650, description="للعقود: المنتهية وما ينتهي خلال N يوماً"),
    format: Literal["json", "csv", "xlsx"] = "json",
    limit: int = Query(default=500, ge=1, le=2000),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    d = REPORTS.get(key)
    if d is None:
        raise HTTPException(404, "التقرير غير معروف أو غير متاح بعد")
    if d.admin_only and user.role != Role.admin:
        raise HTTPException(403, "هذا التقرير للمدير فقط")
    if user.role != Role.admin and user.visibility == "assigned":
        raise HTTPException(403, "نطاقك المهام المسندة إليك فقط")
    if site_id is not None and not db.get(Site, site_id):
        raise HTTPException(404, "الموقع غير موجود")

    if format == "json":
        columns, rows = build_report(db, key, site_id=site_id, status=status, days=days)
        return {"key": key, "label": d.label,
                "columns": [{"key": k, "label": l} for k, l in columns],
                "rows": _jsonable(rows[:limit]), "total_rows": len(rows), "truncated": len(rows) > limit}

    data, count, label = render(db, key, site_id=site_id, status=status, days=days, fmt=format)
    audit(db, user, "export", "report", None, f"{key} {format} rows={count}")
    db.commit()
    name = quote(f"{label}-{today().isoformat()}.{format}")
    return Response(data, media_type=MIME[format],
                    headers={"Content-Disposition": f"attachment; filename=\"report.{format}\"; filename*=UTF-8''{name}"})
