from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..audit import audit
from ..database import get_db
from ..deps import require_admin
from ..levels import access_for, can_manage
from ..models import Role, Site, User, UserSite
from ..schemas import UserCreate, UserOut, UserSitesIn, UserUpdate
from ..security import hash_password

router = APIRouter(prefix="/api/users", tags=["users"], dependencies=[Depends(require_admin)])


def _valid_site_ids(db: Session, site_ids: list[int]) -> list[int]:
    ids = sorted(set(site_ids))
    if ids:
        found = set(db.scalars(select(Site.id).where(Site.id.in_(ids))))
        missing = [i for i in ids if i not in found]
        if missing:
            raise HTTPException(422, f"مواقع غير موجودة: {missing}")
    return ids


def _guard_target(actor: User, target_level: str) -> None:
    if actor.level != "general_manager" and not can_manage(actor.level, target_level):
        raise HTTPException(403, "لا تملك صلاحية على مستخدم بمستوى مساوٍ لمستواك أو أعلى منه")


@router.get("", response_model=list[UserOut])
def list_users(db: Session = Depends(get_db)):
    return db.scalars(select(User).order_by(User.id)).all()


@router.post("", response_model=UserOut, status_code=201)
def create_user(body: UserCreate, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    username = body.username.lower()
    if db.scalar(select(User).where(User.username == username)):
        raise HTTPException(409, "اسم المستخدم مستخدم من قبل")
    site_ids = _valid_site_ids(db, body.site_ids)
    role, level = body.role, body.job_level
    if level:                                   # 3هـ: فئة الوصول تُشتق من المستوى
        _guard_target(admin, level)
        role = Role(access_for(level))
    user = User(username=username, full_name=body.full_name,
                password_hash=hash_password(body.password), role=role, job_level=level,
                visibility_override=body.visibility)
    user.site_links = [UserSite(site_id=s) for s in site_ids]
    db.add(user)
    db.flush()
    audit(db, admin, "create", "user", user.id, f"{username} / {user.level}")
    db.commit()
    return user


@router.patch("/{user_id}", response_model=UserOut)
def update_user(user_id: int, body: UserUpdate, admin: User = Depends(require_admin),
                db: Session = Depends(get_db)):
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(404, "المستخدم غير موجود")
    data = body.model_dump(exclude_unset=True)
    if user.id != admin.id:
        _guard_target(admin, user.level)
    if "job_level" in data and data["job_level"] is None:
        data.pop("job_level")
    if "job_level" in data and data["job_level"] == user.job_level:
        data.pop("job_level")                   # لم يتغير: لا نمسّ فئة الوصول (يحفظ المشاهدين القدامى)
    if "job_level" in data:
        if user.id == admin.id:
            raise HTTPException(400, "لا يمكنك تغيير مستواك الوظيفي")
        _guard_target(admin, data["job_level"])
        data["role"] = Role(access_for(data["job_level"]))
    if "visibility" in data:
        data["visibility_override"] = data.pop("visibility")
    if user.id == admin.id and (data.get("is_active") is False or
                                ("role" in data and data["role"] != admin.role)):
        raise HTTPException(400, "لا يمكنك تعطيل نفسك أو تغيير صلاحيتك")
    if "password" in data and data["password"] is not None:
        user.password_hash = hash_password(data.pop("password"))
    else:
        data.pop("password", None)
    for k, v in data.items():
        setattr(user, k, v)
    audit(db, admin, "update", "user", user.id, ", ".join(sorted(body.model_dump(exclude_unset=True))))
    db.commit()
    return user


@router.put("/{user_id}/sites", response_model=UserOut)
def set_user_sites(user_id: int, body: UserSitesIn, admin: User = Depends(require_admin),
                   db: Session = Depends(get_db)):
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(404, "المستخدم غير موجود")
    if user.id != admin.id:
        _guard_target(admin, user.level)
    site_ids = _valid_site_ids(db, body.site_ids)
    user.site_links.clear()
    db.flush()
    user.site_links.extend(UserSite(site_id=s) for s in site_ids)
    audit(db, admin, "set_sites", "user", user.id, str(site_ids))
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "تعذّر حفظ المواقع")
    return user
