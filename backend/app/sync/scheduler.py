"""Supervisor for the sync and download jobs.

The `running` state lives in the database, not in memory: the same job can never start
twice in parallel even when its execution lasts days, and a process restart leaves no
ghost jobs stuck.

Sync jobs and download jobs are two tables with two independent numbering schemes, so
everything here is keyed by the pair (kind, id): job 3 and download job 3 are two
different jobs and must be able to run at the same time.

The schedule window (see window.py) is checked here and only here, and only for runs the
scheduler starts: pressing Run is an explicit order and runs the job whatever the hour.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, update

from .. import maintenance, notify
from ..config import settings
from ..db import SessionLocal
from ..models import Channel, DownloadJob, DownloadRun, JobRun, SyncJob
from . import window
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
        # The hourly checks: silence alarm and scheduled maintenance. Kept apart from the
        # 10 s tick, which has to stay cheap.
        self._slow_task: asyncio.Task | None = None

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
        closing: list[Key] = []
        async with SessionLocal() as session:
            zone = window.load_zone(await window.load_timezone(session))
            for kind, model in _MODELS.items():
                result = await session.execute(
                    select(model).where(
                        model.enabled.is_(True), model.status.in_(("idle", "running"))
                    )
                )
                for job in result.scalars():
                    open_now = window.is_open(job.schedule_hours, zone, now)
                    if job.status == "running":
                        # A run started inside the window and still going when it closes
                        # is stopped only if the job asks for it. Its next_run_at is not
                        # moved, so it starts again at the next opening.
                        if job.stop_outside_window and not open_now:
                            closing.append((kind, job.id))
                        continue
                    if not open_now:
                        # Due but outside its hours: it waits for the opening instead of
                        # losing the run.
                        continue
                    if job.next_run_at is None or _as_utc(job.next_run_at) <= now:
                        due.append((kind, job.id))

        for kind, job_id in closing:
            cancel = self._cancels.get((kind, job_id))
            if cancel is None or cancel.is_set():
                continue
            log.info("Stopping %s job %d: the schedule window closed", kind, job_id)
            cancel.set("window")

        for kind, job_id in due:
            if self.is_running(kind, job_id):
                continue
            log.info("Starting %s job %d", kind, job_id)
            await self.trigger(kind, job_id)

    # The silence alarm

    async def _check_silence(self) -> None:
        """Reports the jobs that have not finished a run successfully in too long.

        A job that fails the same way every night, or one disabled by mistake, says
        nothing on its own: the interface has to be opened for anybody to find out, which
        means finding out late. The threshold adapts to the job, because three days of
        silence mean nothing to a monthly job and are an eternity for an hourly one: it is
        the longer of the configured days and twice the interval of the job.

        Disabled jobs are included on purpose. That is the case the alarm exists for.
        """
        now = datetime.now(UTC)
        async with SessionLocal() as session:
            silence_days = await notify.load_silence_days(session)
            if silence_days <= 0:
                return

            alerts: list[tuple[int, str, list[str]]] = []
            for kind, model in _MODELS.items():
                run_model = _RUNS[kind]
                result = await session.execute(
                    select(model).where(model.silence_alerts.is_(True))
                )
                for job in result.scalars():
                    if job.status == "running":
                        continue

                    last_ok = await session.scalar(
                        select(func.max(run_model.finished_at)).where(
                            run_model.job_id == job.id, run_model.status == "ok"
                        )
                    )
                    # A job that has never succeeded is counted from its creation, so one
                    # that has been failing since the day it was made is reported too.
                    since = _as_utc(last_ok) if last_ok else _as_utc(job.created_at)
                    threshold = timedelta(
                        hours=max(silence_days * 24, job.interval_hours * 2)
                    )
                    if now - since < threshold:
                        continue

                    # Already reported, and not long enough ago to say it again. Repeating
                    # once per threshold is what keeps a job that stays broken from
                    # sending a message every hour.
                    alerted = job.silence_alerted_at
                    if alerted is not None and now - _as_utc(alerted) < threshold:
                        continue

                    silent_for = notify.format_duration(since, now)
                    lines = [
                        f"No successful run for {silent_for}"
                        + ("" if last_ok else ", not one since it was created"),
                        f"The job is {'disabled' if not job.enabled else 'enabled'}"
                        + (", status error" if job.status == "error" else ""),
                    ]
                    if job.last_error:
                        lines.append(f"Last error: {job.last_error[:300]}")
                    lines.append(
                        "Turn the alarm off on the job itself if this one is meant to be quiet."
                    )
                    alerts.append(
                        (
                            job.account_id,
                            f"{'sync' if kind == SYNC else 'download'} job {job.name} is silent",
                            lines,
                        )
                    )
                    job.silence_alerted_at = now

            await session.commit()

        # Outside the session: sending goes through Telegram and can take a while, and it
        # must not hold a transaction open while it does.
        for account_id, title, lines in alerts:
            log.warning("Silence alarm: %s", title)
            await notify.send_alert(account_id, title, lines)

    async def _loop(self) -> None:
        while True:
            try:
                await self._tick()
            except Exception:
                log.exception("Error in the scheduler tick")
            await asyncio.sleep(settings.scheduler_tick_seconds)

    # Scheduled maintenance

    async def _check_channels(self) -> None:
        """Starts the integrity checks that have come due.

        The rules are the ones the manual endpoint already enforces, for the same reasons:
        never two tasks on one channel, and never a repair while a sync job is writing the
        very index it would change. A check that only reports is allowed at any time, so
        an unattended one never has to wait for a job to finish.
        """
        now = datetime.now(UTC)
        async with SessionLocal() as session:
            zone = window.load_zone(await window.load_timezone(session))
            local_hour = now.astimezone(zone).hour

            result = await session.execute(
                select(Channel).where(Channel.check_interval_days > 0)
            )
            for channel in result.scalars():
                if channel.check_hour != local_hour:
                    continue
                if channel.last_check_at is not None:
                    due_after = _as_utc(channel.last_check_at) + timedelta(
                        days=channel.check_interval_days
                    )
                    # Slightly early is fine: the tick is hourly and the interval is
                    # counted in days, so demanding the full day would push every check
                    # one day later each time it ran.
                    if now + timedelta(hours=1) < due_after:
                        continue
                if maintenance.registry.active_on(channel.id) is not None:
                    continue

                repair = channel.check_repair
                if repair and await maintenance.running_job_on(session, channel.id):
                    # The index this would change is being written right now. Reporting is
                    # still worth doing, so the check runs without the repair.
                    log.info(
                        "Scheduled check of channel %d runs without repair, a job is running",
                        channel.id,
                    )
                    repair = False

                log.info("Starting the scheduled check of channel %d", channel.id)
                maintenance.registry.start(
                    "check",
                    channel,
                    lambda task, channel_id=channel.id, repair=repair: (
                        maintenance.scheduled_check(task, channel_id, repair)
                    ),
                )

    async def _slow_loop(self) -> None:
        """The checks that belong on a clock of hours, not of seconds."""
        while True:
            try:
                await self._check_silence()
            except Exception:
                log.exception("Error in the silence check")
            try:
                await self._check_channels()
            except Exception:
                log.exception("Error in the scheduled maintenance check")
            await asyncio.sleep(settings.watcher_tick_seconds)

    async def start(self) -> None:
        await self.reset_stale()
        self._loop_task = asyncio.create_task(self._loop(), name="scheduler")
        self._slow_task = asyncio.create_task(self._slow_loop(), name="watcher")

    async def shutdown(self) -> None:
        for task in (self._loop_task, self._slow_task):
            if task is None:
                continue
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._loop_task = None
        self._slow_task = None

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
