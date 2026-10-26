"""اختبارات API للمرحلة 3ج: سجل التقارير (بحث/فلترة/فرز)، PDF القياسي، ومكتبة الوسائط وتصنيفاتها."""
import base64

from test_phase3 import login
from test_phase3b import cats, mk_user

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGP4z8DwHwAFAAH/q842iQAAAABJRU5ErkJggg==")
MP4 = b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom" + b"\x00" * 64


def mcats(client, h):
    return {c["name"]: c for c in client.get("/api/media-categories", headers=h).json()}


def upload(client, h, cat, data=PNG, name="a.png", **extra):
    return client.post("/api/media", headers=h, data={"category_id": cat, **extra}, files={"file": (name, data)})


def mk_report(client, h, cid, title, body=None, submit=True, **kw):
    r = client.post("/api/authored-reports", headers=h, json={"category_id": cid, "title": title, "submit": submit,
                                                              "body": body or {}, **kw})
    assert r.status_code == 201, r.text
    return r.json()


# ------------------------------------------------------------------ سجل التقارير
def test_log_search_filter_sort(client):
    h = login(client)
    cid = cats(client, h)["specialists"]["id"]
    body = {"kind": "ملاحظة فنية", "findings": "تسريب مياه تحت المغسلة"}
    a = mk_report(client, h, cid, "تقرير ألف", body, priority="urgent", report_date="2020-01-01")
    b = mk_report(client, h, cid, "تقرير باء", {"kind": "توصية", "findings": "استبدال القفل"}, priority="low", report_date="2020-01-05")
    q = lambda s: client.get(f"/api/authored-reports?category_id={cid}&{s}", headers=h).json()

    assert {i["title"] for i in q("q=تسريب")["items"]} == {"تقرير ألف"}                 # بحث في المتن
    assert {i["title"] for i in q("q=باء")["items"]} == {"تقرير باء"}                   # بحث في العنوان
    assert q(f"serial={b['serial'][-3:]}")["total"] >= 1                                # جزء من الرقم
    ar = "".join("٠١٢٣٤٥٦٧٨٩"[int(d)] if d.isdigit() else d for d in b["serial"])
    assert q(f"serial={ar}")["items"][0]["id"] == b["id"]                               # أرقام عربية
    assert [i["title"] for i in q("priority=urgent")["items"]] == ["تقرير ألف"]
    assert {i["title"] for i in q("date_from=2020-01-03&date_to=2020-01-10")["items"]} == {"تقرير باء"}
    assert q(f"author_id={a['author_id']}")["total"] >= 2 and q("author_id=999999")["total"] == 0

    asc = [i["report_date"] for i in q("sort=report_date&order=asc")["items"]]
    assert asc == sorted(asc)
    pr = [i["priority"] for i in q("sort=priority&order=desc")["items"]]
    assert pr[0] == "urgent" and pr[-1] == "low"                                        # ترتيب الأولوية لا الأبجدي
    first = q("sort=title&order=asc&limit=1")
    assert first["total"] >= 2 and len(first["items"]) == 1
    assert q("limit=1&offset=1")["items"][0]["id"] != q("limit=1&offset=0")["items"][0]["id"]
    assert client.get("/api/authored-reports?sort=password", headers=h).status_code == 422
    row = q("")["items"][0]
    assert {"time", "media_count", "department", "site", "author", "category", "serial"} <= set(row)


def test_log_respects_visibility_and_facets(client):
    h = login(client)
    w = mk_user(client, h, "w3c", "updater")
    other = mk_user(client, h, "o3c", "updater")
    viewer = mk_user(client, h, "v3c", "viewer")
    cid = cats(client, w)["specialists"]["id"]
    draft = mk_report(client, w, cid, "مسودة سرية للبحث", {"findings": "كلمة-فريدة"}, submit=False)
    sent = mk_report(client, w, cid, "مرسل ظاهر", {"kind": "توصية", "findings": "كلمة-فريدة"})
    assert client.get("/api/authored-reports?q=كلمة-فريدة", headers=other).json()["total"] == 1   # لا تظهر المسودة لغير كاتبها
    assert client.get("/api/authored-reports?q=كلمة-فريدة", headers=w).json()["total"] == 2
    assert client.get("/api/authored-reports?q=كلمة-فريدة", headers=viewer).json()["total"] == 1
    names = [a["name"] for a in client.get("/api/authored-reports/facets", headers=other).json()["authors"]]
    assert "w3c" in names
    assert draft["status"] == "draft" and sent["status"] == "submitted"


# ------------------------------------------------------------------ PDF القياسي
def test_print_html_and_pdf_endpoint(client):
    h = login(client)
    cid = cats(client, h)["specialists"]["id"]
    rep = mk_report(client, h, cid, "تقرير للطباعة <b>", {"kind": "توصية", "findings": "نص"})
    r = client.get(f"/api/authored-reports/{rep['id']}/print", headers=h)
    assert r.status_code == 200 and "text/html" in r.headers["content-type"]
    assert rep["serial"] in r.text and "ملاحظات الإدارة" in r.text and "&lt;b&gt;" in r.text and "<b>" not in r.text.split("<h1>")[1].split("</h1>")[0]
    pdf = client.get(f"/api/authored-reports/{rep['id']}/pdf", headers=h)
    assert pdf.status_code in (200, 501)                                                 # 501 إن غابت مكتبات PDF في بيئة الاختبار
    if pdf.status_code == 200:
        assert pdf.content.startswith(b"%PDF") and pdf.headers["content-type"] == "application/pdf"
    other = mk_user(client, h, "pp3c", "updater")
    draft = mk_report(client, h, cid, "مسودة المدير", submit=False)
    assert client.get(f"/api/authored-reports/{draft['id']}/print", headers=other).status_code == 404
    assert client.get(f"/api/authored-reports/{rep['id']}/print").status_code == 401


# ------------------------------------------------------------------ تصنيفات الوسائط
def test_default_categories_and_crud(client):
    h = login(client)
    c = mcats(client, h)
    assert {"الصيانة", "النظافة", "النجارة"} <= set(c)
    new = client.post("/api/media-categories", headers=h, json={"name": "الكهرباء", "color": "#ca8a04"})
    assert new.status_code == 201
    assert client.post("/api/media-categories", headers=h, json={"name": "الكهرباء"}).status_code == 409
    nid = new.json()["id"]
    assert client.patch(f"/api/media-categories/{nid}", headers=h, json={"name": "الكهرباء والإنارة", "color": "bad"}).status_code == 200
    got = mcats(client, h)["الكهرباء والإنارة"]
    assert got["color"] == "#ca8a04"                                                     # لون غير صالح يُتجاهل
    u = mk_user(client, h, "mc3c", "updater")
    assert client.post("/api/media-categories", headers=u, json={"name": "x"}).status_code == 403
    assert client.delete(f"/api/media-categories/{nid}", headers=h).status_code == 204


def test_delete_category_with_files_needs_move(client):
    h = login(client)
    c = mcats(client, h)
    tmp = client.post("/api/media-categories", headers=h, json={"name": "مؤقت للحذف"}).json()["id"]
    m = upload(client, h, tmp).json()
    d = client.delete(f"/api/media-categories/{tmp}", headers=h)
    assert d.status_code == 409 and "1 ملف" in d.json()["detail"]
    assert client.delete(f"/api/media-categories/{tmp}?move_to={tmp}", headers=h).status_code == 422
    assert client.delete(f"/api/media-categories/{tmp}?move_to={c['النجارة']['id']}", headers=h).status_code == 204
    items = client.get(f"/api/media?category_id={c['النجارة']['id']}", headers=h).json()["items"]
    assert any(i["id"] == m["id"] for i in items)


def test_library_cards_setting(client):
    h = login(client)
    assert set(client.get("/api/media-settings", headers=h).json()["cards"]) == {"specialists", "reception"}
    assert client.put("/api/media-settings", headers=h, json={"cards": ["nope"]}).status_code == 422
    assert client.put("/api/media-settings", headers=h, json={"cards": ["specialists"]}).status_code == 200
    assert client.get("/api/media-settings", headers=h).json()["cards"] == ["specialists"]


# ------------------------------------------------------------------ رفع الوسائط وربطها بالتقارير
def test_upload_validation(client):
    h = login(client)
    c = mcats(client, h)["الصيانة"]["id"]
    ok = upload(client, h, c, caption="تسريب", name="../../x.png")
    assert ok.status_code == 201, ok.text
    j = ok.json()
    assert j["kind"] == "image" and j["original_name"] == "x.png" and j["caption"] == "تسريب" and "t=" in j["url"]
    assert upload(client, h, c, MP4, "v.mp4").json()["kind"] == "video"
    assert upload(client, h, c, b"<html><script>1</script></html>", "x.png").status_code == 415      # الامتداد لا يخدع
    assert upload(client, h, c, b"%PDF-1.4 " + b"0" * 40, "x.jpg").status_code == 415
    assert upload(client, h, 999999).status_code == 404
    assert upload(client, h, c, report_id=999999).status_code == 404
    v = mk_user(client, h, "vw_m3c", "viewer")
    assert upload(client, v, c).status_code == 403


def test_signed_file_url_and_download(client):
    h = login(client)
    c = mcats(client, h)["النظافة"]["id"]
    m = upload(client, h, c, name="صورة الغرفة.png").json()
    r = client.get(m["url"])
    assert r.status_code == 200 and r.content == PNG and r.headers["content-type"] == "image/png"
    assert r.headers["x-content-type-options"] == "nosniff"
    assert client.get(m["url"] + "&download=true").headers["content-disposition"].startswith("attachment")
    assert client.get(f"/api/media/{m['id']}/file").status_code == 401                              # بلا توقيع
    assert client.get(f"/api/media/{m['id']}/file?t=garbage").status_code == 401
    other = upload(client, h, c).json()
    tok = m["url"].split("t=")[1]
    assert client.get(f"/api/media/{other['id']}/file?t={tok}").status_code == 401                  # توقيع ملف آخر
    assert client.get(f"/api/media/{m['id']}/file?t={login(client)['Authorization'].split()[1]}").status_code == 401   # رمز الدخول لا يصلح


def test_media_linked_to_reports_rules(client):
    h = login(client)
    w = mk_user(client, h, "wm3c", "updater")
    o = mk_user(client, h, "om3c", "updater")
    cid = cats(client, w)["specialists"]["id"]
    mc = mcats(client, h)["النجارة"]["id"]
    draft = mk_report(client, w, cid, "مسودة بوسائط", {"kind": "توصية", "findings": "نص"}, submit=False)
    m = upload(client, w, mc, report_id=draft["id"], caption="الباب")
    assert m.status_code == 201 and m.json()["report_id"] == draft["id"]
    mid = m.json()["id"]
    assert upload(client, o, mc, report_id=draft["id"]).status_code == 404                            # مسودة غيره لا يراها
    assert [i["id"] for i in client.get(f"/api/media?report_id={draft['id']}", headers=w).json()["items"]] == [mid]
    assert client.get(f"/api/media?report_id={draft['id']}", headers=o).json()["total"] == 0           # وسائط المسودة خاصة

    assert client.post(f"/api/authored-reports/{draft['id']}/submit", headers=w).status_code == 200
    assert client.get(f"/api/media?report_id={draft['id']}", headers=o).json()["total"] == 1           # صارت ظاهرة بعد الإرسال
    rows = {i["id"]: i for i in client.get(f"/api/authored-reports?category_id={cid}", headers=w).json()["items"]}
    assert rows[draft["id"]]["media_count"] == 1
    assert client.delete(f"/api/media/{mid}", headers=w).status_code == 403                             # مقفلة بعد الإرسال
    assert client.delete(f"/api/media/{mid}", headers=h).status_code == 204                             # المدير يحذف


def test_draft_delete_removes_media_and_edit_rules(client):
    h = login(client)
    w = mk_user(client, h, "wd3c", "updater")
    o = mk_user(client, h, "od3c", "updater")
    cid = cats(client, w)["specialists"]["id"]
    mc = mcats(client, h)["الصيانة"]["id"]
    draft = mk_report(client, w, cid, "للحذف", submit=False)
    m = upload(client, w, mc, report_id=draft["id"]).json()
    free = upload(client, w, mc).json()                                                                   # بلا تقرير
    assert client.patch(f"/api/media/{free['id']}", headers=o, json={"caption": "x"}).status_code == 403
    assert client.patch(f"/api/media/{free['id']}", headers=w, json={"caption": "وصف جديد", "report_id": draft["id"]}).json()["report_id"] == draft["id"]
    assert client.patch(f"/api/media/{free['id']}", headers=w, json={"report_id": None}).json()["report_id"] is None
    assert client.patch(f"/api/media/{free['id']}", headers=w, json={"category_id": 999999}).status_code == 404
    url = m["url"]
    assert client.delete(f"/api/authored-reports/{draft['id']}", headers=w).status_code == 204
    assert client.get(url).status_code == 404                                                             # حُذف الملف مع المسودة
    assert client.get(f"/api/media?report_id={draft['id']}", headers=h).json()["total"] == 0
