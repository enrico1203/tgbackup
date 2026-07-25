"""Supervisore dei sync job.

Lo stato `running` sta nel database, non in memoria: lo stesso job non puo mai partire
due volte in parallelo neanche se la sua esecuzione dura giorni, e un riavvio del
processo non lascia job fantasma bloccati.
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
        """Riporta a idle i job rimasti running da un arresto brusco del processo."""
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
        """Avvia subito un job. Ritorna False se era gia in esecuzione."""
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
            log.info("Avvio del job %d", job_id)
            await self.trigger(job_id)

    async def _loop(self) -> None:
        while True:
            try:
                await self._tick()
            except Exception:
                log.exception("Errore nel giro dello scheduler")
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
            # Motivo esplicito: cosi i job fermati dallo spegnimento non si spostano
            # avanti di un intervallo intero e al riavvio riprendono subito.
            cancel.set("shutdown")
        tasks = list(self._tasks.values())
        if tasks:
            # I job in corso vengono avvisati e si fermano al prossimo punto di controllo.
            await asyncio.gather(*tasks, return_exceptions=True)


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


scheduler = Scheduler()
