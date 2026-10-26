from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session, contains_eager

from ..access import ensure_cap, ensure_unit_visible, has_open_task
from ..audit import audit
from ..database import get_db
from ..deps import ensure_can_update_site, get_current_user, require_admin
from ..models import Building, Role, Unit, UnitStatus, UnitStatusHistory, UnitTask, User
from ..realtime import changed_event, manager
from ..schemas import (HistoryOut, StatusChange, UnitBulk, UnitBulkOut, UnitCreate,
                       UnitOut, UnitUpdate)
from ..snapshots import refresh_snapshot

router = APIRouter(prefix="/api/units", tags=["units"])


def _get_unit(db: Session, unit_id: int) -> Unit:
    unit = db.scalar(
        select(Unit).join(Building).options(contains_eager(Unit.building)).where(Unit.id == unit_id)
    )
    if not unit:
        raise HTTPException(404, "الوحدة غير موجودة")
    return unit


@router.get("", response_model=list[UnitOut])
def list_units(
    site_id: int | None = None,
    building_id: int | None = None,
    status: UnitStatus | None = None,
    q: str | None = Query(default=None, description="بحث برقم الوحدة أو اسم المستأجر"),
    limit: int = Query(default=500, ge=1, le=2000),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    stmt = select(Unit).join(Building).options(contains_eager(Unit.building))
    if user.role != Role.admin and user.visibility == "assigned":     # 3هـ: وحدات مهامه المفتوحة فقط
        stmt = stmt.where(Unit.id.in_(select(UnitTask.unit_id).where(
            UnitTask.assignee_id == user.id, UnitTask.status == "open")))
    if site_id:
        stmt = stmt.where(Building.site_id == site_id)
    if building_id:
        stmt = stmt.where(Unit.building_id == building_id)
    if status:
        stmt = stmt.where(Unit.status == status)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(Unit.number.ilike(like) | Unit.tenant_name.ilike(like))
    stmt = stmt.order_by(Building.site_id, Building.name, Unit.number).limit(limit).offset(offset)
    return db.scalars(stmt).all()


@router.post("", response_model=UnitOut, status_code=201)
def create_unit(body: UnitCreate, background: BackgroundTasks, admin: User = Depends(require_admin),
                db: Session = Depends(get_db)):
    building = db.get(Building, body.building_id)
    if not building:
        raise HTTPException(404, "العمارة غير موجودة")
    if db.scalar(select(Unit.id).where(Unit.building_id == body.building_id, Unit.number == body.number)):
        raise HTTPException(409, "رقم الوحدة موجود في هذه العمارة")
    unit = Unit(**body.model_dump(), updated_by=admin.id)
    db.add(unit)
    db.flush()
    db.add(UnitStatusHistory(unit_id=unit.id, old_status=None, new_status=unit.status,
                             tenant_name=unit.tenant_name, contract_end=unit.contract_end,
                             note="إنشاء الوحدة", changed_by=admin.id))
    audit(db, admin, "create", "unit", unit.id, f"{unit.number} (building {unit.building_id})")
    db.flush()
    refresh_snapshot(db, building.site_id)
    db.commit()
    background.add_task(manager.broadcast, changed_event(building.site_id, unit.id))
    return _get_unit(db, unit.id)


@router.post("/bulk", response_model=UnitBulkOut, status_code=201)
def bulk_create_units(body: UnitBulk, background: BackgroundTasks, admin: User = Depends(require_admin),
                      db: Session = Depends(get_db)):
    """إضافة عدة وحدات دفعة واحدة لعمارة، مع تجاهل الأرقام الموجودة."""
    building = db.get(Building, body.building_id)
    if not building:
        raise HTTPException(404, "العمارة غير موجودة")
    existing = set(db.scalars(select(Unit.number).where(Unit.building_id == body.building_id)))
    seen: set[str] = set()
    created, skipped = [], []
    for raw in body.numbers:
        n = raw.strip()
        if not n or len(n) > 30:
            raise HTTPException(422, f"رقم وحدة غير صالح: {raw!r}")
        if n in existing or n in seen:
            skipped.append(n)
            continue
        seen.add(n)
        created.append(n)
    for n in created:
        unit = Unit(building_id=body.building_id, number=n, status=body.status, updated_by=admin.id)
        db.add(unit)
        db.flush()
        db.add(UnitStatusHistory(unit_id=unit.id, old_status=None, new_status=body.status,
                                 note="إنشاء الوحدة", changed_by=admin.id))
    audit(db, admin, "bulk_create", "unit", None,
          f"building {body.building_id}: {len(created)} created, {len(skipped)} skipped")
    db.flush()
    refresh_snapshot(db, building.site_id)
    db.commit()
    if created:
        background.add_task(manager.broadcast, changed_event(building.site_id))
    return UnitBulkOut(created=len(created), skipped_existing=skipped)


@router.get("/{unit_id}", response_model=UnitOut)
def get_unit(unit_id: int, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    return _get_unit(db, unit_id)


@router.patch("/{unit_id}", response_model=UnitOut)
def update_unit(unit_id: int, body: UnitUpdate, admin: User = Depends(require_admin),
                db: Session = Depends(get_db)):
    unit = _get_unit(db, unit_id)
    data = body.model_dump(exclude_unset=True)
    new_number = data.get("number")
    if new_number and new_number != unit.number and db.scalar(
        select(Unit.id).where(Unit.building_id == unit.building_id, Unit.number == new_number)
    ):
        raise HTTPException(409, "رقم الوحدة موجود في هذه العمارة")
    for k, v in data.items():
        setattr(unit, k, v)
    unit.updated_by = admin.id
    audit(db, admin, "update", "unit", unit.id, ", ".join(sorted(data)))
    db.commit()
    return unit


@router.patch("/{unit_id}/status", response_model=UnitOut)
def change_status(unit_id: int, body: StatusChange, background: BackgroundTasks,
                  user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """تغيير حالة الوحدة (المدير، أو المحدّث المسند له موقع الوحدة).

    - عند الانتقال إلى «متاحة» تُمسح بيانات المستأجر وتاريخ العقد.
    - في بقية الحالات تُحدَّث الحقول المرسلة فقط وتبقى القيم السابقة للحقول غير المرسلة.
    """
    unit = _get_unit(db, unit_id)
    ensure_cap(db, user, "update_units", "مستواك الوظيفي لا يسمح بتغيير حالة الوحدات")
    if user.role != Role.admin and user.visibility == "assigned":
        if not has_open_task(db, user.id, unit.id):        # العامل والفني: وحدات مهامهم فقط
            raise HTTPException(403, "هذه الوحدة ليست ضمن مهامك")
    else:
        ensure_can_update_site(user, unit.building.site_id)

    old_status = unit.status
    sent = body.model_fields_set
    if body.status == UnitStatus.available:
        unit.tenant_name = None
        unit.contract_end = None
    else:
        if "tenant_name" in sent:
            unit.tenant_name = body.tenant_name
        if "contract_end" in sent:
            unit.contract_end = body.contract_end
    unit.status = body.status
    unit.updated_by = user.id

    db.add(UnitStatusHistory(unit_id=unit.id, old_status=old_status, new_status=body.status,
                             tenant_name=unit.tenant_name, contract_end=unit.contract_end,
                             note=body.note, changed_by=user.id))
    audit(db, user, "status", "unit", unit.id, f"{old_status.value} -> {body.status.value}")
    db.flush()
    refresh_snapshot(db, unit.building.site_id)
    db.commit()
    background.add_task(manager.broadcast, changed_event(unit.building.site_id, unit.id))
    return unit


@router.get("/{unit_id}/history", response_model=list[HistoryOut])
def unit_history(unit_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    _get_unit(db, unit_id)
    ensure_unit_visible(db, user, unit_id)
    return db.scalars(
        select(UnitStatusHistory)
        .where(UnitStatusHistory.unit_id == unit_id)
        .order_by(UnitStatusHistory.changed_at.desc(), UnitStatusHistory.id.desc())
    ).all()
