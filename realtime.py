from fastapi import WebSocket


class Manager:
    """بث رسائل التحديث لكل المتصلين. يعمل داخل عملية Uvicorn واحدة (عامل واحد)."""

    def __init__(self) -> None:
        self.connections: set = set()

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self.connections.add(ws)

    def disconnect(self, ws: WebSocket) -> None:
        self.connections.discard(ws)

    async def broadcast(self, message: dict) -> None:
        dead = []
        for ws in list(self.connections):
            try:
                await ws.send_json(message)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)


manager = Manager()


def changed_event(site_id: int, unit_id: int | None = None) -> dict:
    return {"type": "changed", "site_id": site_id, "unit_id": unit_id}
