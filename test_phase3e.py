"""اختبارات تكاملية للمرحلة 3هـ (تحتاج بيئة المشروع: pip install -r requirements-dev.txt)."""
from test_phase1 import login


def _mk_user(client, admin, name, level, sites=()):
    r = client.post("/api/users", json={"username": name, "full_name": name, "password": f"{name}-pass-123",
                                        "job_level": level, "site_ids": list(sites)}, headers=admin)
    assert r.status_code == 201, r.text
    return r.json()


def test_levels_tasks_and_matrix(client):
    admin = login(client, "admin", "admin-pass-123")
    me = client.get("/api/auth/me", headers=admin).json()
    assert me["level"] == "general_manager" and "assign_tasks" in me["caps"]

    s = client.post("/api/sites", json={"name": "موقع المستويات"}, headers=admin).json()
    b = client.post("/api/buildings", json={"site_id": s["id"], "name": "ع1"}, headers=admin).json()
    u1 = client.post("/api/units", json={"building_id": b["id"], "number": "501"}, headers=admin).json()
    u2 = client.post("/api/units", json={"building_id": b["id"], "number": "502"}, headers=admin).json()

    w = _mk_user(client, admin, "worker1", "worker")
    sup = _mk_user(client, admin, "sup1", "supervisor", [s["id"]])
    dept = _mk_user(client, admin, "dept1", "dept_manager")
    assert w["role"] == "updater" and w["visibility"] == "assigned" and dept["role"] == "admin"

    hw, hs, hd = (login(client, n, f"{n}-pass-123") for n in ("worker1", "sup1", "dept1"))

    # العامل: لا لوحة، ولا وحدات قبل الإسناد، ولا تغيير حالة
    assert client.get("/api/dashboard", headers=hw).status_code == 403
    assert client.get("/api/units", headers=hw).json() == []
    assert client.patch(f"/api/units/{u1['id']}/status", json={"status": "cleaning"}, headers=hw).status_code == 403

    # المشرف يُسند مهمة على وحدة في موقعه
    t = client.post("/api/tasks", json={"unit_id": u1["id"], "assignee_id": w["id"], "kind": "cleaning"}, headers=hs)
    assert t.status_code == 201, t.text
    assert client.post("/api/tasks", json={"unit_id": u1["id"], "assignee_id": w["id"]}, headers=hw).status_code == 403

    # العامل يرى وحدة مهمته فقط ويغيّر حالتها ثم ينجز المهمة فتختفي الوحدة
    assert [u["id"] for u in client.get("/api/units", headers=hw).json()] == [u1["id"]]
    assert client.patch(f"/api/units/{u1['id']}/status", json={"status": "cleaning"}, headers=hw).status_code == 200
    assert client.patch(f"/api/units/{u2['id']}/status", json={"status": "cleaning"}, headers=hw).status_code == 403
    assert len(client.get("/api/tasks", headers=hw).json()["items"]) == 1
    assert client.post(f"/api/tasks/{t.json()['id']}/done", headers=hw).status_code == 200
    assert client.get("/api/units", headers=hw).json() == []

    # تعديل المدير لنطاق مستخدم واحد
    assert client.patch(f"/api/users/{w['id']}", json={"visibility": "all"}, headers=admin).json()["visibility"] == "all"
    assert len(client.get("/api/units", headers=hw).json()) >= 2
    assert client.patch(f"/api/users/{w['id']}", json={"visibility": None}, headers=admin).json()["visibility"] == "assigned"

    # حدود الإدارة: مدير الإدارة لا يعدّل المدير العام ولا المصفوفة
    assert client.patch(f"/api/users/{me['id']}", json={"full_name": "x"}, headers=hd).status_code == 403
    assert client.put("/api/levels/matrix", json={"matrix": {"worker": {"view_dashboard": True}}}, headers=hd).status_code == 403
    assert client.put("/api/levels/matrix", json={"matrix": {"dept_manager": {"update_units": False}}}, headers=admin).status_code == 422

    # المدير العام يمنح العامل صلاحية اللوحة ثم يعيدها
    ok = client.put("/api/levels/matrix", json={"matrix": {"worker": {"view_dashboard": True}}}, headers=admin)
    assert ok.status_code == 200 and ok.json()["matrix"]["worker"]["view_dashboard"] is True
    assert client.get("/api/dashboard", headers=hw).status_code == 200
    client.put("/api/levels/matrix", json={"matrix": {"worker": {"view_dashboard": False}}}, headers=admin)
    assert client.get("/api/dashboard", headers=hw).status_code == 403


def test_legacy_users_keep_working(client):
    admin = login(client, "admin", "admin-pass-123")
    v = client.post("/api/users", json={"username": "oldview", "full_name": "قديم", "password": "oldview-pass-1",
                                        "role": "viewer"}, headers=admin).json()
    assert v["level"] == "receptionist" and v["role"] == "viewer"
    hv = login(client, "oldview", "oldview-pass-1")
    assert client.get("/api/dashboard", headers=hv).status_code == 200
    # حفظ الاسم دون تغيير المستوى لا يرقّي المشاهد إلى محدّث
    assert client.patch(f"/api/users/{v['id']}", json={"full_name": "قديم2"}, headers=admin).json()["role"] == "viewer"
