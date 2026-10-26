"""المرحلة 3هـ: تعريف المستويات الستة ومصفوفة الصلاحيات (يعدّلها المدير العام وحده)."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..audit import audit
from ..database import get_db
from ..deps import get_current_user, require_admin
from ..levels import (CAPABILITIES, DEFAULT_MATRIX, LEVELS, LOCKED_LEVELS, TASK_KINDS, VISIBILITIES, caps_for,
                      validate_matrix)
from ..models import LevelCapability, Role, User

router = APIRouter(prefix="/api/levels", tags=["levels"])


def seed_matrix(db: Session) -> None:
    """يضيف الخانات الناقصة فقط، ولا يمسّ ما عدّله المدير."""
    have = {(r.level, r.capability) for r in db.scalars(select(LevelCapability))}
    for lv, caps in DEFAULT_MATRIX.items():
        for c, v in caps.items():
            if (lv, c) not in have:
                db.add(LevelCapability(level=lv, capability=c, allowed=v))
    db.commit()


def _matrix(db: Session) -> dict[str, dict[str, bool]]:
    stored: dict[str, dict[str, bool]] = {}
    for r in db.scalars(select(LevelCapability)):
        stored.setdefault(r.level, {})[r.capability] = r.allowed
    return {lv: {c: (c in caps_for(lv, "updater", stored.get(lv, {}))) for c, _ in CAPABILITIES} for lv, *_ in LEVELS}


@router.get("")
def get_levels(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    out = {"levels": [{"key": k, "label": l, "rank": r, "access": a, "default_visibility": v,
                       "locked": k in LOCKED_LEVELS} for k, l, r, a, v in LEVELS],
           "capabilities": [{"key": k, "label": l} for k, l in CAPABILITIES],
           "visibilities": [{"key": k, "label": l} for k, l in VISIBILITIES.items()],
           "task_kinds": [{"key": k, "label": l} for k, l in TASK_KINDS.items()]}
    if user.role == Role.admin:
        out["matrix"] = _matrix(db)
        out["can_edit_matrix"] = user.level == "general_manager"
    return out


class MatrixIn(BaseModel):
    matrix: dict


@router.put("/matrix")
def put_matrix(body: MatrixIn, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    if admin.level != "general_manager":
        raise HTTPException(403, "تعديل مصفوفة الصلاحيات للمدير العام فقط")
    try:
        clean = validate_matrix(body.matrix)
    except ValueError as e:
        raise HTTPException(422, str(e))
    rows = {(r.level, r.capability): r for r in db.scalars(select(LevelCapability))}
    for lv, caps in clean.items():
        for c, v in caps.items():
            if (lv, c) in rows:
                rows[(lv, c)].allowed = v
            else:
                db.add(LevelCapability(level=lv, capability=c, allowed=v))
    audit(db, admin, "update", "level_matrix", None, ",".join(sorted(clean)))
    db.commit()
    return {"matrix": _matrix(db)}
