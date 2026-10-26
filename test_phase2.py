import pytest
from starlette.websockets import WebSocketDisconnect


def login(client):
    r = client.post("/api/auth/login", data={"username": "admin", "password": "admin-pass-123"})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def test_dashboard_and_realtime(client):
    token = login(client)
    h = {"Authorization": f"Bearer {token}"}

    site = client.post("/api/sites", json={"name": "موقع المرحلة 2"}, headers=h).json()
    b = client.post("/api/buildings", json={"site_id": site["id"], "name": "ب1"}, headers=h).json()
    r = client.post("/api/units/bulk", json={"building_id": b["id"], "numbers": ["1", "2", "3"]}, headers=h)
    assert r.json()["created"] == 3
    units = client.get(f"/api/units?site_id={site['id']}", headers=h).json()

    with client.websocket_connect(f"/ws?token={token}") as ws:
        r = client.patch(f"/api/units/{units[0]['id']}/status", headers=h,
                         json={"status": "rented", "tenant_name": "س", "contract_end": "2999-01-01"})
        assert r.status_code == 200, r.text
        msg = ws.receive_json()
        assert msg["type"] == "changed" and msg["site_id"] == site["id"]

    d = client.get(f"/api/dashboard?site_id={site['id']}", headers=h).json()
    assert d["totals"]["total"] == 3
    assert d["totals"]["counts"]["rented"] == 1 and d["totals"]["counts"]["available"] == 2
    assert d["contracts"]["later"] == 1
    assert d["history"][-1]["rented"] == 1


def test_ws_rejects_bad_token(client):
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws?token=bad"):
            pass
