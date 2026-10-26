"""المرحلة 3ج: مكتبة الصور والفيديوهات مربوطة بالتقارير، بتصنيفات قابلة للإضافة والتعديل والحذف."""
import json
import os
import shutil
import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..access import ensure_cap
from ..audit import audit
from ..config import settings
from ..database import get_db
from ..deps import get_current_user, require_admin
from ..media_logic import (DEFAULT_LIBRARY_CARDS, DEFAULT_MEDIA_CATEGORIES, clean_color, new_storage_name,
                           safe_original_name, size_limit, sniff)
from ..media_store import media_path, remove_files
from ..models import (AppSetting, AuthoredReport, MediaCategory, MediaItem, ReportCategory, Role, Site, User)
from ..security import create_media_token, decode_media_token
from .authoring import _can_see, _visible_clause

router = APIRouter(prefix="/api", tags=["media"])
CARDS_KEY, SEEDED_KEY = "library_cards", "media_seeded"


# ------------------------------------------------------------------ البذر والإعدادات
def _get_setting(db: Session, key: str) -> str | None:
    s = db.get(AppSetting, key)
    return s.value if s else None


def _set_setting(db: Session, key: str, value: str) -> None:
    s = db.get(AppSetting, key)
    if s:
        s.value = value
    else:
        db.add(AppSetting(key=key, value=value))


def seed_media(db: Session) -> None:
    """مرة واحدة فقط: التصنيفات الثلاثة (الصيانة، النظافة، النجارة) والبطاقات التي يظهر فيها تبويب المكتبة.
    بعد البذر يملك المدير الحذف والتعديل دون أن تعود التصنيفات عند إعادة التشغيل."""
    if _get_setting(db, SEEDED_KEY):
        return
    if db.scalar(select(MediaCategory.id).limit(1)) is None:
        for i, (name, color) in enumerate(DEFAULT_MEDIA_CATEGORIES, start=1):
            db.add(MediaCategory(name=name, color=color, sort=i))
    keys = {k for (k,) in db.execute(select(ReportCategory.key).where(ReportCategory.kind == "authored"))}
    _set_setting(db, CARDS_KEY, json.dumps([k for k in DEFAULT_LIBRARY_CARDS if k in keys]))
    _set_setting(db, SEEDED_KEY, "1")
    db.commit()


@router.get("/media-settings")
def get_media_settings(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    try:
        cards = json.loads(_get_setting(db, CARDS_KEY) or "[]")
    except ValueError:
        cards = []
    return {"cards": cards}


class MediaSettingsIn(BaseModel):
    cards: list[str] = Field(max_length=20)


@router.put("/media-settings")
def put_media_settings(body: MediaSettingsIn, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    valid = {k for (k,) in db.execute(select(ReportCategory.key).where(ReportCategory.kind == "authored"))}
    bad = [k for k in body.cards if k not in valid]
    if bad:
        raise HTTPException(422, "بطاقة غير معروفة: " + bad[0])
    _set_setting(db, CARDS_KEY, json.dumps(list(dict.fromkeys(body.cards))))
    audit(db, admin, "update", "media_settings", None, ",".join(body.cards))
    db.commit()
    return {"ok": True}


# ------------------------------------------------------------------ التصنيفات
class CatIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    color: str | None = None
    sort: int = Field(default=100, ge=0, le=10000)


class CatPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    color: str | None = None
    sort: int | None = Field(default=None, ge=0, le=10000)


def _cat_or_404(db: Session, cid: int) -> MediaCategory:
    c = db.get(MediaCategory, cid)
    if not c:
        raise HTTPException(404, "التصنيف غير موجود")
    return c


@router.get("/media-categories")
def list_media_categories(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    counts = dict(db.execute(select(MediaItem.category_id, func.count()).group_by(MediaItem.category_id)).all())
    return [{"id": c.id, "name": c.name, "color": c.color, "sort": c.sort, "count": counts.get(c.id, 0)}
            for c in db.scalars(select(MediaCategory).order_by(MediaCategory.sort, MediaCategory.id))]


@router.post("/media-categories", status_code=201)
def create_media_category(body: CatIn, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    name = body.name.strip()
    if not name:
        raise HTTPException(422, "اسم التصنيف مطلوب")
    if db.scalar(select(MediaCategory.id).where(MediaCategory.name == name)):
        raise HTTPException(409, "اسم التصنيف مستخدم")
    c = MediaCategory(name=name, color=clean_color(body.color), sort=body.sort)
    db.add(c)
    db.flush()
    audit(db, admin, "create", "media_category", c.id, name)
    db.commit()
    return {"id": c.id, "name": c.name}


@router.patch("/media-categories/{cid}")
def patch_media_category(cid: int, body: CatPatch, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    c = _cat_or_404(db, cid)
    if body.name is not None:
        name = body.name.strip()
        if not name:
            raise HTTPException(422, "اسم التصنيف مطلوب")
        if db.scalar(select(MediaCategory.id).where(MediaCategory.name == name, MediaCategory.id != cid)):
            raise HTTPException(409, "اسم التصنيف مستخدم")
        c.name = name
    if body.color is not None:
        c.color = clean_color(body.color, c.color)
    if body.sort is not None:
        c.sort = body.sort
    audit(db, admin, "update", "media_category", c.id, c.name)
    db.commit()
    return {"ok": True}


@router.delete("/media-categories/{cid}", status_code=204)
def delete_media_category(cid: int, move_to: int | None = None, admin: User = Depends(require_admin),
                          db: Session = Depends(get_db)):
    """يحذف التصنيف. إن كان فيه ملفات يلزم تحديد تصنيف تُنقل إليه (move_to) حتى لا تضيع."""
    c = _cat_or_404(db, cid)
    n = db.scalar(select(func.count()).select_from(MediaItem).where(MediaItem.category_id == cid)) or 0
    if n:
        if move_to is None:
            raise HTTPException(409, f"يوجد {n} ملف في هذا التصنيف؛ اختر تصنيفاً تُنقل إليه الملفات قبل الحذف")
        if move_to == cid or not db.get(MediaCategory, move_to):
            raise HTTPException(422, "تصنيف النقل غير صالح")
        for m in db.scalars(select(MediaItem).where(MediaItem.category_id == cid)):
            m.category_id = move_to
        db.flush()
    audit(db, admin, "delete", "media_category", c.id, f"{c.name} ({n} ملف)")
    db.delete(c)
    db.commit()


# ------------------------------------------------------------------ مساعدات الوسائط
def _media_or_404(db: Session, mid: int) -> MediaItem:
    m = db.get(MediaItem, mid)
    if not m:
        raise HTTPException(404, "الملف غير موجود")
    return m


def _media_visible(db: Session, user: User, m: MediaItem) -> bool:
    if user.role == Role.admin or m.report_id is None:
        return True
    r = db.get(AuthoredReport, m.report_id)
    return bool(r and _can_see(db, user, r))


def _check_report_link(db: Session, user: User, report_id: int) -> AuthoredReport:
    r = db.get(AuthoredReport, report_id)
    if not r or not _can_see(db, user, r):
        raise HTTPException(404, "التقرير غير موجود")
    if user.role != Role.admin and (r.author_id != user.id or r.status != "draft"):
        raise HTTPException(403, "لا يُرفق بتقرير إلا كاتبه وهو مسودة، أو المدير")
    return r


def _check_site(db: Session, user: User, site_id: int) -> None:
    if not db.get(Site, site_id):
        raise HTTPException(404, "الموقع غير موجود")
    if user.role == Role.updater and site_id not in user.site_ids:
        raise HTTPException(403, "هذا الموقع ليس ضمن مواقعك")


def _can_modify(db: Session, user: User, m: MediaItem) -> bool:
    if user.role == Role.admin:
        return True
    if m.uploaded_by != user.id or user.role != Role.updater:
        return False
    if m.report_id is None:
        return True
    r = db.get(AuthoredReport, m.report_id)
    return bool(r and r.status == "draft")


def _serialize(db: Session, user: User, items: list[MediaItem]) -> list[dict]:
    def names(model, ids, col="name"):
        ids = {i for i in ids if i}
        return {o.id: o for o in db.scalars(select(model).where(model.id.in_(ids)))} if ids else {}
    cats = names(MediaCategory, [m.category_id for m in items])
    users = names(User, [m.uploaded_by for m in items])
    sites = names(Site, [m.site_id for m in items])
    reps = names(AuthoredReport, [m.report_id for m in items])
    out = []
    for m in items:
        c, u, s, r = cats.get(m.category_id), users.get(m.uploaded_by), sites.get(m.site_id), reps.get(m.report_id)
        out.append({"id": m.id, "kind": m.kind, "mime": m.mime, "size": m.size_bytes, "caption": m.caption,
                    "original_name": m.original_name, "category_id": m.category_id,
                    "category": c.name if c else None, "color": c.color if c else "#64748b",
                    "report_id": m.report_id, "report_serial": r.serial if r else None,
                    "report_title": r.title if r else None, "site_id": m.site_id, "site": s.name if s else None,
                    "uploaded_by": m.uploaded_by, "uploader": u.full_name if u else None,
                    "created_at": m.created_at.isoformat(), "can_edit": _can_modify(db, user, m),
                    "url": f"/api/media/{m.id}/file?t={create_media_token(m.id, user.id)}"})
    return out


# ------------------------------------------------------------------ الوسائط
@router.get("/media")
def list_media(category_id: int | None = None, kind: str | None = Query(default=None, pattern="^(image|video)$"),
               report_id: int | None = None, site_id: int | None = None, mine: bool = False,
               q: str | None = Query(default=None, max_length=100),
               limit: int = Query(default=60, ge=1, le=200), offset: int = Query(default=0, ge=0),
               user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    stmt = select(MediaItem)
    clause = _visible_clause(db, user)
    if clause is not None:
        stmt = stmt.where(or_(MediaItem.report_id.is_(None),
                              MediaItem.report_id.in_(select(AuthoredReport.id).where(clause))))
    if category_id is not None:
        stmt = stmt.where(MediaItem.category_id == category_id)
    if kind:
        stmt = stmt.where(MediaItem.kind == kind)
    if report_id is not None:
        stmt = stmt.where(MediaItem.report_id == report_id)
    if site_id is not None:
        stmt = stmt.where(MediaItem.site_id == site_id)
    if mine:
        stmt = stmt.where(MediaItem.uploaded_by == user.id)
    if q and q.strip():
        t = q.strip().lower()
        stmt = stmt.where(or_(func.lower(MediaItem.caption).contains(t, autoescape=True),
                              func.lower(MediaItem.original_name).contains(t, autoescape=True)))
    total = db.scalar(select(func.count()).select_from(stmt.order_by(None).subquery()))
    items = list(db.scalars(stmt.order_by(MediaItem.created_at.desc(), MediaItem.id.desc()).limit(limit).offset(offset)))
    return {"total": total, "items": _serialize(db, user, items)}


@router.post("/media", status_code=201)
def upload_media(file: UploadFile = File(...), category_id: int = Form(...), caption: str | None = Form(None),
                 report_id: int | None = Form(None), site_id: int | None = Form(None),
                 user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if user.role == Role.viewer:
        raise HTTPException(403, "المشاهد لا يرفع ملفات")
    ensure_cap(db, user, "upload_media", "مستواك الوظيفي لا يسمح برفع الملفات")
    if not db.get(MediaCategory, category_id):
        raise HTTPException(404, "التصنيف غير موجود")
    if report_id is not None:
        _check_report_link(db, user, report_id)
    if site_id is not None:
        _check_site(db, user, site_id)
    src = file.file
    head = src.read(32)
    found = sniff(head)
    if not found:
        raise HTTPException(415, "نوع الملف غير مدعوم. المسموح: صور JPG وPNG وWEBP وGIF، وفيديو MP4 وWEBM وMOV")
    kind, mime, ext = found
    src.seek(0, os.SEEK_END)
    size = src.tell()
    src.seek(0)
    limit = size_limit(kind, settings.max_image_mb, settings.max_video_mb)
    if size > limit:
        raise HTTPException(413, f"حجم الملف يتجاوز الحد ({limit // 1024 // 1024} MB لـ{'الصور' if kind == 'image' else 'الفيديو'})")
    os.makedirs(settings.media_dir, exist_ok=True)
    name = new_storage_name(ext)
    tmp = media_path(f".tmp-{uuid.uuid4().hex}")
    try:
        with open(tmp, "wb") as out:
            shutil.copyfileobj(src, out, 1024 * 1024)
        os.replace(tmp, media_path(name))
        m = MediaItem(category_id=category_id, report_id=report_id, site_id=site_id, kind=kind, mime=mime,
                      file_name=name, original_name=safe_original_name(file.filename), size_bytes=size,
                      caption=(caption or "").strip()[:300] or None, uploaded_by=user.id)
        db.add(m)
        db.flush()
        audit(db, user, "create", "media", m.id, f"{kind} {size}B")
        db.commit()
    except Exception:
        remove_files([name, os.path.basename(tmp)])
        raise
    return _serialize(db, user, [m])[0]


class MediaPatch(BaseModel):
    caption: str | None = Field(default=None, max_length=300)
    category_id: int | None = None
    report_id: int | None = None
    site_id: int | None = None


@router.patch("/media/{mid}")
def patch_media(mid: int, body: MediaPatch, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    m = _media_or_404(db, mid)
    if not _media_visible(db, user, m):
        raise HTTPException(404, "الملف غير موجود")
    if not _can_modify(db, user, m):
        raise HTTPException(403, "لا تملك صلاحية تعديل هذا الملف")
    data = body.model_fields_set
    if "category_id" in data:
        if body.category_id is None or not db.get(MediaCategory, body.category_id):
            raise HTTPException(404, "التصنيف غير موجود")
        m.category_id = body.category_id
    if "report_id" in data:
        if body.report_id is not None and body.report_id != m.report_id:
            _check_report_link(db, user, body.report_id)
        m.report_id = body.report_id
    if "site_id" in data:
        if body.site_id is not None:
            _check_site(db, user, body.site_id)
        m.site_id = body.site_id
    if "caption" in data:
        m.caption = (body.caption or "").strip()[:300] or None
    audit(db, user, "update", "media", m.id)
    db.commit()
    return _serialize(db, user, [m])[0]


@router.delete("/media/{mid}", status_code=204)
def delete_media(mid: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    m = _media_or_404(db, mid)
    if not _media_visible(db, user, m):
        raise HTTPException(404, "الملف غير موجود")
    if not _can_modify(db, user, m):
        raise HTTPException(403, "لا تملك صلاحية حذف هذا الملف")
    name = m.file_name
    audit(db, user, "delete", "media", m.id, m.original_name)
    db.delete(m)
    db.commit()
    remove_files([name])


@router.get("/media/{mid}/file")
def media_file(mid: int, t: str = "", download: bool = False, db: Session = Depends(get_db)):
    """يخدم الملف برابط موقّع قصير العمر (تحمله وسوم img وvideo). تُعاد قراءة صلاحية المستخدم عند كل طلب."""
    tok = decode_media_token(t)
    if not tok or tok[0] != mid:
        raise HTTPException(401, "الرابط غير صالح أو منتهٍ")
    user = db.get(User, tok[1])
    m = db.get(MediaItem, mid)
    if not user or not user.is_active or not m or not _media_visible(db, user, m):
        raise HTTPException(404, "الملف غير موجود")
    path = media_path(m.file_name)
    if not os.path.isfile(path):
        raise HTTPException(404, "الملف مفقود من التخزين")
    headers = {"Cache-Control": "private, max-age=3600", "X-Content-Type-Options": "nosniff"}
    if download:
        return FileResponse(path, media_type=m.mime, filename=m.original_name or m.file_name, headers=headers)
    return FileResponse(path, media_type=m.mime, headers=headers)
