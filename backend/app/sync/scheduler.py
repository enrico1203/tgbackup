"""Supervisor for the sync jobs.

The `running` state lives in the database, not in memory: the same job can never start
twice in parallel even when its execution lasts days, and a process restart leaves no
ghost jobs stuck.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from datetime import datetime, timezone

from sqlalchemy import select, update

from ..config import settings
from ..db import SessionLocal
from ..models import JobRun, SyncJob
from .runner import StopSignal, execute_job

log = logging.getLogger(__name__)


class Scheduler:
    def __init__(self) -> None:
        self._tasks: dict[int, asyncio.Task] = {}
        self._cancels: dict[int, StopSignal] = {}
        self._loop_task: asyncio.Task | None = None

    async def reset_stale(self) -> None:
        """Brings back to idle the jobs left running by an abrupt process stop."""
        async with SessionLocal() as session:
            await session.execute(
                update(SyncJob).where(SyncJob.status == "running").values(status="idle", phase=None)
            )
            await session.execute(
                update(JobRun)
                .where(JobRun.status == "running")
                .values(status="stopped", finished_at=datetime.now(timezone.utc))
            )
            await session.commit()

    def is_running(self, job_id: int) -> bool:
        task = self._tasks.get(job_id)
        return task is not None and not task.done()

    async def trigger(self, job_id: int) -> bool:
        """Starts a job right away. Returns False if it was already running."""
        if self.is_running(job_id):
            return False

        async with SessionLocal() as session:
            job = await session.get(SyncJob, job_id)
            if job is None:
                return False
            job.status = "running"
            job.phase = "scan"
            await session.commit()

        self._spawn(job_id)
        return True

    def _spawn(self, job_id: int) -> None:
        cancel = StopSignal()
        self._cancels[job_id] = cancel

        async def wrapper() -> None:
            try:
                await execute_job(job_id, cancel)
            finally:
                self._tasks.pop(job_id, None)
                self._cancels.pop(job_id, None)

        self._tasks[job_id] = asyncio.create_task(wrapper(), name=f"job-{job_id}")

    async def stop(self, job_id: int) -> bool:
        cancel = self._cancels.get(job_id)
        if cancel is None:
            return False
        cancel.set("user")
        return True

    async def _tick(self) -> None:
        now = datetime.now(timezone.utc)
        async with SessionLocal() as session:
            result = await session.execute(
                select(SyncJob).where(
                    SyncJob.enabled.is_(True),
                    SyncJob.status == "idle",
                )
            )
            due = [
                job.id
                for job in result.scalars()
                if job.next_run_at is None or _as_utc(job.next_run_at) <= now
            ]

        for job_id in due:
            if self.is_running(job_id):
                continue
            log.info("Starting job %d", job_id)
            await self.trigger(job_id)

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
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


scheduler = Scheduler()
