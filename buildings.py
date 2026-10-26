from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..audit import audit
from ..database import get_db
from ..deps import get_current_user, require_admin
from ..models import Building, Site, User
from ..schemas import BuildingCreate, BuildingOut, BuildingUpdate

router = APIRouter(prefix="/api/buildings", tags=["buildings"])


@router.get("", response_model=list[BuildingOut])
def list_buildings(site_id: int | None = None, include_inactive: bool = False,
                   db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    q = select(Building).order_by(Building.site_id, Building.name)
    if site_id:
        q = q.where(Building.site_id == site_id)
    if not include_inactive:
        q = q.where(Building.is_active.is_(True))
    return db.scalars(q).all()


@router.post("", response_model=BuildingOut, status_code=201)
def create_building(body: BuildingCreate, admin: User = Depends(require_admin),
                    db: Session = Depends(get_db)):
    if not db.get(Site, body.site_id):
        raise HTTPException(404, "الموقع غير موجود")
    if db.scalar(select(Building.id).where(Building.site_id == body.site_id, Building.name == body.name)):
        raise HTTPException(409, "اسم العمارة موجود في هذا الموقع")
    b = Building(**body.model_dump())
    db.add(b)
    db.flush()
    audit(db, admin, "create", "building", b.id, f"{b.name} (site {b.site_id})")
    db.commit()
    return b


@router.patch("/{building_id}", response_model=BuildingOut)
def update_building(building_id: int, body: BuildingUpdate, admin: User = Depends(require_admin),
                    db: Session = Depends(get_db)):
    b = db.get(Building, building_id)
    if not b:
        raise HTTPException(404, "العمارة غير موجودة")
    data = body.model_dump(exclude_unset=True)
    new_name = data.get("name")
    if new_name and new_name != b.name and db.scalar(
        select(Building.id).where(Building.site_id == b.site_id, Building.name == new_name)
    ):
        raise HTTPException(409, "اسم العمارة موجود في هذا الموقع")
    for k, v in data.items():
        setattr(b, k, v)
    audit(db, admin, "update", "building", b.id, ", ".join(sorted(data)))
    db.commit()
    return b
