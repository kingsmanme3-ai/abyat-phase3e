from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import get_current_user, require_cap
from ..models import STATUS_LABELS_AR, Building, Site, Unit, UnitStatus, User

router = APIRouter(prefix="/api", tags=["summary"])


@router.get("/meta/statuses")
def statuses(_: User = Depends(get_current_user)):
    return [{"key": s.value, "label": STATUS_LABELS_AR[s]} for s in UnitStatus]


@router.get("/summary")
def summary(db: Session = Depends(get_db), _: User = Depends(require_cap("view_dashboard"))):
    """أعداد الوحدات حسب الحالة، إجمالي وحسب الموقع (أساس لوحة الإشغال في المرحلة 2)."""
    rows = db.execute(
        select(Building.site_id, Unit.status, func.count(Unit.id))
        .join(Building, Building.id == Unit.building_id)
        .group_by(Building.site_id, Unit.status)
    ).all()

    def empty():
        return {s.value: 0 for s in UnitStatus}

    per_site: dict[int, dict[str, int]] = {}
    for site_id, st, n in rows:
        key = st.value if isinstance(st, UnitStatus) else st
        per_site.setdefault(site_id, empty())[key] = n

    total = empty()
    sites_out = []
    for site in db.scalars(select(Site).where(Site.is_active.is_(True)).order_by(Site.name)):
        counts = per_site.get(site.id, empty())
        for k, v in counts.items():
            total[k] += v
        sites_out.append({"site_id": site.id, "name": site.name,
                          "total": sum(counts.values()), "by_status": counts})
    return {"total": sum(total.values()), "by_status": total, "sites": sites_out}
