"""اختبارات نقية لمنطق 3ب (تعمل بلا FastAPI أو قاعدة بيانات)."""
from types import SimpleNamespace as NS

from app import authoring as a

SCHEMA = a.validate_schema({"fields": [
    {"key": "kind", "label": "النوع", "type": "select", "required": True, "options": ["أ", "ب"]},
    {"key": "n", "label": "العدد", "type": "number"},
    {"key": "d", "label": "التاريخ", "type": "date"},
    {"key": "ok", "label": "تم", "type": "checkbox", "required": True},
    {"key": "t", "label": "جدول", "type": "table", "columns": [
        {"key": "x", "label": "س", "type": "text"}, {"key": "amt", "label": "مبلغ", "type": "number"}]},
]})


def fails(fn, *args, contains=""):
    try:
        fn(*args)
    except ValueError as e:
        assert contains in str(e), str(e)
        return
    raise AssertionError("كان يجب أن يفشل")


def test_default_templates_valid():
    for _, (_, fields) in a.DEFAULT_TEMPLATES.items():
        assert a.validate_schema({"fields": fields})["fields"]
    keys = [c[0] for c in a.CATEGORIES]
    assert len(keys) == 9 and set(a.DEFAULT_TEMPLATES) <= set(keys)


def test_schema_rejections():
    fails(a.validate_schema, {"fields": []})
    fails(a.validate_schema, {"fields": [{"key": "Bad Key", "label": "x", "type": "text"}]}, contains="مفتاح")
    fails(a.validate_schema, {"fields": [{"key": "a", "label": "x", "type": "text"},
                                         {"key": "a", "label": "y", "type": "text"}]}, contains="مكرر")
    fails(a.validate_schema, {"fields": [{"key": "a", "label": "x", "type": "select"}]}, contains="options")
    fails(a.validate_schema, {"fields": [{"key": "a", "label": "x", "type": "table", "columns": []}]})
    fails(a.validate_schema, {"fields": [{"key": "a", "label": "x", "type": "zzz"}]}, contains="غير مدعوم")


def test_draft_is_lenient_submit_is_strict():
    assert a.validate_body(SCHEMA, {}, submit=False) == {}
    fails(a.validate_body, SCHEMA, {}, True, contains="مطلوب")
    ok = a.validate_body(SCHEMA, {"kind": "أ", "ok": True}, True)
    assert ok == {"kind": "أ", "ok": True}
    fails(a.validate_body, SCHEMA, {"kind": "أ", "ok": False}, True, contains="«تم»")


def test_coercion_and_cleaning():
    b = a.validate_body(SCHEMA, {"kind": "ب", "n": "١٢٫٥", "d": "2026-10-23", "ok": True, "ghost": 1,
                                 "t": [{"x": " بند ", "amt": "1,500"}, {"x": "", "amt": ""}]}, True)
    assert b["n"] == 12.5 and b["d"] == "2026-10-23" and "ghost" not in b
    assert b["t"] == [{"x": "بند", "amt": 1500}]          # الصف الفارغ حُذف
    fails(a.validate_body, SCHEMA, {"n": "abc"}, False, contains="«العدد»")
    fails(a.validate_body, SCHEMA, {"d": "23/10/2026"}, False, contains="«التاريخ»")
    fails(a.validate_body, SCHEMA, {"kind": "ج"}, False, contains="«النوع»")
    fails(a.validate_body, SCHEMA, {"t": "x"}, False)
    fails(a.validate_body, SCHEMA, {"t": [{"amt": "x"}]}, False)
    fails(a.validate_body, SCHEMA, {"n": True}, False)


def test_serial_and_permissions():
    assert a.format_serial(2026, 123) == "RPT-2026-000123"
    assert a.effective_flags(True, []) == {"can_write": True, "can_read": True, "can_review": True}
    none = a.effective_flags(False, [])
    assert not any(none.values())
    w = a.effective_flags(False, [NS(can_write=True, can_read=False, can_review=False)])
    assert w["can_write"] and w["can_read"] and not w["can_review"]      # الكتابة تتضمن القراءة
    r = a.effective_flags(False, [NS(can_write=False, can_read=True, can_review=False),
                                  NS(can_write=False, can_read=False, can_review=True)])
    assert r == {"can_write": False, "can_read": True, "can_review": True}


if __name__ == "__main__":
    for n, f in list(globals().items()):
        if n.startswith("test_"):
            f(); print("ok", n)
