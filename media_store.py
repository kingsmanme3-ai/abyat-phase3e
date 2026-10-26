"""تخزين الوسائط على القرص ودوال تعتمد على النماذج فقط (بلا اعتماد على الراوترات، لتفادي الاستيراد الدائري)."""
import base64
import os

from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import settings
from .media_logic import human_size
from .models import MediaCategory, MediaItem

MAX_EMBED_IMAGES = 8
MAX_EMBED_BYTES = 4 * 1024 * 1024


def media_path(file_name: str) -> str:
    return os.path.join(settings.media_dir, file_name)


def remove_files(names: list[str]) -> None:
    for n in names:
        try:
            os.remove(media_path(n))
        except OSError:
            pass


def purge_report_media(db: Session, report_id: int) -> list[str]:
    """يحذف صفوف وسائط تقرير يُحذف (مسودة) ويرجع أسماء ملفاتها؛ يحذفها المستدعي بـ remove_files بعد الـ commit."""
    items = list(db.scalars(select(MediaItem).where(MediaItem.report_id == report_id)))
    names = [m.file_name for m in items]
    for m in items:
        db.delete(m)
    db.flush()
    return names


def print_media(db: Session, report_id: int) -> list[dict]:
    """مرفقات التقرير لقالب PDF: الصور الصغيرة تُضمَّن داخل الملف، وما سواها يُذكر بالاسم."""
    rows = db.execute(select(MediaItem, MediaCategory.name, MediaCategory.color)
                      .join(MediaCategory, MediaCategory.id == MediaItem.category_id)
                      .where(MediaItem.report_id == report_id).order_by(MediaItem.id)).all()
    out, embedded = [], 0
    for m, cname, ccolor in rows:
        d = {"kind": m.kind, "category": cname, "color": ccolor, "caption": m.caption,
             "original_name": m.original_name, "size": human_size(m.size_bytes)}
        if m.kind == "image" and embedded < MAX_EMBED_IMAGES and m.size_bytes <= MAX_EMBED_BYTES:
            try:
                with open(media_path(m.file_name), "rb") as f:
                    d["data_uri"] = f"data:{m.mime};base64," + base64.b64encode(f.read()).decode()
                embedded += 1
            except OSError:
                pass
        out.append(d)
    return out
