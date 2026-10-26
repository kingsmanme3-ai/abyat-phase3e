"""منطق نظام كتابة التقارير (3ب): دوال نقية بلا اعتماد على FastAPI أو قاعدة البيانات، فتُختبر بسرعة."""
import re
from datetime import date

FIELD_TYPES = ("text", "textarea", "number", "date", "select", "checkbox", "table")
COLUMN_TYPES = ("text", "number", "date")
PRIORITIES = ("low", "normal", "high", "urgent")
PRIORITY_LABELS = {"low": "منخفضة", "normal": "عادية", "high": "عالية", "urgent": "عاجلة"}
STATUS_LABELS = {"draft": "مسودة", "submitted": "مرسل", "reviewed": "مُطّلع عليه"}
KEY_RE = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
MAX_FIELDS, MAX_OPTIONS, MAX_COLUMNS, MAX_ROWS = 60, 50, 8, 50
_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹٫٬", "01234567890123456789.,")


def norm_digits(s: str) -> str:
    """يحوّل الأرقام العربية والفارسية إلى لاتينية (للبحث بالرقم المسلسل)."""
    return (s or "").translate(_DIGITS)


def format_serial(year: int, n: int) -> str:
    return f"RPT-{year}-{n:06d}"


def _s(v) -> str:
    return v.strip() if isinstance(v, str) else ""


# ---------------------------------------------------------------- القوالب
def validate_schema(schema) -> dict:
    """يتحقق من تعريف الحقول ويعيده منظّفاً. يرفع ValueError برسالة عربية."""
    if not isinstance(schema, dict) or not isinstance(schema.get("fields"), list):
        raise ValueError("تعريف القالب يجب أن يحتوي قائمة fields")
    fields = schema["fields"]
    if not 1 <= len(fields) <= MAX_FIELDS:
        raise ValueError(f"عدد الحقول من 1 إلى {MAX_FIELDS}")
    out, seen = [], set()
    for f in fields:
        if not isinstance(f, dict):
            raise ValueError("كل حقل يجب أن يكون كائناً")
        key, label, ftype = _s(f.get("key")), _s(f.get("label")), _s(f.get("type"))
        if not KEY_RE.match(key):
            raise ValueError(f"مفتاح الحقل «{key}» غير صالح (حروف إنجليزية صغيرة وأرقام وشرطة سفلية، يبدأ بحرف)")
        if key in seen:
            raise ValueError(f"المفتاح «{key}» مكرر")
        seen.add(key)
        if not 1 <= len(label) <= 120:
            raise ValueError(f"عنوان الحقل «{key}» مطلوب (حتى 120 حرفاً)")
        if ftype not in FIELD_TYPES:
            raise ValueError(f"نوع الحقل «{key}» غير مدعوم")
        item = {"key": key, "label": label, "type": ftype, "required": bool(f.get("required", False))}
        if _s(f.get("help")):
            item["help"] = _s(f["help"])[:200]
        if ftype == "select":
            opts = f.get("options")
            if not isinstance(opts, list):
                raise ValueError(f"حقل القائمة «{label}» يحتاج options")
            clean = []
            for o in opts:
                o = _s(o)
                if o and o not in clean:
                    clean.append(o[:100])
            if not 1 <= len(clean) <= MAX_OPTIONS:
                raise ValueError(f"خيارات «{label}» من 1 إلى {MAX_OPTIONS}")
            item["options"] = clean
        if ftype == "table":
            cols = f.get("columns")
            if not isinstance(cols, list) or not 1 <= len(cols) <= MAX_COLUMNS:
                raise ValueError(f"أعمدة جدول «{label}» من 1 إلى {MAX_COLUMNS}")
            ccl, cseen = [], set()
            for c in cols:
                ck, cl, ct = _s(c.get("key")) if isinstance(c, dict) else "", _s(c.get("label")) if isinstance(c, dict) else "", \
                    _s(c.get("type") or "text") if isinstance(c, dict) else ""
                if not KEY_RE.match(ck) or ck in cseen or not cl or ct not in COLUMN_TYPES:
                    raise ValueError(f"تعريف أعمدة «{label}» غير صالح")
                cseen.add(ck)
                ccl.append({"key": ck, "label": cl[:80], "type": ct})
            item["columns"] = ccl
        out.append(item)
    return {"fields": out}


# ---------------------------------------------------------------- القيم
def _num(v):
    if isinstance(v, bool):
        raise ValueError
    if isinstance(v, (int, float)):
        return v
    t = _s(v).translate(_DIGITS).replace(",", "")
    n = float(t)                       # ValueError إن لم يكن رقماً
    if n != n or n in (float("inf"), float("-inf")):
        raise ValueError
    return int(n) if n.is_integer() else n


def _date(v) -> str:
    return date.fromisoformat(_s(v)).isoformat()


def _coerce(ftype: str, v, f: dict):
    """يحوّل قيمة واحدة. يرجع None للقيمة الفارغة، ويرفع ValueError للقيمة غير الصالحة."""
    if v is None or (isinstance(v, str) and not v.strip()):
        return None
    if ftype == "text":
        return str(v).strip()[:300]
    if ftype == "textarea":
        return str(v).strip()[:10000]
    if ftype == "number":
        return _num(v)
    if ftype == "date":
        return _date(v)
    if ftype == "select":
        if _s(v) not in f["options"]:
            raise ValueError
        return _s(v)
    if ftype == "checkbox":
        if not isinstance(v, bool):
            raise ValueError
        return v
    raise ValueError


def validate_body(schema: dict, body, submit: bool) -> dict:
    """يتحقق من قيم التقرير على نسخة القالب. المسودة تتسامح مع الحقول الناقصة، والإرسال يشترط المطلوب."""
    if body is None:
        body = {}
    if not isinstance(body, dict):
        raise ValueError("متن التقرير غير صالح")
    clean, errors = {}, []
    for f in schema["fields"]:
        key, label, ftype = f["key"], f["label"], f["type"]
        raw = body.get(key)
        try:
            if ftype == "table":
                rows = raw if isinstance(raw, list) else ([] if raw in (None, "") else None)
                if rows is None or len(rows) > MAX_ROWS:
                    raise ValueError
                good = []
                for r in rows:
                    if not isinstance(r, dict):
                        raise ValueError
                    row = {}
                    for c in f["columns"]:
                        cv = _coerce(c["type"], r.get(c["key"]), c)
                        if cv is not None:
                            row[c["key"]] = cv
                    if row:
                        good.append(row)
                val = good or None
            else:
                val = _coerce(ftype, raw, f)
        except (ValueError, TypeError, AttributeError):
            errors.append(f"قيمة «{label}» غير صالحة")
            continue
        if val is not None:
            clean[key] = val
        if submit and f["required"] and (val is None or (ftype == "checkbox" and val is False)):
            errors.append(f"الحقل «{label}» مطلوب")
    if errors:
        raise ValueError("؛ ".join(errors))
    return clean


# ---------------------------------------------------------------- الصلاحيات
def effective_flags(is_admin: bool, rows) -> dict:
    """rows: صفوف صلاحيات تخص المستخدم (بدوره أو باسمه). الكتابة تتضمن القراءة."""
    if is_admin:
        return {"can_write": True, "can_read": True, "can_review": True}
    w = any(r.can_write for r in rows)
    return {"can_write": w, "can_read": w or any(r.can_read for r in rows),
            "can_review": any(r.can_review for r in rows)}


# ---------------------------------------------------------------- البيانات الافتراضية
CATEGORIES = [
    ("specialists", "تقرير المختصين", "authored", "تقارير فنية وتوصيات يكتبها المختصون", "🛠️"),
    ("customer_service", "نموذج خدمة العملاء", "authored", "استفسارات وشكاوى وطلبات العملاء", "🎧"),
    ("reception", "موظفو الاستقبال", "authored", "تقرير الوردية والتسليم والاستلام", "🛎️"),
    ("finance", "التقارير المالية", "authored", "ملخصات مالية وبنود الدخل والمصروف", "💰"),
    ("hr", "التقارير الإدارية (HR)", "authored", "شؤون الموظفين والحضور والإجازات", "👥"),
    ("units", "تقرير الوحدات", "data", "بيانات تلقائية: حالة كل وحدة", "🏢"),
    ("properties", "تقرير العقارات", "data", "بيانات تلقائية: الإشغال لكل عمارة", "🏘️"),
    ("contracts", "تقرير العقود", "data", "بيانات تلقائية: العقود وتواريخ انتهائها", "📄"),
    ("users", "تقرير المستخدمين", "data", "بيانات تلقائية للمدير: المستخدمون وصلاحياتهم", "🔐"),
]


def _f(key, label, type_="text", required=False, **kw):
    return {"key": key, "label": label, "type": type_, "required": required, **kw}


DEFAULT_TEMPLATES = {
    "specialists": ("نموذج تقرير المختص", [
        _f("kind", "نوع التقرير", "select", True, options=["فحص دوري", "ملاحظة فنية", "توصية", "أخرى"]),
        _f("findings", "الملاحظات والنتائج", "textarea", True),
        _f("recommendations", "التوصيات", "textarea"),
        _f("action_required", "يتطلب إجراءً", "checkbox"),
        _f("follow_up", "تاريخ المتابعة", "date"),
    ]),
    "customer_service": ("نموذج خدمة العملاء", [
        _f("customer", "اسم العميل", "text", True),
        _f("phone", "رقم الجوال"),
        _f("unit_no", "رقم الوحدة"),
        _f("request_type", "نوع الطلب", "select", True, options=["استفسار", "شكوى", "طلب خدمة", "اقتراح", "إلغاء"]),
        _f("details", "التفاصيل", "textarea", True),
        _f("action_taken", "الإجراء المتخذ", "textarea"),
        _f("resolved", "تمت المعالجة", "checkbox"),
    ]),
    "reception": ("تقرير وردية الاستقبال", [
        _f("shift", "الوردية", "select", True, options=["صباحية", "مسائية", "ليلية"]),
        _f("arrivals", "عدد الوصول", "number"),
        _f("departures", "عدد المغادرة", "number"),
        _f("handover", "ملاحظات التسليم", "textarea", True),
        _f("issues", "المشكلات والإجراءات", "table", columns=[
            {"key": "unit", "label": "الوحدة", "type": "text"},
            {"key": "issue", "label": "المشكلة", "type": "text"},
            {"key": "action", "label": "الإجراء", "type": "text"}]),
    ]),
    "finance": ("نموذج التقرير المالي", [
        _f("period_from", "من تاريخ", "date", True),
        _f("period_to", "إلى تاريخ", "date"),
        _f("income", "إجمالي الدخل", "number"),
        _f("expenses", "إجمالي المصروفات", "number"),
        _f("notes", "ملاحظات", "textarea"),
        _f("items", "البنود", "table", columns=[
            {"key": "desc", "label": "البيان", "type": "text"},
            {"key": "amount", "label": "المبلغ", "type": "number"},
            {"key": "on", "label": "التاريخ", "type": "date"}]),
    ]),
    "hr": ("نموذج التقرير الإداري", [
        _f("topic", "الموضوع", "select", True,
           options=["حضور وانصراف", "إجازات", "توظيف", "أداء", "مخالفات", "أخرى"]),
        _f("employee", "اسم الموظف"),
        _f("details", "التفاصيل", "textarea", True),
        _f("decision", "القرار / التوصية", "textarea"),
    ]),
}

DEFAULT_DEPARTMENTS = ["العمليات", "الاستقبال", "خدمة العملاء", "المالية", "الموارد البشرية", "الدعم الفني", "الصيانة"]

# الصلاحيات الافتراضية: أقل امتياز. المالية والموارد البشرية للمدير حتى يمنح غيره من لوحة الصلاحيات.
DEFAULT_PERMISSIONS = {
    "specialists": {"updater": (True, True), "viewer": (False, True)},
    "customer_service": {"updater": (True, True), "viewer": (False, True)},
    "reception": {"updater": (True, True), "viewer": (False, True)},
}
