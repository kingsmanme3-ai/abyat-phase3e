from test_phase3 import login


def mk_user(client, h, name, role, sites=()):
    r = client.post("/api/users", headers=h, json={"username": name, "full_name": name, "password": f"{name}-pass-1",
                                                   "role": role, "site_ids": list(sites)})
    assert r.status_code == 201, r.text
    return login(client, name, f"{name}-pass-1")


def cats(client, h):
    return {c["key"]: c for c in client.get("/api/report-categories", headers=h).json()}


def test_nine_cards_seeded_and_visibility(client):
    h = login(client)
    c = cats(client, h)
    assert len(c) == 9 and c["units"]["kind"] == "data" and c["finance"]["kind"] == "authored"
    hu = mk_user(client, h, "upd3b", "updater")
    cu = cats(client, hu)
    assert cu["reception"]["can_write"] and not cu["finance"]["can_write"] and "finance" not in cu
    assert "users" not in cu and "units" in cu                       # تقرير المستخدمين للمدير فقط
    hv = mk_user(client, h, "view3b", "viewer")
    assert cats(client, hv)["reception"]["can_read"] and not cats(client, hv)["reception"]["can_write"]


def test_write_draft_submit_flow(client):
    h = login(client)
    hu = mk_user(client, h, "writer3b", "updater")
    cid = cats(client, hu)["reception"]["id"]
    tpl = client.get(f"/api/report-categories/{cid}/template", headers=hu).json()
    assert tpl["version"] == 1 and any(f["key"] == "shift" for f in tpl["schema"]["fields"])

    r = client.post("/api/authored-reports", headers=hu, json={"category_id": cid, "title": "وردية الأحد", "body": {"arrivals": "٥"}})
    assert r.status_code == 201, r.text
    rep = r.json()
    assert rep["status"] == "draft" and rep["serial"] is None and rep["body"]["arrivals"] == 5

    bad = client.post(f"/api/authored-reports/{rep['id']}/submit", headers=hu)
    assert bad.status_code == 422 and "مطلوب" in bad.json()["detail"]

    ok = client.patch(f"/api/authored-reports/{rep['id']}", headers=hu,
                      json={"body": {"shift": "صباحية", "handover": "كل شيء سليم", "arrivals": 5}, "priority": "high"})
    assert ok.status_code == 200
    s = client.post(f"/api/authored-reports/{rep['id']}/submit", headers=hu).json()
    assert s["status"] == "submitted" and s["serial"].startswith("RPT-") and s["serial"].endswith("000001")
    assert client.patch(f"/api/authored-reports/{rep['id']}", headers=hu, json={"title": "x"}).status_code == 409
    assert client.delete(f"/api/authored-reports/{rep['id']}", headers=hu).status_code == 409

    second = client.post("/api/authored-reports", headers=hu, json={
        "category_id": cid, "title": "وردية الاثنين", "submit": True, "body": {"shift": "مسائية", "handover": "ملاحظة"}}).json()
    assert second["serial"].endswith("000002")


def test_permissions_and_draft_privacy(client):
    h = login(client)
    hu = mk_user(client, h, "w2_3b", "updater")
    other = mk_user(client, h, "o2_3b", "updater")
    cid = cats(client, hu)["specialists"]["id"]
    draft = client.post("/api/authored-reports", headers=hu, json={"category_id": cid, "title": "مسودة خاصة"}).json()
    assert client.get(f"/api/authored-reports/{draft['id']}", headers=other).status_code == 404   # المسودة لكاتبها والمدير
    assert client.get(f"/api/authored-reports/{draft['id']}", headers=h).status_code == 200
    assert client.patch(f"/api/authored-reports/{draft['id']}", headers=other, json={"title": "x"}).status_code == 404

    fin = cats(client, h)["finance"]["id"]
    assert client.post("/api/authored-reports", headers=hu, json={"category_id": fin, "title": "x"}).status_code == 403
    assert client.post("/api/authored-reports", headers=h, json={"category_id": cats(client, h)["units"]["id"], "title": "x"}).status_code == 400

    users = {u["username"]: u for u in client.get("/api/users", headers=h).json()}
    perms = {"items": [{"role": "updater", "can_write": False, "can_read": False},
                       {"user_id": users["w2_3b"]["id"], "can_write": True, "can_read": True}]}
    assert client.put(f"/api/report-categories/{fin}/permissions", headers=h, json=perms).status_code == 200
    assert client.post("/api/authored-reports", headers=hu, json={"category_id": fin, "title": "مالي"}).status_code == 201
    assert client.post("/api/authored-reports", headers=other, json={"category_id": fin, "title": "x"}).status_code == 403
    assert client.put(f"/api/report-categories/{fin}/permissions", headers=hu, json=perms).status_code == 403


def test_template_versions_keep_old_reports(client):
    h = login(client)
    cid = cats(client, h)["hr"]["id"]
    old = client.post("/api/authored-reports", headers=h, json={"category_id": cid, "title": "قديم"}).json()
    new_schema = {"name": "قالب جديد", "schema": {"fields": [{"key": "only", "label": "حقل وحيد", "type": "text", "required": True}]}}
    v = client.put(f"/api/report-categories/{cid}/template", headers=h, json=new_schema).json()
    assert v["version"] == 2
    assert client.put(f"/api/report-categories/{cid}/template", headers=h,
                      json={"name": "x", "schema": {"fields": [{"key": "A", "label": "x", "type": "text"}]}}).status_code == 422
    assert client.get(f"/api/authored-reports/{old['id']}", headers=h).json()["template_version"] == 1
    fresh = client.post("/api/authored-reports", headers=h, json={"category_id": cid, "title": "جديد"}).json()
    assert fresh["template_version"] == 2


def test_list_filters_and_departments(client):
    h = login(client)
    lst = client.get("/api/authored-reports?status=submitted", headers=h).json()
    assert lst["total"] >= 2 and all(i["status"] == "submitted" for i in lst["items"])
    assert client.get("/api/authored-reports?mine=true&status=draft", headers=h).status_code == 200
    d = client.post("/api/departments", headers=h, json={"name": "إدارة اختبار"}).json()
    assert any(x["id"] == d["id"] for x in client.get("/api/departments", headers=h).json())
    assert client.post("/api/departments", headers=h, json={"name": "إدارة اختبار"}).status_code == 409
