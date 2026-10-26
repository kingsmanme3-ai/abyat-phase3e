from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..audit import audit
from ..database import get_db
from ..deps import get_current_user, require_admin
from ..models import Site, User
from ..schemas import SiteCreate, SiteOut, SiteUpdate

router = APIRouter(prefix="/api/sites", tags=["sites"])


def _check_unique(db: Session, name: str | None, code: str | None, exclude_id: int | None = None):
    if name:
        q = select(Site.id).where(Site.name == name)
        if exclude_id:
            q = q.where(Site.id != exclude_id)
        if db.scalar(q):
            raise HTTPException(409, "اسم الموقع موجود من قبل")
    if code:
        q = select(Site.id).where(Site.code == code)
        if exclude_id:
            q = q.where(Site.id != exclude_id)
        if db.scalar(q):
            raise HTTPException(409, "رمز الموقع موجود من قبل")


@router.get("", response_model=list[SiteOut])
def list_sites(include_inactive: bool = False, db: Session = Depends(get_db),
               _: User = Depends(get_current_user)):
    q = select(Site).order_by(Site.name)
    if not include_inactive:
        q = q.where(Site.is_active.is_(True))
    return db.scalars(q).all()


@router.post("", response_model=SiteOut, status_code=201)
def create_site(body: SiteCreate, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    _check_unique(db, body.name, body.code)
    site = Site(**body.model_dump())
    db.add(site)
    db.flush()
    audit(db, admin, "create", "site", site.id, site.name)
    db.commit()
    return site


@router.get("/{site_id}", response_model=SiteOut)
def get_site(site_id: int, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    site = db.get(Site, site_id)
    if not site:
        raise HTTPException(404, "الموقع غير موجود")
    return site


@router.patch("/{site_id}", response_model=SiteOut)
def update_site(site_id: int, body: SiteUpdate, admin: User = Depends(require_admin),
                db: Session = Depends(get_db)):
    site = db.get(Site, site_id)
    if not site:
        raise HTTPException(404, "الموقع غير موجود")
    data = body.model_dump(exclude_unset=True)
    _check_unique(db, data.get("name"), data.get("code"), exclude_id=site.id)
    for k, v in data.items():
        setattr(site, k, v)
    audit(db, admin, "update", "site", site.id, ", ".join(sorted(data)))
    db.commit()
    return site
