def login(client, username, password):
    r = client.post("/api/auth/login", data={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def test_full_flow(client):
    admin = login(client, "admin", "admin-pass-123")

    assert client.post("/api/auth/login", data={"username": "admin", "password": "wrong"}).status_code == 401
    assert client.get("/api/sites").status_code == 401

    # موقعان وعمارة ووحدة
    s1 = client.post("/api/sites", json={"name": "المهرجان", "code": "MHR"}, headers=admin)
    assert s1.status_code == 201, s1.text
    s2 = client.post("/api/sites", json={"name": "الأندلس"}, headers=admin).json()
    assert client.post("/api/sites", json={"name": "المهرجان"}, headers=admin).status_code == 409
    s1 = s1.json()

    b1 = client.post("/api/buildings", json={"site_id": s1["id"], "name": "عمارة 1"}, headers=admin)
    assert b1.status_code == 201
    b1 = b1.json()
    b2 = client.post("/api/buildings", json={"site_id": s2["id"], "name": "عمارة A"}, headers=admin).json()

    u = client.post("/api/units", json={"building_id": b1["id"], "number": "101"}, headers=admin)
    assert u.status_code == 201
    u = u.json()
    assert u["status"] == "available" and u["site_id"] == s1["id"]
    assert client.post("/api/units", json={"building_id": b1["id"], "number": "101"}, headers=admin).status_code == 409

    bulk = client.post("/api/units/bulk", json={"building_id": b1["id"], "numbers": ["101", "102", "103"]},
                       headers=admin).json()
    assert bulk == {"created": 2, "skipped_existing": ["101"]}
    u_other = client.post("/api/units", json={"building_id": b2["id"], "number": "A1"}, headers=admin).json()

    # مستخدمون: محدّث على الموقع الأول، ومشاهد
    upd = client.post("/api/users", json={"username": "Ali", "full_name": "علي", "password": "ali-pass-123",
                                          "role": "updater", "site_ids": [s1["id"]]}, headers=admin)
    assert upd.status_code == 201 and upd.json()["site_ids"] == [s1["id"]]
    assert client.post("/api/users", json={"username": "viewer1", "full_name": "مشاهد",
                                           "password": "viewer-pass-1", "role": "viewer"},
                       headers=admin).status_code == 201
    ali = login(client, "ali", "ali-pass-123")
    viewer = login(client, "viewer1", "viewer-pass-1")

    # المحدّث لا ينشئ مواقع ولا مستخدمين
    assert client.post("/api/sites", json={"name": "x"}, headers=ali).status_code == 403
    assert client.get("/api/users", headers=ali).status_code == 403

    # المحدّث يغيّر حالة وحدة في موقعه
    r = client.patch(f"/api/units/{u['id']}/status", headers=ali,
                     json={"status": "rented", "tenant_name": "محمد", "contract_end": "2027-03-01"})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "rented" and r.json()["tenant_name"] == "محمد"

    # ...لكن ليس في موقع غير مسند له
    assert client.patch(f"/api/units/{u_other['id']}/status", headers=ali,
                        json={"status": "maintenance"}).status_code == 403
    # والمشاهد لا يحدّث
    assert client.patch(f"/api/units/{u['id']}/status", headers=viewer,
                        json={"status": "cleaning"}).status_code == 403

    # العودة إلى متاحة تمسح المستأجر
    r = client.patch(f"/api/units/{u['id']}/status", headers=ali, json={"status": "available"}).json()
    assert r["tenant_name"] is None and r["contract_end"] is None

    # السجل: إنشاء + تأجير + إتاحة
    hist = client.get(f"/api/units/{u['id']}/history", headers=viewer).json()
    assert [h["new_status"] for h in hist] == ["available", "rented", "available"]

    # الفلترة والملخص
    rows = client.get("/api/units", params={"site_id": s1["id"]}, headers=viewer).json()
    assert len(rows) == 3
    assert client.get("/api/units", params={"status": "rented"}, headers=viewer).json() == []
    summ = client.get("/api/summary", headers=viewer).json()
    assert summ["total"] == 4 and summ["by_status"]["available"] == 4

    # حالة غير صالحة
    assert client.patch(f"/api/units/{u['id']}/status", headers=admin,
                        json={"status": "nope"}).status_code == 422

    # تغيير كلمة المرور
    assert client.post("/api/auth/change-password", headers=ali,
                       json={"old_password": "ali-pass-123", "new_password": "ali-new-pass-9"}).status_code == 204
    login(client, "ali", "ali-new-pass-9")

    # تعطيل المستخدم يمنع الدخول
    uid = client.get("/api/auth/me", headers=ali).json()["id"]
    assert client.patch(f"/api/users/{uid}", json={"is_active": False}, headers=admin).status_code == 200
    assert client.get("/api/auth/me", headers=ali).status_code == 401
