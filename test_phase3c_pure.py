"""اختبارات نقية للمرحلة 3ج: لا تحتاج FastAPI ولا قاعدة بيانات."""
import base64

from app.authoring import norm_digits, validate_schema
from app.media_logic import clean_color, human_size, new_storage_name, safe_original_name, size_limit, sniff
from app.report_print import render_report_html

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGP4z8DwHwAFAAH/q842iQAAAABJRU5ErkJggg==")


def test_sniff_types_from_content_not_extension():
    assert sniff(PNG[:32]) == ("image", "image/png", ".png")
    assert sniff(b"\xff\xd8\xff\xe0" + b"0" * 28)[1] == "image/jpeg"
    assert sniff(b"GIF89a" + b"0" * 26)[2] == ".gif"
    assert sniff(b"RIFF\x00\x00\x00\x00WEBPVP8 " + b"0" * 12)[1] == "image/webp"
    assert sniff(b"\x00\x00\x00\x18ftypmp42" + b"0" * 20) == ("video", "video/mp4", ".mp4")
    assert sniff(b"\x00\x00\x00\x14ftypqt  " + b"0" * 20)[1] == "video/quicktime"
    assert sniff(b"\x1a\x45\xdf\xa3" + b"0" * 28)[1] == "video/webm"


def test_sniff_rejects_dangerous_or_unsupported():
    assert sniff(b"<html><script>alert(1)</script>") is None
    assert sniff(b"<svg xmlns='http://www.w3.org/2000/svg'/>") is None      # SVG قد يحمل سكربت
    assert sniff(b"MZ\x90\x00" + b"0" * 28) is None
    assert sniff(b"%PDF-1.7" + b"0" * 24) is None
    assert sniff(b"\x00\x00\x00\x18ftypheic" + b"0" * 20) is None
    assert sniff(b"") is None


def test_limits_names_and_colors():
    assert size_limit("image", 15, 150) == 15 * 1024 * 1024 and size_limit("video", 15, 150) == 150 * 1024 * 1024
    assert safe_original_name("../../etc/passwd") == "passwd"
    assert safe_original_name("C:\\x\\صورة المطبخ.jpg") == "صورة المطبخ.jpg"
    assert safe_original_name("a\x00b\n.png") == "ab.png"
    assert len(safe_original_name("x" * 500)) == 200 and safe_original_name(None) == ""
    n = new_storage_name(".png")
    assert n.endswith(".png") and len(n) == 36 and n != new_storage_name(".png")
    assert clean_color("#0F766E") == "#0F766E" and clean_color("red") == "#0f766e" and clean_color(None, "#111111") == "#111111"
    assert human_size(500) == "500 B" and human_size(2048) == "2 KB" and human_size(5 * 1024 * 1024) == "5.0 MB"


def test_arabic_digits_for_serial_search():
    assert norm_digits("RPT-٢٠٢٦-٠٠٠٠١٢") == "RPT-2026-000012"
    assert norm_digits("") == "" and norm_digits(None) == ""


SCHEMA = validate_schema({"fields": [
    {"key": "findings", "label": "الملاحظات", "type": "textarea"},
    {"key": "done", "label": "تم الإصلاح", "type": "checkbox"},
    {"key": "items", "label": "البنود", "type": "table", "columns": [
        {"key": "unit", "label": "الوحدة", "type": "text"}, {"key": "cost", "label": "التكلفة", "type": "number"}]},
]})
REP = {"serial": "RPT-2026-000007", "title": "تسريب <b>مياه</b> في 204", "category": "تقرير المختصين", "status": "submitted",
       "priority": "high", "site": "المهرجان", "department": "الصيانة", "author": "أحمد", "report_date": "2026-10-23",
       "time": "09:41", "template_version": 2, "schema": SCHEMA,
       "body": {"findings": "ماسورة <script>x</script> متضررة", "done": True,
                "items": [{"unit": "204", "cost": 350}, {"unit": "205", "cost": 120.5}]}}
CO = {"name": "شركة أسوار بلس العقارية", "subtitle": "للشقق المخدومة"}


def test_print_html_has_all_standard_sections():
    html = render_report_html(REP, [], CO, "2026-10-23 10:00")
    for needle in ("شركة أسوار بلس العقارية", "RPT-2026-000007", "تفاصيل التقرير", "ملاحظات الإدارة", "التوقيع «تم الاطلاع»",
                   "اسم المطّلع", "عالية", "مرسل", "v2", "الوحدة", "350", "120.5", "نعم", 'dir="rtl"'):
        assert needle in html, needle
    assert "المرفقات" not in html and "مسودة غير معتمدة" not in html


def test_print_html_escapes_user_content():
    html = render_report_html(REP, [], CO, "x")
    assert "<script>x</script>" not in html and "&lt;script&gt;" in html and "&lt;b&gt;مياه" in html


def test_draft_banner_and_media_section():
    d = dict(REP, serial=None, status="draft")
    media = [{"kind": "image", "category": "الصيانة", "color": "#dc2626", "caption": "قبل الإصلاح",
              "original_name": "a.png", "data_uri": "data:image/png;base64,AAAA", "size": "1 KB"},
             {"kind": "video", "category": "النجارة", "caption": "شرح العطل", "original_name": "v.mp4", "size": "20.0 MB"}]
    html = render_report_html(d, media, CO, "x", font_base="file:///srv/fonts/")
    assert "مسودة غير معتمدة" in html and "DRAFT" in html
    assert "المرفقات (2)" in html and 'src="data:image/png;base64,AAAA"' in html and "قبل الإصلاح" in html
    assert "شرح العطل" in html and "20.0 MB" in html and "file:///srv/fonts/Tajawal-Regular.woff2" in html
