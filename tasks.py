"""المرحلة 3هـ: المهام المسندة على الوحدات (العامل والفني يريان مهامهم فقط، والمدير يعدّل نطاق كل مستخدم)."""
from datetime import date
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ..access import user_caps
from ..audit import audit
from ..database import get_db
from ..deps import get_current_user
from ..levels import TASK_KINDS, level_label, task_overdue
from ..models import Building, Role, Site, Unit, UnitTask, User, utcnow
from ..realtime import changed_event, manager
from ..snapshots import today

router = APIRouter(prefix="/api/tasks", tags=["tasks"])
Kind = Literal["cleaning", "maintenance", "inspection", "other"]


class TaskIn(BaseModel):
    unit_id: int
    assignee_id: int
    kind: Kind = "other"
    title: str | None = Field(default=None, max_length=200)
    note: str | None = Field(default=None, max_length=1000)
    due_date: date | None = None


def _can_assign(db: Session, user: User) -> bool:
    return "assign_tasks" in user_caps(db, user)


def _task_or_404(db: Session, tid: int) -> UnitTask:
    t = db.get(UnitTask, tid)
    if not t:
        raise HTTPException(404, "المهمة غير موجودة")
    return t


def _row(t: UnitTask, unit: Unit, bld: Building, site: Site, names: dict[int, str]) -> dict:
    return {"id": t.id, "unit_id": unit.id, "unit_number": unit.number, "building": bld.name,
            "site_id": site.id, "site": site.name, "unit_status": unit.status.value,
            "kind": t.kind, "kind_label": TASK_KINDS.get(t.kind, t.kind), "title": t.title, "note": t.note,
            "status": t.status, "due_date": t.due_date.isoformat() if t.due_date else None,
            "overdue": task_overdue(t.due_date, today(), t.status),
            "assignee_id": t.assignee_id, "assignee": names.get(t.assignee_id),
            "created_by": names.get(t.created_by), "created_at": t.created_at.isoformat(),
            "done_at": t.done_at.isoformat() if t.done_at else None}


@router.get("")
def list_tasks(status: Literal["open", "done", "all"] = "open", mine: bool = False,
               site_id: int | None = None, limit: int = Query(default=300, ge=1, le=1000),
               user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    stmt = (select(UnitTask, Unit, Building, Site)
            .join(Unit, Unit.id == UnitTask.unit_id).join(Building, Building.id == Unit.building_id)
            .join(Site, Site.id == Building.site_id))
    scope = user.visibility
    if mine or scope == "assigned":
        stmt = stmt.where(UnitTask.assignee_id == user.id)
    elif scope == "sites" and user.role != Role.admin:
        stmt = stmt.where(or_(UnitTask.assignee_id == user.id, Site.id.in_(user.site_ids or [0])))
    if status != "all":
        stmt = stmt.where(UnitTask.status == status)
    if site_id is not None:
        stmt = stmt.where(Site.id == site_id)
    stmt = stmt.order_by(UnitTask.status.desc(), UnitTask.due_date.is_(None), UnitTask.due_date, UnitTask.id.desc()).limit(limit)
    found = db.execute(stmt).all()
    ids = {i for t, *_ in found for i in (t.assignee_id, t.created_by) if i}
    names = {u.id: u.full_name for u in db.scalars(select(User).where(User.id.in_(ids)))} if ids else {}
    return {"can_assign": _can_assign(db, user), "items": [_row(t, u, b, s, names) for t, u, b, s in found]}


@router.get("/assignees")
def assignees(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if not _can_assign(db, user):
        raise HTTPException(403, "لا تملك صلاحية إسناد المهام")
    out = []
    for u in db.scalars(select(User).where(User.is_active.is_(True)).order_by(User.full_name)):
        if u.role == Role.viewer:
            continue
        if user.role != Role.admin and not (u.site_ids and set(u.site_ids) & set(user.site_ids)) and u.visibility != "assigned":
            continue
        out.append({"id": u.id, "name": u.full_name, "level": u.level, "level_label": level_label(u.level)})
    return out


@router.post("", status_code=201)
def create_task(body: TaskIn, background: BackgroundTasks, user: User = Depends(get_current_user),
                db: Session = Depends(get_db)):
    if not _can_assign(db, user):
        raise HTTPException(403, "لا تملك صلاحية إسناد المهام")
    unit = db.scalar(select(Unit).where(Unit.id == body.unit_id))
    if not unit:
        raise HTTPException(404, "الوحدة غير موجودة")
    bld = db.get(Building, unit.building_id)
    if user.role != Role.admin and bld.site_id not in user.site_ids:
        raise HTTPException(403, "هذه الوحدة ليست في مواقعك")
    who = db.get(User, body.assignee_id)
    if not who or not who.is_active or who.role == Role.viewer:
        raise HTTPException(404, "المستخدم المسند إليه غير موجود أو لا يستقبل مهاماً")
    t = UnitTask(unit_id=unit.id, assignee_id=who.id, kind=body.kind,
                 title=(body.title or "").strip() or TASK_KINDS[body.kind], note=body.note,
                 due_date=body.due_date, created_by=user.id)
    db.add(t)
    db.flush()
    audit(db, user, "create", "task", t.id, f"unit {unit.id} -> user {who.id}")
    db.commit()
    background.add_task(manager.broadcast, changed_event(bld.site_id, unit.id))
    return {"id": t.id}


def _may_close(db: Session, user: User, t: UnitTask) -> bool:
    return user.role == Role.admin or t.assignee_id == user.id or t.created_by == user.id


def _set_status(tid: int, new: str, background: BackgroundTasks, user: User, db: Session) -> dict:
    t = _task_or_404(db, tid)
    if not _may_close(db, user, t):
        raise HTTPException(403, "ليست مهمتك")
    t.status = new
    t.done_at = utcnow() if new == "done" else None
    unit = db.get(Unit, t.unit_id)
    audit(db, user, new, "task", t.id)
    db.commit()
    background.add_task(manager.broadcast, changed_event(db.get(Building, unit.building_id).site_id, unit.id))
    return {"ok": True, "status": t.status}


@router.post("/{tid}/done")
def done(tid: int, background: BackgroundTasks, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return _set_status(tid, "done", background, user, db)


@router.post("/{tid}/reopen")
def reopen(tid: int, background: BackgroundTasks, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    t = _task_or_404(db, tid)
    if user.role != Role.admin and t.created_by != user.id:
        raise HTTPException(403, "إعادة فتح المهمة لمن أسندها أو للمدير")
    return _set_status(tid, "open", background, user, db)


@router.delete("/{tid}", status_code=204)
def delete_task(tid: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    t = _task_or_404(db, tid)
    if user.role != Role.admin and t.created_by != user.id:
        raise HTTPException(403, "الحذف لمن أسند المهمة أو للمدير")
    audit(db, user, "delete", "task", t.id)
    db.delete(t)
    db.commit()
