"""Supervisor for the sync and download jobs.

The `running` state lives in the database, not in memory: the same job can never start
twice in parallel even when its execution lasts days, and a process restart leaves no
ghost jobs stuck.

Sync jobs and download jobs are two tables with two independent numbering schemes, so
everything here is keyed by the pair (kind, id): job 3 and download job 3 are two
different jobs and must be able to run at the same time.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from datetime import UTC, datetime

from sqlalchemy import select, update

from ..config import settings
from ..db import SessionLocal
from ..models import DownloadJob, DownloadRun, JobRun, SyncJob
from .download import execute_download_job
from .runner import StopSignal, execute_job

log = logging.getLogger(__name__)

SYNC = "sync"
DOWNLOAD = "download"

_MODELS = {SYNC: SyncJob, DOWNLOAD: DownloadJob}
_RUNS = {SYNC: JobRun, DOWNLOAD: DownloadRun}
_EXECUTORS = {SYNC: execute_job, DOWNLOAD: execute_download_job}

Key = tuple[str, int]


class Scheduler:
    def __init__(self) -> None:
        self._tasks: dict[Key, asyncio.Task] = {}
        self._cancels: dict[Key, StopSignal] = {}
        self._loop_task: asyncio.Task | None = None

    async def reset_stale(self) -> None:
        """Brings back to idle the jobs left running by an abrupt process stop."""
        async with SessionLocal() as session:
            for kind, model in _MODELS.items():
                await session.execute(
                    update(model)
                    .where(model.status == "running")
                    .values(status="idle", phase=None)
                )
                run_model = _RUNS[kind]
                await session.execute(
                    update(run_model)
                    .where(run_model.status == "running")
                    .values(status="stopped", finished_at=datetime.now(UTC))
                )
            await session.commit()

    def is_running(self, kind: str, job_id: int) -> bool:
        task = self._tasks.get((kind, job_id))
        return task is not None and not task.done()

    async def trigger(self, kind: str, job_id: int) -> bool:
        """Starts a job right away. Returns False if it was already running."""
        if self.is_running(kind, job_id):
            return False

        async with SessionLocal() as session:
            job = await session.get(_MODELS[kind], job_id)
            if job is None:
                return False
            job.status = "running"
            job.phase = "index" if kind == DOWNLOAD else "scan"
            await session.commit()

        self._spawn(kind, job_id)
        return True

    def _spawn(self, kind: str, job_id: int) -> None:
        key = (kind, job_id)
        cancel = StopSignal()
        self._cancels[key] = cancel
        execute = _EXECUTORS[kind]

        async def wrapper() -> None:
            try:
                await execute(job_id, cancel)
            finally:
                self._tasks.pop(key, None)
                self._cancels.pop(key, None)

        self._tasks[key] = asyncio.create_task(wrapper(), name=f"{kind}-{job_id}")

    async def stop(self, kind: str, job_id: int) -> bool:
        cancel = self._cancels.get((kind, job_id))
        if cancel is None:
            return False
        cancel.set("user")
        return True

    async def _tick(self) -> None:
        now = datetime.now(UTC)
        due: list[Key] = []
        async with SessionLocal() as session:
            for kind, model in _MODELS.items():
                result = await session.execute(
                    select(model).where(model.enabled.is_(True), model.status == "idle")
                )
                due.extend(
                    (kind, job.id)
                    for job in result.scalars()
                    if job.next_run_at is None or _as_utc(job.next_run_at) <= now
                )

        for kind, job_id in due:
            if self.is_running(kind, job_id):
                continue
            log.info("Starting %s job %d", kind, job_id)
            await self.trigger(kind, job_id)

    async def _loop(self) -> None:
        while True:
            try:
                await self._tick()
            except Exception:
                log.exception("Error in the scheduler tick")
            await asyncio.sleep(settings.scheduler_tick_seconds)

    async def start(self) -> None:
        await self.reset_stale()
        self._loop_task = asyncio.create_task(self._loop(), name="scheduler")

    async def shutdown(self) -> None:
        if self._loop_task is not None:
            self._loop_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._loop_task
            self._loop_task = None

        for cancel in self._cancels.values():
            # Explicit reason: jobs stopped by shutdown are not pushed forward by a whole
            # interval and resume right away on restart.
            cancel.set("shutdown")
        tasks = list(self._tasks.values())
        if tasks:
            # Running jobs are notified and stop at their next checkpoint.
            await asyncio.gather(*tasks, return_exceptions=True)


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


scheduler = Scheduler()
