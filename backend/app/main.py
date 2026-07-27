import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from sqlalchemy import select

from .api import (
    accounts,
    auth,
    dashboard,
    downloads,
    export,
    files,
    jobs,
    maintenance,
    preferences,
    rclone,
    ws,
)
from .db import SessionLocal, engine
from .migrate import upgrade_database
from .models import User
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
        log.info("Admin user created, initial password admin, change required at sign in")


@asynccontextmanager
async def lifespan(_: FastAPI):
    await upgrade_database(engine)
    await seed_admin()

    # rclone wants a file: it is rewritten from the encrypted copy in the database at every start.
    async with SessionLocal() as session:
        await rclone.sync_config_to_disk(session)

    hub.start()
    await manager.restore_sessions()
    await scheduler.start()
    log.info("tgbackup started")

    yield

    await scheduler.shutdown()
    await manager.shutdown()
    await hub.stop()
    await engine.dispose()


app = FastAPI(title="tgbackup", version="1.0.0", lifespan=lifespan)

app.include_router(auth.router)
app.include_router(accounts.router)
app.include_router(jobs.router)
app.include_router(downloads.router)
app.include_router(files.router)
app.include_router(dashboard.router)
app.include_router(export.router)
app.include_router(maintenance.router)
app.include_router(preferences.router)
app.include_router(rclone.router)
app.include_router(ws.router)


@app.get("/api/health")
async def health() -> JSONResponse:
    return JSONResponse({"status": "ok"})
