import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from sqlalchemy import select

from .api import accounts, auth, dashboard, files, jobs, rclone, ws
from .db import SessionLocal, engine
from .migrate import ensure_schema
from .models import Base, User
from .security import hash_password
from .sync.progress import hub
from .sync.scheduler import scheduler
from .telegram.manager import manager

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
)
log = logging.getLogger("tgbackup")


async def seed_admin() -> None:
    async with SessionLocal() as session:
        existing = await session.scalar(select(User).limit(1))
        if existing is not None:
            return
        session.add(
            User(
                username="admin",
                password_hash=hash_password("admin"),
                must_change_password=True,
            )
        )
        await session.commit()
        log.info("Utente admin creato, password iniziale admin, cambio obbligatorio al login")


@asynccontextmanager
async def lifespan(_: FastAPI):
    async with engine.begin() as connection:
        await ensure_schema(connection)
        await connection.run_sync(Base.metadata.create_all)
    await seed_admin()

    # rclone vuole un file: si riscrive da quello cifrato nel database a ogni avvio.
    async with SessionLocal() as session:
        await rclone.sync_config_to_disk(session)

    hub.start()
    await manager.restore_sessions()
    await scheduler.start()
    log.info("tgbackup avviato")

    yield

    await scheduler.shutdown()
    await manager.shutdown()
    await hub.stop()
    await engine.dispose()


app = FastAPI(title="tgbackup", version="1.0.0", lifespan=lifespan)

app.include_router(auth.router)
app.include_router(accounts.router)
app.include_router(jobs.router)
app.include_router(files.router)
app.include_router(dashboard.router)
app.include_router(rclone.router)
app.include_router(ws.router)


@app.get("/api/health")
async def health() -> JSONResponse:
    return JSONResponse({"status": "ok"})
