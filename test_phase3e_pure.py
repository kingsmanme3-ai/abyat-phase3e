"""اختبارات نقية للمرحلة 3هـ (المستويات والمصفوفة): لا تحتاج FastAPI ولا قاعدة بيانات."""
import re
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from app import levels as L

WEB = Path(__file__).resolve().parents[2] / "web"
APP = (WEB / "app.js").read_text(encoding="utf-8")
HTML = (WEB / "index.html").read_text(encoding="utf-8")


def test_six_levels_in_approved_order():
    assert [l[1] for l in L.LEVELS] == ["عامل", "فني صيانة", "موظف استقبال", "مشرف موقع", "مدير إدارة", "مدير عام"]
    assert [l[2] for l in L.LEVELS] == [1, 2, 3, 4, 5, 6]


def test_worker_and_technician_default_to_assigned_only():
    assert L.default_visibility("worker") == "assigned" == L.default_visibility("technician")
    assert L.default_visibility("supervisor") == "sites" and L.default_visibility("general_manager") == "all"


def test_visibility_override_and_admin_always_all():
    assert L.visibility_of("all", "worker", "updater") == "all"          # تعديل المدير لكل مستخدم
    assert L.visibility_of(None, "worker", "updater") == "assigned"
    assert L.visibility_of("assigned", "general_manager", "admin") == "all"
    assert L.visibility_of("bogus", "worker", "updater") == "assigned"


def test_legacy_roles_map_to_levels():
    assert L.level_of(None, "admin") == "general_manager"
    assert L.level_of(None, "updater") == "supervisor"
    assert L.level_of(None, "viewer") == "receptionist"
    assert L.level_of("worker", "updater") == "worker"


def test_access_class_follows_level():
    assert [L.access_for(k) for k in L.LEVEL_KEYS] == ["updater"] * 4 + ["admin"] * 2


def test_default_matrix_worker_has_no_dashboard_but_can_update():
    m = L.DEFAULT_MATRIX
    assert not m["worker"]["view_dashboard"] and m["worker"]["update_units"] and not m["worker"]["assign_tasks"]
    assert m["supervisor"]["assign_tasks"] and all(m["general_manager"].values())


def test_caps_for_locked_and_stored():
    assert L.caps_for("dept_manager", "admin", {"update_units": False}) == set(L.CAP_KEYS)
    assert "view_dashboard" in L.caps_for("worker", "updater", {"view_dashboard": True})
    assert "update_units" not in L.caps_for("worker", "updater", {"update_units": False})
    assert "update_units" in L.caps_for("worker", "updater", {})


def test_validate_matrix():
    assert L.validate_matrix({"worker": {"view_dashboard": True}}) == {"worker": {"view_dashboard": True}}
    for bad in ({}, {"x": {}}, {"worker": {"nope": True}}, {"worker": {"update_units": 1}},
                {"dept_manager": {"update_units": False}}, {"general_manager": {"assign_tasks": False}}):
        with pytest.raises(ValueError):
            L.validate_matrix(bad)


def test_can_manage_rank_rules():
    assert L.can_manage("general_manager", "general_manager")
    assert L.can_manage("dept_manager", "supervisor") and not L.can_manage("dept_manager", "general_manager")
    assert not L.can_manage("dept_manager", "dept_manager")


def test_select_perm_rows_prefers_level_rows():
    r = lambda role=None, uid=None: NS(role=role, user_id=uid)      # noqa: E731
    rows = [r("updater"), r("worker"), r(uid=7), r("viewer")]
    got = L.select_perm_rows(rows, "worker", "updater", 7)
    assert [(x.role, x.user_id) for x in got] == [(None, 7), ("worker", None)]
    got = L.select_perm_rows(rows, "supervisor", "updater", 9)      # لا صف للمستوى فيُستعمل صف فئة الدور
    assert [x.role for x in got] == ["updater"]


def test_task_overdue():
    from datetime import date
    assert L.task_overdue(date(2026, 10, 1), date(2026, 10, 25), "open")
    assert not L.task_overdue(date(2026, 10, 1), date(2026, 10, 25), "done")
    assert not L.task_overdue(None, date(2026, 10, 25), "open")


def test_upgrade_script_is_idempotent_and_backfills():
    src = (Path(__file__).resolve().parents[1] / "app" / "upgrade.py").read_text(encoding="utf-8")
    assert "if name not in have" in src and "ADD COLUMN" in src
    for lvl, role in (("general_manager", "admin"), ("supervisor", "updater"), ("receptionist", "viewer")):
        assert f"job_level='{lvl}' WHERE job_level IS NULL AND role='{role}'" in src


def test_frontend_wiring():
    assert 'id="uLevel"' in HTML and 'id="lvMatrix"' in HTML and 'id="taskDialog"' in HTML and 'data-view="tasks"' in HTML
    assert "uRole" not in HTML and "uRole" not in APP
    assert "/levels/matrix" in APP and "hasCap('view_dashboard')" in APP and "assignedOnly()" in APP
    assert "v=0.3.4" in HTML


def test_status_colors_unchanged():
    block = re.search(r"const COLORS = \{(.*?)\};", APP, re.S).group(1)
    assert len(re.findall(r"#[0-9a-fA-F]{6}", block)) == 6
