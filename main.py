import asyncio
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from sqlalchemy import select, text
from starlette.concurrency import run_in_threadpool

from .config import settings
from .database import Base, SessionLocal, engine
from .models import Role, User
from .realtime import manager
from .report_runner import scheduler_loop
from .routers import (access_levels, auth, authoring, buildings, dashboard, media, report_schedules, reports, sites,
                      summary, tasks, units, users)
from .security import decode_token, hash_password
from .snapshots import backfill_history, refresh_all_snapshots
from .upgrade import upgrade_schema

log = logging.getLogger("abyat")


def seed_admin() -> None:
    with SessionLocal() as db:
        if db.scalar(select(User.id).limit(1)) is None:
            db.add(User(username=settings.admin_username.lower(),
                        full_name=settings.admin_full_name,
                        password_hash=hash_password(settings.admin_password),
                        role=Role.admin, job_level="general_manager"))
            db.commit()


def init_snapshots() -> None:
    with SessionLocal() as db:
        backfill_history(db)          # مرة واحدة: يبني التاريخ من سجل تغيّر الحالات
        refresh_all_snapshots(db)


def _refresh_job() -> None:
    with SessionLocal() as db:
        refresh_all_snapshots(db)


async def snapshot_loop() -> None:
    """يضمن وجود لقطة لليوم الحالي لكل موقع حتى لو لم يتغير شيء."""
    while True:
        await asyncio.sleep(1800)
        try:
            await run_in_threadpool(_refresh_job)
        except Exception:
            log.exception("فشل تحديث اللقطات اليومية")


@asynccontextmanager
async def lifespan(_: FastAPI):
    # إنشاء الجداول تلقائياً (يُنشئ daily_snapshots أيضاً). قبل الإنتاج (المرحلة 5) يُستبدل بـ Alembic.
    Base.metadata.create_all(engine)
    upgrade_schema(engine)                    # 3هـ: أعمدة المستويات دون مسّ البيانات
    seed_admin()
    with SessionLocal() as _db:
        authoring.seed_report_system(_db)     # 3ب: البطاقات التسع والقوالب والصلاحيات الافتراضية
        media.seed_media(_db)                 # 3ج: تصنيفات الوسائط (الصيانة، النظافة، النجارة)
        access_levels.seed_matrix(_db)        # 3هـ: مصفوفة الصلاحيات الافتراضية
    init_snapshots()
    os.makedirs(settings.reports_dir, exist_ok=True)
    os.makedirs(settings.media_dir, exist_ok=True)
    tasks = [asyncio.create_task(snapshot_loop())]
    if settings.scheduler_enabled:
        tasks.append(asyncio.create_task(scheduler_loop()))
    yield
    for t in tasks:
        t.cancel()


app = FastAPI(title="منظومة أسوار بلس للحصر والإشغال", version="0.3.4", lifespan=lifespan)

for r in (auth, users, sites, buildings, units, summary, dashboard, reports, report_schedules, authoring, media,
          access_levels, tasks):
    app.include_router(r.router)
app.include_router(report_schedules.runs_router)


@app.get("/health", tags=["system"])
def health():
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))
    return {"status": "ok"}


def _user_active(user_id: int) -> bool:
    with SessionLocal() as db:
        u = db.get(User, user_id)
        return bool(u and u.is_active)


@app.websocket("/ws")
async def ws_endpoint(websocket: WebSocket, token: str = ""):
    """قناة التحديث اللحظي: يستقبل التوكن في الرابط، ويبث {"type":"changed"} عند أي تغيير."""
    uid = decode_token(token)
    if not uid or not await run_in_threadpool(_user_active, uid):
        await websocket.close(code=4401)
        return
    await manager.connect(websocket)
    try:
        while True:
            await websocket.receive_text()    # رسائل ping من العميل فقط
    except WebSocketDisconnect:
        pass
    finally:
        manager.disconnect(websocket)
