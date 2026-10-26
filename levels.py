"""المرحلة 3هـ: المستويات الوظيفية الستة ومصفوفة الصلاحيات والمهام. دوال نقية بلا FastAPI ولا قاعدة بيانات."""
from datetime import date

# (المفتاح، الاسم، الرتبة، فئة الوصول القديمة، نطاق الرؤية الافتراضي)
LEVELS = [
    ("worker", "عامل", 1, "updater", "assigned"),
    ("technician", "فني صيانة", 2, "updater", "assigned"),
    ("receptionist", "موظف استقبال", 3, "updater", "sites"),
    ("supervisor", "مشرف موقع", 4, "updater", "sites"),
    ("dept_manager", "مدير إدارة", 5, "admin", "all"),
    ("general_manager", "مدير عام", 6, "admin", "all"),
]
LEVEL_KEYS = [l[0] for l in LEVELS]
_BY_KEY = {l[0]: l for l in LEVELS}
LOCKED_LEVELS = ("dept_manager", "general_manager")      # صلاحياتهم كاملة دائماً ولا تُعدَّل
LEGACY_LEVEL = {"admin": "general_manager", "updater": "supervisor", "viewer": "receptionist"}

VISIBILITIES = {
    "assigned": "المهام المسندة إليه فقط",
    "sites": "عرض الكل وتحديث مواقعه",
    "all": "كل المواقع",
}

CAPABILITIES = [
    ("view_dashboard", "لوحة الإشغال والتقارير التلقائية"),
    ("update_units", "تغيير حالة الوحدات"),
    ("write_reports", "كتابة التقارير"),
    ("upload_media", "رفع الصور والفيديو"),
    ("assign_tasks", "إسناد المهام"),
]
CAP_KEYS = [c[0] for c in CAPABILITIES]

_DEFAULT_ON = {
    "view_dashboard": ("receptionist", "supervisor", "dept_manager", "general_manager"),
    "update_units": LEVEL_KEYS,
    "write_reports": LEVEL_KEYS,
    "upload_media": LEVEL_KEYS,
    "assign_tasks": ("supervisor", "dept_manager", "general_manager"),
}
DEFAULT_MATRIX = {lv: {c: lv in _DEFAULT_ON[c] for c in CAP_KEYS} for lv in LEVEL_KEYS}

TASK_KINDS = {"cleaning": "نظافة", "maintenance": "صيانة", "inspection": "معاينة", "other": "أخرى"}
TASK_STATUSES = ("open", "done")


def level_label(key: str) -> str:
    return _BY_KEY[key][1] if key in _BY_KEY else key


def level_rank(key: str) -> int:
    return _BY_KEY[key][2] if key in _BY_KEY else 0


def access_for(key: str) -> str:
    return _BY_KEY[key][3]


def default_visibility(key: str) -> str:
    return _BY_KEY[key][4] if key in _BY_KEY else "sites"


def level_of(job_level: str | None, role: str) -> str:
    """المستوى الفعلي: المخزَّن، أو المكافئ للدور القديم لمن لم تُرقَّ بياناته."""
    return job_level if job_level in _BY_KEY else LEGACY_LEVEL.get(role, "receptionist")


def visibility_of(override: str | None, level: str, role: str) -> str:
    if role == "admin":
        return "all"
    return override if override in VISIBILITIES else default_visibility(level)


def can_manage(actor_level: str, target_level: str) -> bool:
    """المدير العام يدير الجميع، وغيره يدير من رتبته أدنى من رتبته فقط."""
    if actor_level == "general_manager":
        return True
    return level_rank(actor_level) > level_rank(target_level)


def caps_for(level: str, role: str, stored: dict[str, bool]) -> set[str]:
    """stored: صفوف المصفوفة المحفوظة لهذا المستوى. الإداريون (فئة admin) يملكون كل شيء دائماً."""
    if role == "admin" or level in LOCKED_LEVELS:
        return set(CAP_KEYS)
    base = DEFAULT_MATRIX.get(level, {})
    return {c for c in CAP_KEYS if stored.get(c, base.get(c, False))}


def validate_matrix(matrix) -> dict[str, dict[str, bool]]:
    """يتحقق من مصفوفة واردة {مستوى: {صلاحية: bool}}؛ يرفع ValueError برسالة عربية."""
    if not isinstance(matrix, dict) or not matrix:
        raise ValueError("المصفوفة فارغة")
    out: dict[str, dict[str, bool]] = {}
    for lv, caps in matrix.items():
        if lv not in _BY_KEY:
            raise ValueError(f"مستوى غير معروف: {lv}")
        if not isinstance(caps, dict):
            raise ValueError("صيغة المصفوفة غير صالحة")
        row = {}
        for c, v in caps.items():
            if c not in CAP_KEYS:
                raise ValueError(f"صلاحية غير معروفة: {c}")
            if not isinstance(v, bool):
                raise ValueError("القيم يجب أن تكون صح أو خطأ")
            if lv in LOCKED_LEVELS and v is False:
                raise ValueError(f"صلاحيات «{level_label(lv)}» كاملة دائماً ولا تُعدَّل")
            row[c] = v
        out[lv] = row
    return out


def select_perm_rows(rows, level: str, role: str, user_id: int):
    """صفوف صلاحيات بطاقة واحدة: صفوف المستخدم نفسه، ثم صفوف مستواه إن وُجدت، وإلا صفوف فئة دوره القديمة."""
    mine = [r for r in rows if r.user_id == user_id]
    lvl = [r for r in rows if r.user_id is None and r.role == level]
    base = lvl or [r for r in rows if r.user_id is None and r.role == role]
    return mine + base


def task_overdue(due: date | None, today: date, status: str) -> bool:
    return bool(due and status == "open" and due < today)
