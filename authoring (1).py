"""المرحلة 3ب: بطاقات التقارير والقوالب والصلاحيات والتقارير المكتوبة (مسودة ← مرسل)."""
import json
from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import case, func, or_, select
from sqlalchemy.orm import Session

from ..access import user_caps
from ..audit import audit
from ..levels import select_perm_rows
from ..authoring import (DEFAULT_DEPARTMENTS, DEFAULT_PERMISSIONS, DEFAULT_TEMPLATES, CATEGORIES,
                         PRIORITIES, effective_flags, format_serial, norm_digits, validate_body, validate_schema)
from ..config import settings
from ..database import get_db
from ..deps import get_current_user, require_admin
from ..media_store import print_media, purge_report_media, remove_files
from ..models import (AuthoredReport, CategoryPermission, Department, MediaItem, ReportCategory, ReportCounter,
                      ReportTemplate, Role, Site, User, utcnow)
from ..report_print import pdf_from_html, render_report_html
from ..reports import REPORTS, _local_str
from ..snapshots import today

router = APIRouter(prefix="/api", tags=["authoring"])
Priority = Literal["low", "normal", "high", "urgent"]


# ------------------------------------------------------------------ البذر الأولي
def seed_report_system(db: Session) -> None:
    """يُنفَّذ عند الإقلاع: إن كانت البطاقات فارغة تُنشأ التسع مع القوالب والإدارات والصلاحيات الافتراضية."""
    if db.scalar(select(ReportCategory.id).limit(1)) is not None:
        return
    cats = {}
    for i, (key, title, kind, desc, icon) in enumerate(CATEGORIES, start=1):
        c = ReportCategory(key=key, title=title, kind=kind, description=desc, icon=icon, sort=i)
        db.add(c)
        cats[key] = c
    db.flush()
    for key, (name, fields) in DEFAULT_TEMPLATES.items():
        db.add(ReportTemplate(category_id=cats[key].id, name=name, version=1,
                              schema_json=json.dumps(validate_schema({"fields": fields}), ensure_ascii=False)))
    for key, roles in DEFAULT_PERMISSIONS.items():
        for role, (w, r) in roles.items():
            db.add(CategoryPermission(category_id=cats[key].id, role=role, can_write=w, can_read=r))
    if db.scalar(select(Department.id).limit(1)) is None:
        for n in DEFAULT_DEPARTMENTS:
            db.add(Department(name=n))
    db.commit()


# ------------------------------------------------------------------ مساعدات
def _my_perm_rows(db: Session, user: User) -> list[CategoryPermission]:
    user._caps_cache = user_caps(db, user)      # 3هـ: تستعملها _flags لقيد «كتابة التقارير» من المصفوفة
    return list(db.scalars(select(CategoryPermission).where(
        or_(CategoryPermission.role == user.role.value, CategoryPermission.role == user.level,
            CategoryPermission.user_id == user.id))))


def _flags(user: User, cat: ReportCategory, rows: list[CategoryPermission]) -> dict:
    if cat.kind == "data":
        d = REPORTS.get(cat.key)
        ok = bool(d) and (user.role == Role.admin or (not d.admin_only and user.visibility != "assigned"))
        return {"can_write": False, "can_read": ok, "can_review": False}
    mine = select_perm_rows([r for r in rows if r.category_id == cat.id], user.level, user.role.value, user.id)
    f = effective_flags(user.role == Role.admin, mine)
    caps = getattr(user, "_caps_cache", None)
    if f["can_write"] and user.role != Role.admin and caps is not None and "write_reports" not in caps:
        f = {**f, "can_write": False}
    return f


def _category_or_404(db: Session, cid: int) -> ReportCategory:
    c = db.get(ReportCategory, cid)
    if not c:
        raise HTTPException(404, "البطاقة غير موجودة")
    return c


def _active_template(db: Session, cid: int) -> ReportTemplate | None:
    return db.scalar(select(ReportTemplate).where(ReportTemplate.category_id == cid, ReportTemplate.is_active)
                     .order_by(ReportTemplate.version.desc()).limit(1))


def _next_serial(db: Session) -> str:
    year = today().year
    c = db.scalar(select(ReportCounter).where(ReportCounter.year == year).with_for_update())
    if c is None:
        c = ReportCounter(year=year, last=0)
        db.add(c)
        db.flush()
    c.last += 1
    db.flush()
    return format_serial(year, c.last)


def _check_refs(db: Session, user: User, site_id, department_id) -> None:
    if site_id is not None:
        s = db.get(Site, site_id)
        if not s:
            raise HTTPException(404, "الموقع غير موجود")
        if user.role == Role.updater and site_id not in user.site_ids:
            raise HTTPException(403, "هذا الموقع ليس ضمن مواقعك")
    if department_id is not None and not db.get(Department, department_id):
        raise HTTPException(404, "الإدارة غير موجودة")


def _names(db: Session, rows: list[AuthoredReport]):
    def m(model, ids, col="name"):
        ids = {i for i in ids if i}
        return {o.id: getattr(o, col) for o in db.scalars(select(model).where(model.id.in_(ids)))} if ids else {}
    return (m(User, [r.author_id for r in rows], "full_name"), m(Site, [r.site_id for r in rows]),
            m(Department, [r.department_id for r in rows]), m(ReportCategory, [r.category_id for r in rows], "title"))


def _row(r: AuthoredReport, nm, full: bool = False) -> dict:
    authors, sites, depts, cats = nm
    d = {"id": r.id, "serial": r.serial, "title": r.title, "category_id": r.category_id,
         "category": cats.get(r.category_id), "status": r.status, "priority": r.priority,
         "site_id": r.site_id, "site": sites.get(r.site_id), "department_id": r.department_id,
         "department": depts.get(r.department_id), "author_id": r.author_id, "author": authors.get(r.author_id),
         "report_date": r.report_date.isoformat(), "created_at": r.created_at.isoformat(),
         "submitted_at": r.submitted_at.isoformat() if r.submitted_at else None,
         "time": _local_str(r.submitted_at or r.created_at)[11:16]}
    if full:
        d.update(schema=json.loads(r.schema_json), body=json.loads(r.body_json), template_version=r.template_version)
    return d


def _can_see(db: Session, user: User, r: AuthoredReport) -> bool:
    if user.role == Role.admin or r.author_id == user.id:
        return True
    if r.status == "draft":
        return False
    cat = db.get(ReportCategory, r.category_id)
    return _flags(user, cat, _my_perm_rows(db, user))["can_read"]


def _owned_draft(db: Session, user: User, rid: int) -> AuthoredReport:
    r = db.get(AuthoredReport, rid)
    if not r or not _can_see(db, user, r):
        raise HTTPException(404, "التقرير غير موجود")
    if user.role != Role.admin and r.author_id != user.id:
        raise HTTPException(403, "لا يعدّل التقرير إلا كاتبه أو المدير")
    if r.status != "draft":
        raise HTTPException(409, "التقرير أُرسل ولا يمكن تعديله")
    return r


def _clean_body(schema: dict, body, submit: bool) -> dict:
    try:
        return validate_body(schema, body, submit)
    except ValueError as e:
        raise HTTPException(422, str(e))


# ------------------------------------------------------------------ البطاقات
@router.get("/report-categories")
def list_categories(include_inactive: bool = False, user: User = Depends(get_current_user),
                    db: Session = Depends(get_db)):
    """البطاقات مع صلاحيات المستخدم وعدّادات سريعة. غير المدير لا يرى إلا ما يملك قراءته أو كتابته."""
    admin = user.role == Role.admin
    stmt = select(ReportCategory).order_by(ReportCategory.sort, ReportCategory.id)
    if not (admin and include_inactive):
        stmt = stmt.where(ReportCategory.is_active)
    rows = _my_perm_rows(db, user)
    drafts = dict(db.execute(select(AuthoredReport.category_id, func.count()).where(
        AuthoredReport.author_id == user.id, AuthoredReport.status == "draft").group_by(AuthoredReport.category_id)).all())
    sent = dict(db.execute(select(AuthoredReport.category_id, func.count()).where(
        AuthoredReport.status != "draft").group_by(AuthoredReport.category_id)).all())
    out = []
    for c in db.scalars(stmt):
        f = _flags(user, c, rows)
        if not admin and not (f["can_read"] or f["can_write"]):
            continue
        out.append({"id": c.id, "key": c.key, "title": c.title, "kind": c.kind, "description": c.description,
                    "icon": c.icon, "sort": c.sort, "is_active": c.is_active, **f,
                    "my_drafts": drafts.get(c.id, 0),
                    "submitted": sent.get(c.id, 0) if (f["can_read"] and c.kind == "authored") else 0})
    return out


class CategoryIn(BaseModel):
    key: str = Field(pattern=r"^[a-z][a-z0-9_]{1,39}$")
    title: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=300)
    icon: str | None = Field(default=None, max_length=8)
    sort: int = Field(default=100, ge=0, le=10000)


class CategoryPatch(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=300)
    icon: str | None = Field(default=None, max_length=8)
    sort: int | None = Field(default=None, ge=0, le=10000)
    is_active: bool | None = None


@router.post("/report-categories", status_code=201)
def create_category(body: CategoryIn, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    if db.scalar(select(ReportCategory.id).where(ReportCategory.key == body.key)):
        raise HTTPException(409, "مفتاح البطاقة مستخدم")
    c = ReportCategory(key=body.key, title=body.title.strip(), kind="authored", description=body.description,
                       icon=body.icon, sort=body.sort)
    db.add(c)
    db.flush()
    audit(db, admin, "create", "report_category", c.id, c.key)
    db.commit()
    return {"id": c.id, "key": c.key}


@router.patch("/report-categories/{cid}")
def patch_category(cid: int, body: CategoryPatch, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    c = _category_or_404(db, cid)
    for k, v in body.model_dump(exclude_unset=True).items():
        if v is not None:
            setattr(c, k, v)
    audit(db, admin, "update", "report_category", c.id, c.key)
    db.commit()
    return {"ok": True}


# ------------------------------------------------------------------ القوالب
@router.get("/report-categories/{cid}/template")
def get_template(cid: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    c = _category_or_404(db, cid)
    f = _flags(user, c, _my_perm_rows(db, user))
    if c.kind != "authored" or not (f["can_write"] or user.role == Role.admin):
        raise HTTPException(403, "لا تملك صلاحية الكتابة في هذه البطاقة")
    t = _active_template(db, cid)
    if not t:
        raise HTTPException(404, "لا يوجد قالب لهذه البطاقة بعد")
    return {"id": t.id, "name": t.name, "version": t.version, "schema": json.loads(t.schema_json)}


class TemplateIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    schema_: dict = Field(alias="schema")

    model_config = {"populate_by_name": True}


@router.put("/report-categories/{cid}/template")
def put_template(cid: int, body: TemplateIn, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    """ينشئ إصداراً جديداً من القالب؛ التقارير المكتوبة سابقاً تبقى على نسختها."""
    c = _category_or_404(db, cid)
    if c.kind != "authored":
        raise HTTPException(400, "بطاقات البيانات التلقائية لا تحتاج قالباً")
    try:
        schema = validate_schema(body.schema_)
    except ValueError as e:
        raise HTTPException(422, str(e))
    last = db.scalar(select(func.max(ReportTemplate.version)).where(ReportTemplate.category_id == cid)) or 0
    for t in db.scalars(select(ReportTemplate).where(ReportTemplate.category_id == cid, ReportTemplate.is_active)):
        t.is_active = False
    t = ReportTemplate(category_id=cid, name=body.name.strip(), version=last + 1, created_by=admin.id,
                       schema_json=json.dumps(schema, ensure_ascii=False))
    db.add(t)
    db.flush()
    audit(db, admin, "create", "report_template", t.id, f"{c.key} v{t.version}")
    db.commit()
    return {"id": t.id, "version": t.version}


# ------------------------------------------------------------------ الصلاحيات
class PermItem(BaseModel):
    role: Literal["updater", "viewer"] | None = None
    user_id: int | None = None
    can_write: bool = False
    can_read: bool = False
    can_review: bool = False


class PermsIn(BaseModel):
    items: list[PermItem] = Field(max_length=200)


@router.get("/report-categories/{cid}/permissions")
def get_permissions(cid: int, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    _category_or_404(db, cid)
    rows = db.scalars(select(CategoryPermission).where(CategoryPermission.category_id == cid))
    return {"items": [{"role": r.role, "user_id": r.user_id, "can_write": r.can_write,
                       "can_read": r.can_read, "can_review": r.can_review} for r in rows]}


@router.put("/report-categories/{cid}/permissions")
def put_permissions(cid: int, body: PermsIn, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    c = _category_or_404(db, cid)
    if c.kind != "authored":
        raise HTTPException(400, "صلاحيات تقارير البيانات تتبع دور المستخدم")
    seen = set()
    for it in body.items:
        if (it.role is None) == (it.user_id is None):
            raise HTTPException(422, "كل صف يخص دوراً أو مستخدماً واحداً")
        key = ("r", it.role) if it.role else ("u", it.user_id)
        if key in seen:
            raise HTTPException(422, "صف صلاحيات مكرر")
        seen.add(key)
        if it.user_id is not None and not db.get(User, it.user_id):
            raise HTTPException(404, f"المستخدم {it.user_id} غير موجود")
    for r in db.scalars(select(CategoryPermission).where(CategoryPermission.category_id == cid)):
        db.delete(r)
    db.flush()
    for it in body.items:
        db.add(CategoryPermission(category_id=cid, role=it.role, user_id=it.user_id, can_write=it.can_write,
                                  can_read=it.can_read, can_review=it.can_review))
    audit(db, admin, "update", "category_permissions", cid, c.key)
    db.commit()
    return {"ok": True}


# ------------------------------------------------------------------ الإدارات
class DeptIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    parent_id: int | None = None


@router.get("/departments")
def list_departments(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return [{"id": d.id, "name": d.name, "parent_id": d.parent_id}
            for d in db.scalars(select(Department).where(Department.is_active).order_by(Department.name))]


@router.post("/departments", status_code=201)
def create_department(body: DeptIn, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    name = body.name.strip()
    if db.scalar(select(Department.id).where(Department.name == name)):
        raise HTTPException(409, "الاسم مستخدم")
    if body.parent_id is not None and not db.get(Department, body.parent_id):
        raise HTTPException(404, "القطاع الأب غير موجود")
    d = Department(name=name, parent_id=body.parent_id)
    db.add(d)
    db.flush()
    audit(db, admin, "create", "department", d.id, name)
    db.commit()
    return {"id": d.id, "name": d.name}


@router.patch("/departments/{did}")
def patch_department(did: int, body: dict, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    d = db.get(Department, did)
    if not d:
        raise HTTPException(404, "الإدارة غير موجودة")
    if isinstance(body.get("name"), str) and body["name"].strip():
        d.name = body["name"].strip()[:120]
    if isinstance(body.get("is_active"), bool):
        d.is_active = body["is_active"]
    audit(db, admin, "update", "department", d.id, d.name)
    db.commit()
    return {"ok": True}


# ------------------------------------------------------------------ التقارير المكتوبة
def _nonblank(v: str) -> str:
    v = v.strip()
    if not v:
        raise ValueError("العنوان مطلوب")
    return v


class ReportIn(BaseModel):
    category_id: int
    title: str = Field(min_length=1, max_length=200)
    priority: Priority = "normal"
    site_id: int | None = None
    department_id: int | None = None
    report_date: date | None = None
    body: dict = {}
    submit: bool = False

    _t = field_validator("title")(_nonblank)


class ReportPatch(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    priority: Priority | None = None
    site_id: int | None = None
    department_id: int | None = None
    report_date: date | None = None
    body: dict | None = None


def _submit(db: Session, r: AuthoredReport, user: User) -> None:
    r.body_json = json.dumps(_clean_body(json.loads(r.schema_json), json.loads(r.body_json), True), ensure_ascii=False)
    r.serial = _next_serial(db)
    r.status = "submitted"
    r.submitted_at = utcnow()
    audit(db, user, "submit", "authored_report", r.id, r.serial)


@router.post("/authored-reports", status_code=201)
def create_report(body: ReportIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    c = _category_or_404(db, body.category_id)
    if c.kind != "authored" or not c.is_active:
        raise HTTPException(400, "هذه البطاقة لا تقبل تقارير مكتوبة")
    if not _flags(user, c, _my_perm_rows(db, user))["can_write"]:
        raise HTTPException(403, "لا تملك صلاحية الكتابة في هذه البطاقة")
    t = _active_template(db, c.id)
    if not t:
        raise HTTPException(409, "لا يوجد قالب لهذه البطاقة؛ اطلب من المدير تعريفه")
    _check_refs(db, user, body.site_id, body.department_id)
    schema = json.loads(t.schema_json)
    r = AuthoredReport(category_id=c.id, template_id=t.id, template_version=t.version, schema_json=t.schema_json,
                       title=body.title, priority=body.priority, site_id=body.site_id,
                       department_id=body.department_id, author_id=user.id,
                       report_date=body.report_date or today(),
                       body_json=json.dumps(_clean_body(schema, body.body, body.submit), ensure_ascii=False))
    db.add(r)
    db.flush()
    audit(db, user, "create", "authored_report", r.id, c.key)
    if body.submit:
        _submit(db, r, user)
    db.commit()
    db.refresh(r)
    return _row(r, _names(db, [r]), full=True)


def _visible_clause(db: Session, user: User):
    """شرط رؤية التقارير: المدير يرى الكل، والآخر يرى تقاريره + المرسل من البطاقات التي يقرؤها."""
    if user.role == Role.admin:
        return None
    rows = _my_perm_rows(db, user)
    readable = [c.id for c in db.scalars(select(ReportCategory).where(ReportCategory.kind == "authored"))
                if _flags(user, c, rows)["can_read"]]
    return or_(AuthoredReport.author_id == user.id,
               (AuthoredReport.status != "draft") & AuthoredReport.category_id.in_(readable or [0]))


_PRIO_ORDER = case({"low": 0, "normal": 1, "high": 2, "urgent": 3}, value=AuthoredReport.priority, else_=1)
SORT_COLUMNS = {
    "serial": AuthoredReport.serial, "title": AuthoredReport.title, "report_date": AuthoredReport.report_date,
    "created_at": AuthoredReport.created_at, "status": AuthoredReport.status, "priority": _PRIO_ORDER,
    "author": User.full_name, "site": Site.name, "department": Department.name, "category": ReportCategory.title,
}
SortKey = Literal["serial", "title", "report_date", "created_at", "status", "priority", "author", "site",
                  "department", "category"]


def _media_counts(db: Session, ids: list[int]) -> dict:
    if not ids:
        return {}
    return dict(db.execute(select(MediaItem.report_id, func.count()).where(MediaItem.report_id.in_(ids))
                           .group_by(MediaItem.report_id)).all())


@router.get("/authored-reports")
def list_reports(category_id: int | None = None, status: Literal["draft", "submitted", "reviewed"] | None = None,
                 mine: bool = False, q: str | None = Query(default=None, max_length=100),
                 serial: str | None = Query(default=None, max_length=30),
                 date_from: date | None = None, date_to: date | None = None, author_id: int | None = None,
                 site_id: int | None = None, department_id: int | None = None,
                 priority: Priority | None = None, sort: SortKey = "created_at",
                 order: Literal["asc", "desc"] = "desc",
                 limit: int = Query(default=100, ge=1, le=500), offset: int = Query(default=0, ge=0),
                 user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """سجل التقارير (3ج): بحث نصي في العنوان والمتن، فلترة، وفرز بأي عمود."""
    stmt = (select(AuthoredReport)
            .outerjoin(User, User.id == AuthoredReport.author_id)
            .outerjoin(Site, Site.id == AuthoredReport.site_id)
            .outerjoin(Department, Department.id == AuthoredReport.department_id)
            .outerjoin(ReportCategory, ReportCategory.id == AuthoredReport.category_id))
    if mine:
        stmt = stmt.where(AuthoredReport.author_id == user.id)
    else:
        clause = _visible_clause(db, user)
        if clause is not None:
            stmt = stmt.where(clause)
    if category_id is not None:
        stmt = stmt.where(AuthoredReport.category_id == category_id)
    if status:
        stmt = stmt.where(AuthoredReport.status == status)
    if author_id is not None:
        stmt = stmt.where(AuthoredReport.author_id == author_id)
    if site_id is not None:
        stmt = stmt.where(AuthoredReport.site_id == site_id)
    if department_id is not None:
        stmt = stmt.where(AuthoredReport.department_id == department_id)
    if priority:
        stmt = stmt.where(AuthoredReport.priority == priority)
    if date_from:
        stmt = stmt.where(AuthoredReport.report_date >= date_from)
    if date_to:
        stmt = stmt.where(AuthoredReport.report_date <= date_to)
    if serial and serial.strip():
        stmt = stmt.where(AuthoredReport.serial.contains(norm_digits(serial.strip()).upper(), autoescape=True))
    if q and q.strip():
        t = norm_digits(q.strip()).lower()
        stmt = stmt.where(or_(func.lower(AuthoredReport.title).contains(t, autoescape=True),
                              func.lower(AuthoredReport.body_json).contains(t, autoescape=True),
                              func.lower(AuthoredReport.serial).contains(t, autoescape=True)))
    total = db.scalar(select(func.count()).select_from(stmt.order_by(None).subquery()))
    col = SORT_COLUMNS[sort]
    primary = col.asc() if order == "asc" else col.desc()
    items = list(db.scalars(stmt.order_by(primary, AuthoredReport.id.desc()).limit(limit).offset(offset)))
    nm, mc = _names(db, items), _media_counts(db, [r.id for r in items])
    return {"total": total, "items": [{**_row(r, nm), "media_count": mc.get(r.id, 0)} for r in items]}


@router.get("/authored-reports/facets")
def report_facets(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """قيم فلاتر السجل: كتّاب التقارير التي يراها المستخدم (لقائمة «الكاتب»)."""
    stmt = select(User.id, User.full_name).join(AuthoredReport, AuthoredReport.author_id == User.id)
    clause = _visible_clause(db, user)
    if clause is not None:
        stmt = stmt.where(clause)
    rows = db.execute(stmt.distinct().order_by(User.full_name)).all()
    return {"authors": [{"id": i, "name": n} for i, n in rows]}


@router.get("/authored-reports/{rid}")
def get_report(rid: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    r = db.get(AuthoredReport, rid)
    if not r or not _can_see(db, user, r):
        raise HTTPException(404, "التقرير غير موجود")
    return {**_row(r, _names(db, [r]), full=True), "media_count": _media_counts(db, [r.id]).get(r.id, 0)}


def _render(db: Session, user: User, rid: int, font_base: str) -> str:
    r = db.get(AuthoredReport, rid)
    if not r or not _can_see(db, user, r):
        raise HTTPException(404, "التقرير غير موجود")
    rep = _row(r, _names(db, [r]), full=True)
    now = _local_str(utcnow())
    return render_report_html(rep, print_media(db, r.id),
                              {"name": settings.company_name, "subtitle": settings.company_subtitle}, now, font_base)


@router.get("/authored-reports/{rid}/print", response_class=HTMLResponse)
def print_report(rid: int, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """صفحة الطباعة القياسية (تُفتح في المتصفح للطباعة أو الحفظ كـ PDF)."""
    return HTMLResponse(_render(db, user, rid, str(request.base_url).rstrip("/") + "/fonts/"))


@router.get("/authored-reports/{rid}/pdf")
def pdf_report(rid: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """ملف PDF قياسي للتقرير. إن تعذر التوليد على الخادم يرجع 501 فتفتح الواجهة صفحة الطباعة بدلاً منه."""
    import pathlib
    fonts = pathlib.Path(settings.fonts_dir)
    html = _render(db, user, rid, fonts.as_uri() + "/" if fonts.is_dir() else "/fonts/")
    try:
        pdf = pdf_from_html(html, base_url=fonts.as_uri() + "/" if fonts.is_dir() else None)
    except RuntimeError:
        raise HTTPException(501, "توليد PDF غير متاح على الخادم، استخدم الطباعة ثم «حفظ كـ PDF»")
    rep = db.get(AuthoredReport, rid)
    name = (rep.serial or f"draft-{rep.id}") + ".pdf"
    return Response(pdf, media_type="application/pdf", headers={"Content-Disposition": f'inline; filename="{name}"'})


@router.patch("/authored-reports/{rid}")
def patch_report(rid: int, body: ReportPatch, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    r = _owned_draft(db, user, rid)
    data = body.model_dump(exclude_unset=True)
    if "site_id" in data or "department_id" in data:
        _check_refs(db, user, data.get("site_id"), data.get("department_id"))
    for k in ("title", "priority", "report_date"):
        if data.get(k) is not None:
            setattr(r, k, data[k])
    for k in ("site_id", "department_id"):
        if k in data:
            setattr(r, k, data[k])
    if body.body is not None:
        r.body_json = json.dumps(_clean_body(json.loads(r.schema_json), body.body, False), ensure_ascii=False)
    audit(db, user, "update", "authored_report", r.id)
    db.commit()
    db.refresh(r)
    return _row(r, _names(db, [r]), full=True)


@router.post("/authored-reports/{rid}/submit")
def submit_report(rid: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    r = _owned_draft(db, user, rid)
    _submit(db, r, user)
    db.commit()
    db.refresh(r)
    return _row(r, _names(db, [r]), full=True)


@router.delete("/authored-reports/{rid}", status_code=204)
def delete_report(rid: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    r = _owned_draft(db, user, rid)
    audit(db, user, "delete", "authored_report", r.id, r.title)
    files = purge_report_media(db, r.id)
    db.delete(r)
    db.commit()
    remove_files(files)
