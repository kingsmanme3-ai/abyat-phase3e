"""حساب صلاحيات المستخدم الفعلية (المستوى + المصفوفة المحفوظة) وفحص ظهور الوحدات."""
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .levels import CAP_KEYS, caps_for
from .models import LevelCapability, Role, UnitTask, User


def user_caps(db: Session, user: User) -> set[str]:
    if user.role == Role.admin:
        return set(CAP_KEYS)
    rows = db.scalars(select(LevelCapability).where(LevelCapability.level == user.level))
    return caps_for(user.level, user.role.value, {r.capability: r.allowed for r in rows})


def ensure_cap(db: Session, user: User, cap: str, msg: str = "لا تملك هذه الصلاحية") -> None:
    if cap not in user_caps(db, user):
        raise HTTPException(403, msg)


def has_open_task(db: Session, user_id: int, unit_id: int) -> bool:
    return db.scalar(select(UnitTask.id).where(UnitTask.assignee_id == user_id, UnitTask.unit_id == unit_id,
                                                UnitTask.status == "open").limit(1)) is not None


def ensure_unit_visible(db: Session, user: User, unit_id: int) -> None:
    if user.role != Role.admin and user.visibility == "assigned" and not has_open_task(db, user.id, unit_id):
        raise HTTPException(403, "هذه الوحدة ليست ضمن مهامك")
