"""اختبارات نقية للمرحلة 3د (أيقونات الوحدات): لا تحتاج FastAPI ولا قاعدة بيانات."""
import re
from pathlib import Path

from app.models import UnitStatus

WEB = Path(__file__).resolve().parents[2] / "web"
APP = (WEB / "app.js").read_text(encoding="utf-8")
ICONS = (WEB / "icons.js").read_text(encoding="utf-8")
CSS = (WEB / "style.css").read_text(encoding="utf-8")

APPROVED = {
    "rented": "#16a34a", "available": "#2563eb", "temp_reserved": "#f59e0b",
    "development": "#8b5cf6", "cleaning": "#0891b2", "maintenance": "#dc2626",
}


def _colors():
    block = re.search(r"const COLORS = \{(.*?)\};", APP, re.S).group(1)
    return dict(re.findall(r"(\w+):\s*'(#[0-9a-fA-F]{6})'", block))


def test_status_colors_are_the_approved_six():
    assert _colors() == APPROVED


def test_every_status_has_a_color():
    assert {s.value for s in UnitStatus} == set(_colors())


def test_icons_are_single_color_currentcolor():
    assert 'fill="currentColor"' in ICONS
    assert not re.search(r"#[0-9a-fA-F]{3,6}", ICONS), "الأيقونات لا تحمل ألواناً ثابتة"
    assert "house" in ICONS and "building" in ICONS


def test_tile_uses_house_icon_with_dot_number_and_label():
    assert "ICONS.house(" in APP and 'class="dot"' in APP and "ICONS.building(" in APP
    assert re.search(r"\.tile\{[^}]*background:#fff", CSS)      # البطاقة بيضاء والأيقونة هي الملوّنة
    assert "--c" in CSS
