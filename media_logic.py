"""منطق مكتبة الوسائط (3ج): دوال نقية بلا اعتماد على FastAPI أو قاعدة البيانات، فتُختبر بسرعة."""
import re
import uuid

DEFAULT_MEDIA_CATEGORIES = [("الصيانة", "#dc2626"), ("النظافة", "#0891b2"), ("النجارة", "#b45309")]
DEFAULT_LIBRARY_CARDS = ["specialists", "reception"]     # البطاقات التي يظهر فيها تبويب المكتبة افتراضياً
COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


def sniff(head: bytes):
    """يتعرف على نوع الملف من بدايته الفعلية (لا من الامتداد ولا من ما يدّعيه المتصفح).
    يرجع (kind, mime, ext) أو None إن كان النوع غير مدعوم."""
    if head.startswith(b"\xff\xd8\xff"):
        return "image", "image/jpeg", ".jpg"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image", "image/png", ".png"
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return "image", "image/gif", ".gif"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image", "image/webp", ".webp"
    if head[4:8] == b"ftyp":
        brand = head[8:12]
        if brand == b"qt  ":
            return "video", "video/quicktime", ".mov"
        if brand in (b"heic", b"heix", b"mif1", b"msf1", b"avif"):
            return None                                   # صور HEIC/AVIF غير مدعومة في المتصفحات
        return "video", "video/mp4", ".mp4"
    if head.startswith(b"\x1a\x45\xdf\xa3"):
        return "video", "video/webm", ".webm"
    return None


def size_limit(kind: str, max_image_mb: int, max_video_mb: int) -> int:
    return (max_image_mb if kind == "image" else max_video_mb) * 1024 * 1024


def safe_original_name(name: str | None) -> str:
    """اسم العرض الأصلي بلا مسارات أو محارف تحكم (يُعرض فقط ولا يُستخدم اسماً على القرص)."""
    n = (name or "").replace("\\", "/").split("/")[-1]
    n = re.sub(r"[\x00-\x1f\x7f]", "", n).strip()
    return n[:200]


def new_storage_name(ext: str) -> str:
    return uuid.uuid4().hex + ext


def clean_color(v: str | None, default: str = "#0f766e") -> str:
    return v if v and COLOR_RE.match(v) else default


def human_size(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.0f} KB"
    return f"{n / 1024 / 1024:.1f} MB"
