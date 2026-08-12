"""Execution of a sync job: scan, diff, deletions, upload.

The goal is for the Telegram channel to be the exact mirror of the source. Every file
missing from the source is deleted from the channel using the stored message ids, and
every modified or renamed file is deleted and uploaded again.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import math
import os
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, select, update
from telethon.errors import FloodWaitError
from telethon.tl.types import DocumentAttributeFilename

from .. import notify
from ..db import SessionLocal
from ..models import (
    GUARD_MIN_ENTRIES,
    MTIME_UNKNOWN,
    Bot,
    BotSet,
    Channel,
    FileEntry,
    FilePart,
    JobRun,
    SyncJob,
    TelegramAccount,
    utcnow,
)
from ..telegram.bots import bot_set_budget, bots
from ..telegram.fast_transfer import call_with_timeout, upload_slice
from ..telegram.flood import FloodGate, FloodGroup
from ..telegram.manager import account_budget, manager
from ..telegram.throttle import limiter_for
from . import window
from .progress import WorkerProgress, hub
from .source import build_source

log = logging.getLogger(__name__)

DELETE_BATCH = 100


def part_plan(size: int, part_size: int) -> list[tuple[int, int]]:
    """Returns the slices (offset, length) a file has to be split into."""
    if size <= part_size:
        return [(0, size)]
    count = math.ceil(size / part_size)
    return [
        (index * part_size, min(part_size, size - index * part_size)) for index in range(count)
    ]


def build_caption(rel_path: str, name: str, index: int, total: int) -> str:
    """Message caption.

    Format fixed by the user:

        FileName: <file name>
        Path: <folder holding it, relative to the job root>

    The Part line only shows on split files, where it is needed to restore the order.
    Size and mtime are not in the caption: they live in the database.
    """
    folder = os.path.dirname(rel_path)
    lines = [f"FileName: {name}", f"Path: {folder}"]
    if total > 1:
        lines.append(f"Part: {index + 1}/{total}")
    # A message caption is limited to 1024 characters when media is attached.
    return "\n".join(lines)[:1024]


def part_file_name(name: str, index: int, total: int) -> str:
    if total == 1:
        return name
    width = len(str(total))
    return f"{name}.part{index + 1:0{width}d}"


class JobCancelled(Exception):
    pass


class DeleteGuardTripped(Exception):
    """The run wanted to delete more than the job allows, so it deleted nothing.

    A source that fails to mount is an empty folder, not an error: `list_files` returns
    nothing and the diff concludes, correctly as far as it can see, that every file has
    been removed. The channel would then be emptied message by message, and the messages
    are the backup. The guard is what turns that into a run that fails.
    """


def guard_verdict(removals: int, known: int, percent: int, files: int) -> str | None:
    """The reason the deletions must not go through, or None if they may.

    Both limits are off at zero and either one is enough to stop the run. The percentage
    is not applied to a job holding very few files, where any single deletion is a large
    share of the whole and the ratio says nothing.
    """
    if removals <= 0:
        return None
    if files > 0 and removals >= files:
        return f"{removals} files would be deleted, the limit for this job is {files}"
    if percent > 0 and known >= GUARD_MIN_ENTRIES:
        share = removals * 100 / known
        if share >= percent:
            return (
                f"{removals} of the {known} indexed files would be deleted, "
                f"that is {share:.0f}% against a limit of {percent}%"
            )
    return None


class StopSignal:
    """Request to stop a job, carrying the reason.

    The reason matters: if the job stops because the process is shutting down, or because
    its schedule window closed under it, the next run must not be pushed forward by a whole
    interval, otherwise every container restart would move the backup by hours and a night
    window would only ever get one run. If the user stopped it instead, the normal interval
    applies.
    """

    def __init__(self) -> None:
        self._event = asyncio.Event()
        self.reason = "user"

    def set(self, reason: str = "user") -> None:
        self.reason = reason
        self._event.set()

    def is_set(self) -> bool:
        return self._event.is_set()

    async def wait(self) -> None:
        """Blocks until a stop is asked for.

        The flag is read between one part and the next, which is enough while the bytes
        move and useless while a Telegram call hangs: there is no next part to reach. A
        transfer that can await this races the pending call against the button instead of
        against the ten minute timeout. See fast_transfer.call_with_timeout.
        """
        await self._event.wait()


class NoCarrier(Exception):
    """Nothing is left that can carry a file: every bot of the set has failed.

    Told apart from an ordinary upload failure on purpose. A file that fails is one file
    marked `error` and the run carries on; a transport that fails would mark every
    remaining file in turn, in the time it takes to try, and leave a job full of errors
    whose real cause was one dead token.
    """


class AccountTransport:
    """The way a job has always talked to Telegram: one account, one file at a time.

    The connection budget is the account's, divided among the jobs allowed to upload on it
    together, and there is one flood gate because there is one account to be limited.
    """

    kind = "account"

    def __init__(
        self, job_id: int, account: TelegramAccount | None, account_id: int, peer, cancel
    ) -> None:
        self.account_id = account_id
        self.label = account.label if account else str(account_id)
        self._peer = peer
        self.concurrency, self.max_connections = account_budget(account)
        # One file at a time: an account has one budget of twenty connections, and
        # splitting it across two files would only make both of them slower.
        self.workers = 1
        self.gate = FloodGate(f"job {job_id}", cancel=cancel)
        self.lock = manager.transfer_lock(account_id, self.concurrency)
        self.waiting_for = f"account {self.label}"

    async def control(self):
        """The client the deletions and the cleanup go through."""
        return await manager.get_client(self.account_id), self._peer

    @contextlib.asynccontextmanager
    async def carrier(self, worker: WorkerProgress):
        client = await manager.get_client(self.account_id)
        worker.label = self.label
        yield client, self._peer, self.gate, self.max_connections


class BotSetTransport:
    """N bots of one set, one file each.

    A bot is leased for the length of one file and handed back, never held for the run.
    That is what lets two jobs share a set without deadlocking, since a worker waits before
    it holds anything, and it is why a job configured for five parallel files on a set
    whose bots are busy simply uploads fewer at a time instead of failing.

    Each bot keeps its own flood gate, because each bot is its own account: one told to
    wait must not stop the other four, and the connection budget the gate lowers under a
    limit belongs to that bot alone. What the run reports is the group of them.
    """

    kind = "botset"

    def __init__(
        self,
        job_id: int,
        bot_set: BotSet,
        members: list[tuple[int, str]],
        tg_id: int,
        parallel_files: int,
        cancel,
    ) -> None:
        self.set_id = bot_set.id
        self.label = bot_set.name
        self.tg_id = tg_id
        self._labels = dict(members)
        self.valid = {bot_id for bot_id, _ in members}
        self.concurrency, self.max_connections = bot_set_budget(bot_set)
        # Zero means "as many as there are bots", which is what somebody who built a set of
        # five wants from it. More workers than bots would only queue on the pool.
        wanted = parallel_files if parallel_files > 0 else len(members)
        self.workers = max(1, min(wanted, len(members)))
        self._gates = {
            bot_id: FloodGate(f"job {job_id} on {label}", cancel=cancel)
            for bot_id, label in members
        }
        self.gate = FloodGroup(list(self._gates.values()))
        self.lock = bots.transfer_lock(self.set_id, self.concurrency)
        self.waiting_for = f"bot set {self.label}"
        bots.pool(self.set_id, [bot_id for bot_id, _ in members])

    async def control(self):
        """The first bot that answers, for the deletions and the cleanup.

        Deleting is not parallel work and one client is enough. It has to be a bot that is
        an admin able to delete, which is checked when the job is saved and said in the
        form, because a bot without that right uploads perfectly and then leaves every
        removed file in the channel for ever.
        """
        last: Exception | None = None
        for bot_id in list(self.valid):
            try:
                client = await bots.get_client(bot_id)
                return client, await bots.peer(bot_id, self.tg_id)
            except Exception as exc:
                last = exc
                log.warning("Bot %s is unusable: %s", self._labels.get(bot_id, bot_id), exc)
                self._retire(bot_id)
        raise NoCarrier(
            f"No bot of the set {self.label} could be used: {last}"
            if last
            else f"The set {self.label} has no usable bot"
        )

    def _retire(self, bot_id: int) -> None:
        self.valid.discard(bot_id)
        bots.drop_from_pool(self.set_id, bot_id)

    @contextlib.asynccontextmanager
    async def carrier(self, worker: WorkerProgress):
        while True:
            if not self.valid:
                raise NoCarrier(f"Every bot of the set {self.label} failed")
            bot_id = await bots.lease(self.set_id, self.valid)
            if bot_id is None:
                raise NoCarrier(f"Every bot of the set {self.label} failed")
            try:
                client = await bots.get_client(bot_id)
                peer = await bots.peer(bot_id, self.tg_id)
            except Exception as exc:
                # The bot is out for the rest of the run rather than for this file: a
                # token that was revoked or a bot removed from the channel will fail in
                # exactly the same way on every file that follows.
                log.warning(
                    "Bot %s dropped from this run: %s", self._labels.get(bot_id, bot_id), exc
                )
                self._retire(bot_id)
                continue

            worker.label = self._labels.get(bot_id, str(bot_id))
            try:
                yield client, peer, self._gates[bot_id], self.max_connections
            finally:
                bots.release(self.set_id, bot_id)
            return


async def build_transport(session, job: SyncJob, channel: Channel, cancel):
    """Whoever is going to carry this job: its account, or the bots of its set."""
    if job.bot_set_id is not None:
        bot_set = await session.get(BotSet, job.bot_set_id)
        if bot_set is None:
            raise RuntimeError("The bot set of the job no longer exists")
        result = await session.execute(
            select(Bot).where(Bot.bot_set_id == bot_set.id, Bot.enabled.is_(True))
        )
        members = [
            (bot.id, bot.username or bot.first_name or f"bot {bot.id}")
            for bot in result.scalars()
        ]
        if not members:
            raise RuntimeError(
                f"The bot set {bot_set.name} has no enabled bot: add one, or move the "
                "job onto an account"
            )
        return BotSetTransport(
            job.id, bot_set, members, channel.tg_id, job.parallel_files, cancel
        )

    if job.account_id is None:
        raise RuntimeError("The job has neither a Telegram account nor a bot set")
    account = await session.get(TelegramAccount, job.account_id)
    return AccountTransport(
        job.id, account, job.account_id, manager.input_peer(channel), cancel
    )


class JobRunner:
    def __init__(self, job_id: int, cancel: StopSignal) -> None:
        self.job_id = job_id
        self.cancel = cancel
        # One process and one event loop, so a lock is all it takes to stop two workers
        # claiming the same file: the select and the commit that marks it `uploading` have
        # to be one step.
        self._claim = asyncio.Lock()

    def _check_cancel(self) -> None:
        if self.cancel.is_set():
            raise JobCancelled()

    async def run(self) -> None:
        async with SessionLocal() as session:
            job = await session.get(SyncJob, self.job_id)
            if job is None:
                return
            channel = await session.get(Channel, job.channel_id)
            if channel is None:
                raise RuntimeError("The job channel no longer exists")

            run = JobRun(job_id=job.id)
            session.add(run)
            job.last_run_at = utcnow()
            job.status = "running"
            job.phase = "scan"
            job.last_error = None
            await session.commit()
            run_id = run.id
            job_name = job.name
            part_size = job.part_size_bytes
            trash_days = job.trash_days
            source = build_source(job)

            # Who carries the bytes, decided once: one account and one file at a time, or
            # the bots of a set and one file each. Everything below asks the transport
            # rather than the account, which is what makes the two paths one run.
            transport = await build_transport(session, job, channel, self.cancel)

            # The ceiling of this job under the one of the installation, following the
            # hours of the window: the provider is read as the bucket refills, so a run
            # that crosses into a throttled hour slows down where it is.
            zone = window.load_zone(await window.load_timezone(session))
            limiter = limiter_for(
                window.rate_provider(job.throttle_bps, job.schedule_hours, zone)
            )

        progress = hub.start_job(self.job_id, job_name)
        progress.phase = "scan"
        # The gate of the whole run, so the backoff climbs across files instead of starting
        # again at thirty seconds on every one of them, and the interface can say that a
        # job at zero bytes is being held rather than being slow. On a bot set it is the
        # group of the gates, one per bot, read as one.
        flood = transport.gate
        progress.flood = flood

        try:
            client, entity = await transport.control()

            counters = await self._diff(source, run_id, progress)
            self._check_cancel()

            progress.phase = "delete"
            progress.scanned_where = None
            await self._set_phase("delete")
            purged = await self._purge_trash(trash_days)
            if purged:
                log.info(
                    "Job %d: %d files out of the trash have passed %d days",
                    self.job_id, purged, trash_days,
                )
            removed = await self._apply_deletions(client, entity)

            # Two jobs on the same account would each open 20 connections and exceed the
            # per data center ceiling, blocking each other: the upload phase is serialized
            # per account, and per bot set for the same reason, its bots being shared.
            # Only this phase, though: scanning and cleanup can go on in parallel, and
            # waiting has a phase of its own so a queued job does not look stuck.
            lock = transport.lock
            if lock.locked():
                progress.phase = "waiting"
                await self._set_phase("waiting")
                log.info(
                    "Job %d waiting for %s to upload", self.job_id, transport.waiting_for
                )

            async with lock:
                self._check_cancel()
                progress.phase = "upload"
                await self._set_phase("upload")
                uploaded_files, uploaded_bytes = await self._upload_pending(
                    transport, part_size, progress, source, limiter
                )

            async with SessionLocal() as session:
                run = await session.get(JobRun, run_id)
                run.status = "ok"
                run.finished_at = utcnow()
                run.scanned = counters["scanned"]
                run.added = counters["added"]
                run.modified = counters["modified"]
                run.trashed = counters["trashed"]
                run.revived = counters["revived"]
                run.removed = removed
                run.uploaded_files = uploaded_files
                run.uploaded_bytes = uploaded_bytes
                await session.commit()

        except JobCancelled:
            async with SessionLocal() as session:
                run = await session.get(JobRun, run_id)
                if run is not None:
                    run.status = "stopped"
                    run.finished_at = utcnow()
                    await session.commit()
            raise
        except Exception as exc:
            # Without this the row stays "running" for ever, with no end date and no
            # reason, until a restart sweeps it: the Runs page would show a failed run
            # as one still going. The job status is set by the caller, this is the run.
            async with SessionLocal() as session:
                run = await session.get(JobRun, run_id)
                if run is not None:
                    run.status = "error"
                    run.finished_at = utcnow()
                    run.error = str(exc)[:1000]
                    await session.commit()
            raise
        finally:
            # Whatever ended the run, what Telegram did to it while it lasted is worth
            # keeping: once it is over, a run that was cut every three minutes and one
            # that flew look exactly the same, and this is the answer to why last night
            # took four hours. Written here so all three endings carry it.
            if flood.events:
                async with SessionLocal() as session:
                    run = await session.get(JobRun, run_id)
                    if run is not None:
                        run.limited_events = flood.events
                        await session.commit()
            hub.end_job(self.job_id)

    async def _set_phase(self, phase: str) -> None:
        async with SessionLocal() as session:
            job = await session.get(SyncJob, self.job_id)
            if job is not None:
                job.phase = phase
                await session.commit()

    # Scanning and comparison

    async def _diff(self, source, run_id: int, progress) -> dict:
        def report(files: int, dirs: int, total_bytes: int, where: str) -> None:
            progress.scanned_files = files
            progress.scanned_dirs = dirs
            progress.scanned_bytes = total_bytes
            progress.scanned_where = where or None

        found = await source.list_files(on_progress=report)
        self._check_cancel()

        progress.phase = "diff"
        progress.scanned_where = None
        await self._set_phase("diff")

        on_disk = {item.rel_path: item for item in found}

        added = 0
        modified = 0
        revived = 0

        async with SessionLocal() as session:
            result = await session.execute(
                select(FileEntry).where(FileEntry.job_id == self.job_id)
            )
            known = {entry.rel_path: entry for entry in result.scalars()}

            for rel_path, item in on_disk.items():
                entry = known.get(rel_path)
                if entry is not None and entry.state == "trashed":
                    # Back at the source before the retention ran out. Its messages were
                    # never deleted, so a file put back exactly as it was costs nothing:
                    # the entry returns to `uploaded` and no byte travels. Only a file
                    # that came back different has to be uploaded again.
                    entry.trashed_at = None
                    entry.error = None
                    revived += 1
                    if entry.size == item.size and entry.mtime_ns in (
                        item.mtime_ns,
                        MTIME_UNKNOWN,
                    ):
                        entry.mtime_ns = item.mtime_ns
                        entry.name = item.name
                        entry.state = "uploaded"
                    else:
                        entry.size = item.size
                        entry.mtime_ns = item.mtime_ns
                        entry.name = item.name
                        entry.state = "stale"
                        modified += 1
                elif entry is None:
                    session.add(
                        FileEntry(
                            job_id=self.job_id,
                            rel_path=item.rel_path,
                            name=item.name,
                            size=item.size,
                            mtime_ns=item.mtime_ns,
                            state="pending",
                        )
                    )
                    added += 1
                elif entry.mtime_ns == MTIME_UNKNOWN and entry.size == item.size:
                    # Rebuilt by reading the channel: the messages carry the name, the
                    # folder and the part number, never the date. The file is up there and
                    # is the right size, so the date of the source is adopted instead of
                    # deleting the whole thing and uploading it again for a field that was
                    # never recorded.
                    entry.mtime_ns = item.mtime_ns
                    entry.name = item.name
                elif entry.size != item.size or entry.mtime_ns != item.mtime_ns:
                    # Content changed: the old parts on Telegram are no longer valid.
                    entry.size = item.size
                    entry.mtime_ns = item.mtime_ns
                    entry.name = item.name
                    entry.state = "stale"
                    entry.error = None
                    modified += 1
                elif entry.state in ("error", "uploading"):
                    # Retry for files left half done on the previous round, by error or by
                    # a process stop. They go through stale, not pending: the parts already
                    # sent are recorded and must first be deleted from the channel, or they
                    # would stay as orphan messages.
                    entry.state = "stale"
                    entry.error = None

            # Only the files that disappeared during this run. One already in the trash,
            # or already marked for deletion by a run that was interrupted before it got
            # there, has been counted once and must not be counted again: otherwise a
            # legitimate large deletion would trip the guard on every run for as long as
            # the retention lasts.
            removals = [
                entry
                for rel_path, entry in known.items()
                if rel_path not in on_disk and entry.state not in ("trashed", "to_delete")
            ]

            # Before anything is written: the guard has to be able to leave the index
            # exactly as it found it. Raising here rolls the whole transaction back, so a
            # run stopped at this point has neither marked a deletion nor recorded a new
            # file, and running it again once the source is back gives the same answer it
            # would have given all along.
            job = await session.get(SyncJob, self.job_id)
            reason = guard_verdict(
                len(removals),
                len(known),
                job.delete_guard_percent if job else 0,
                job.delete_guard_files if job else 0,
            )
            if reason is not None and job is not None and job.delete_guard_bypass:
                log.warning(
                    "Job %d: deletion guard bypassed by the user (%s)", self.job_id, reason
                )
                reason = None
            if reason is not None:
                raise DeleteGuardTripped(reason)
            if job is not None and job.delete_guard_bypass:
                # Consumed whether or not it was needed: an acknowledgement is worth one
                # run, or it would sit there disarming every run that follows.
                job.delete_guard_bypass = False

            trash_days = job.trash_days if job else 0
            trashed = 0
            now = utcnow()
            for entry in removals:
                if trash_days > 0 and entry.state == "uploaded":
                    # Only a file that actually reached the channel is worth keeping: one
                    # still pending has no message to hold on to, so there is nothing the
                    # trash could give back and it goes straight out.
                    entry.state = "trashed"
                    entry.trashed_at = now
                    trashed += 1
                else:
                    entry.state = "to_delete"

            run = await session.get(JobRun, run_id)
            if run is not None:
                run.scanned = len(on_disk)
                run.added = added
                run.modified = modified
                run.trashed = trashed
                run.revived = revived
            await session.commit()

        return {
            "scanned": len(on_disk),
            "added": added,
            "modified": modified,
            "trashed": trashed,
            "revived": revived,
        }

    async def _purge_trash(self, trash_days: int) -> int:
        """Sends the expired trash on to the deletion phase. Returns how many.

        Nothing is deleted here: the entries become `to_delete` and the existing machinery
        removes the messages and the rows, which is the only place in the application that
        deletes from a channel. With the retention set back to zero the whole trash
        expires at once, which is what turning the feature off has to mean.
        """
        cutoff = utcnow() - timedelta(days=trash_days)
        async with SessionLocal() as session:
            result = await session.execute(
                update(FileEntry)
                .where(
                    FileEntry.job_id == self.job_id,
                    FileEntry.state == "trashed",
                    FileEntry.trashed_at.is_not(None),
                    FileEntry.trashed_at < cutoff,
                )
                .values(state="to_delete")
            )
            await session.commit()
            return result.rowcount or 0

    # Deletions

    async def _apply_deletions(self, client, entity) -> int:
        """Deletes from Telegram what no longer exists at the source or needs re-uploading."""
        removed = 0
        while True:
            self._check_cancel()
            async with SessionLocal() as session:
                result = await session.execute(
                    select(FileEntry)
                    .where(
                        FileEntry.job_id == self.job_id,
                        FileEntry.state.in_(("to_delete", "stale")),
                    )
                    .limit(DELETE_BATCH)
                )
                entries = list(result.scalars())
                if not entries:
                    return removed

                message_ids: list[int] = []
                for entry in entries:
                    parts = await session.execute(
                        select(FilePart).where(FilePart.file_id == entry.id)
                    )
                    message_ids.extend(part.message_id for part in parts.scalars())

                if message_ids:
                    await self._delete_messages(client, entity, message_ids)

                for entry in entries:
                    await session.execute(
                        delete(FilePart).where(FilePart.file_id == entry.id)
                    )
                    if entry.state == "to_delete":
                        await session.delete(entry)
                        removed += 1
                    else:
                        # The file still exists at the source, it only needs re-uploading.
                        entry.state = "pending"
                        entry.parts_total = 1
                await session.commit()

    async def _delete_messages(self, client, entity, message_ids: list[int]) -> None:
        for start in range(0, len(message_ids), DELETE_BATCH):
            chunk = message_ids[start : start + DELETE_BATCH]
            try:
                await client.delete_messages(entity, chunk)
            except FloodWaitError as exc:
                log.warning("Flood wait of %ss while deleting", exc.seconds)
                await asyncio.sleep(exc.seconds + 1)
                await client.delete_messages(entity, chunk)
            except Exception as exc:
                # A message already gone from the channel must not block the job.
                log.warning("Deleting %d messages failed: %s", len(chunk), exc)

    # Upload

    async def _upload_pending(
        self, transport, part_size: int, progress, source, limiter
    ) -> tuple[int, int]:
        """Uploads everything pending, one file per worker at a time.

        An account job has one worker and behaves exactly as it always has. A bot set job
        has as many as its bots, each carrying a different file on a different account, so
        what changes is not how a file is uploaded but how many are in flight.
        """
        async with SessionLocal() as session:
            totals = await session.execute(
                select(func.count(FileEntry.id), func.coalesce(func.sum(FileEntry.size), 0)).where(
                    FileEntry.job_id == self.job_id, FileEntry.state == "pending"
                )
            )
            count, total_bytes = totals.one()

        progress.files_total = count
        progress.bytes_total = total_bytes
        progress.files_done = 0
        progress.bytes_done = 0

        workers = [
            WorkerProgress(slot=index, label=transport.label)
            for index in range(transport.workers)
        ]
        progress.workers = workers
        if transport.workers > 1:
            log.info(
                "Job %d uploads %d files at a time on %s",
                self.job_id, transport.workers, transport.waiting_for,
            )

        tasks = [
            asyncio.create_task(
                self._upload_worker(transport, worker, part_size, progress, source, limiter),
                name=f"job-{self.job_id}-upload-{worker.slot}",
            )
            for worker in workers
        ]
        try:
            results = await asyncio.gather(*tasks)
        except BaseException:
            # One worker failed or the run was stopped: the others are carrying a file
            # each and would go on uploading after the run has been written off. They are
            # cancelled and awaited, which puts their entries back through the same path a
            # stop takes, and nothing is lost since every part sent is already recorded.
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise
        finally:
            progress.workers = []

        return sum(item[0] for item in results), sum(item[1] for item in results)

    async def _claim_next(self) -> tuple[int, str, str, int] | None:
        """Takes the next pending file, marking it `uploading` so no other worker can.

        The lock is what makes the read and the write one step. Two workers selecting the
        same row and both committing it would upload the same file twice, into two sets of
        messages, of which only the last would be recorded and the first would stay in the
        channel with nothing pointing at it.
        """
        async with self._claim:
            async with SessionLocal() as session:
                result = await session.execute(
                    select(FileEntry)
                    .where(FileEntry.job_id == self.job_id, FileEntry.state == "pending")
                    .order_by(FileEntry.id)
                    .limit(1)
                )
                entry = result.scalar_one_or_none()
                if entry is None:
                    return None
                entry.state = "uploading"
                await session.commit()
                return entry.id, entry.rel_path, entry.name, entry.size

    async def _release(self, file_id: int) -> None:
        """Puts a claimed file back, when the transport could not take it after all."""
        async with SessionLocal() as session:
            entry = await session.get(FileEntry, file_id)
            if entry is not None and entry.state == "uploading":
                entry.state = "pending"
                await session.commit()

    async def _upload_worker(
        self, transport, worker: WorkerProgress, part_size: int, progress, source, limiter
    ) -> tuple[int, int]:
        uploaded_files = 0
        uploaded_bytes = 0

        while True:
            self._check_cancel()
            claimed = await self._claim_next()
            if claimed is None:
                worker.current_file = None
                return uploaded_files, uploaded_bytes
            file_id, rel_path, name, size = claimed

            worker.current_file = rel_path
            progress.current_file = rel_path

            try:
                async with transport.carrier(worker) as (
                    client, entity, flood, max_connections,
                ):
                    try:
                        sent = await self._upload_one(
                            client, entity, source, rel_path, name, size, part_size,
                            progress, worker, file_id, max_connections, limiter, flood,
                        )
                    except JobCancelled:
                        async with SessionLocal() as session:
                            entry = await session.get(FileEntry, file_id)
                            if entry is not None and entry.state == "uploading":
                                # If any part had already gone out it must be deleted next
                                # round, otherwise it would stay orphaned in the channel.
                                has_parts = await session.scalar(
                                    select(func.count(FilePart.id)).where(
                                        FilePart.file_id == file_id
                                    )
                                )
                                entry.state = "stale" if has_parts else "pending"
                                await session.commit()
                        raise
                    except Exception as exc:
                        log.exception("Upload of %s failed", rel_path)
                        async with SessionLocal() as session:
                            entry = await session.get(FileEntry, file_id)
                            if entry is not None:
                                entry.state = "error"
                                entry.error = str(exc)[:1000]
                                await session.commit()
                        progress.files_done += 1
                        worker.current_file = None
                        continue
            except NoCarrier:
                # Not this file's fault and not a reason to mark it: it goes back to
                # pending and the run fails as a whole, which is the honest answer when
                # there is nothing left to upload with.
                await self._release(file_id)
                worker.current_file = None
                raise

            async with SessionLocal() as session:
                entry = await session.get(FileEntry, file_id)
                if entry is not None:
                    entry.state = "uploaded"
                    entry.parts_total = sent
                    entry.uploaded_at = utcnow()
                    entry.error = None
                    await session.commit()

            uploaded_files += 1
            uploaded_bytes += size
            progress.files_done += 1
            worker.current_file = None

    async def _upload_one(
        self,
        client,
        entity,
        source,
        rel_path: str,
        name: str,
        size: int,
        part_size: int,
        progress,
        worker: WorkerProgress,
        file_id: int,
        max_connections: int,
        limiter,
        flood,
    ) -> int:
        """Uploads a file, splitting it above the threshold. Returns the number of parts.

        Every part is recorded in the database as soon as its message has been sent: if the
        process dies in the middle of a large file, the message id is already stored and on
        the next round the part is deleted instead of staying orphaned in the channel.
        """
        slices = part_plan(size, part_size)
        progress.current_parts = len(slices)
        worker.current_parts = len(slices)

        for index, (offset, length) in enumerate(slices):
            self._check_cancel()
            progress.current_part = index + 1
            worker.current_part = index + 1
            file_name = part_file_name(name, index, len(slices))

            handle = await self._upload_with_retry(
                client, source, rel_path, offset, length, file_name, progress,
                max_connections, limiter, flood,
            )

            caption = build_caption(rel_path, name, index, len(slices))
            message = await self._publish_part(
                client, entity, handle, caption, file_name, flood
            )

            async with SessionLocal() as session:
                session.add(
                    FilePart(
                        file_id=file_id,
                        part_index=index,
                        offset=offset,
                        size=length,
                        message_id=message.id,
                    )
                )
                await session.commit()

        return len(slices)

    async def _publish_part(self, client, entity, handle, caption, file_name: str, flood):
        """Posts the message that turns an uploaded part into a file in the channel.

        The bytes are already on the server, so this is only the message, which is why the
        timeout is generous by a wide margin. It is here because it is the last call of a
        part that could hang for ever, and one that hangs here stops the job just as surely
        as one that hangs while uploading.

        It goes through the same gate as the parts: a limited account is limited on this
        call too, and a part whose bytes made it through only to lose the message would be
        deleted and uploaded again from nothing on the next run.
        """
        while True:
            if flood is not None:
                await flood.hold()
            try:
                return await call_with_timeout(
                    client.send_file(
                        entity,
                        handle,
                        caption=caption,
                        # Photos and videos must stay documents: no recompression, no
                        # quality loss, bytes identical to the original.
                        force_document=True,
                        attributes=[DocumentAttributeFilename(file_name)],
                    ),
                    f"the message carrying {file_name}",
                    self.cancel,
                )
            except asyncio.CancelledError:
                # Same conversion the upload retry makes: a stop has to leave through the
                # door the run knows, or the status of the job would never be written back
                # and it would stay "running" with nothing running.
                raise JobCancelled() from None
            except FloodWaitError as exc:
                if flood is None:
                    raise
                await flood.flooded(exc.seconds)

    async def _upload_with_retry(
        self, client, source, rel_path: str, offset: int, length: int, file_name: str,
        progress, max_connections: int, limiter, flood,
    ):
        attempt = 0
        while True:
            attempt += 1
            try:
                # The reader is rebuilt on every attempt: on a remote that means reopening
                # the ranged request from the right point.
                return await upload_slice(
                    client,
                    source.reader(rel_path, offset, length),
                    length,
                    file_name,
                    on_progress=progress.add_bytes,
                    cancel=self.cancel,
                    source=f"{source.label}/{rel_path}",
                    max_connections=max_connections,
                    limiter=limiter,
                    flood=flood,
                )
            except FloodWaitError as exc:
                # The senders answer a flood wait by themselves and never let one end a
                # slice, so this is left for a limit met outside them, on opening the
                # connections. The attempt is not counted: waiting is not failing.
                attempt -= 1
                await flood.flooded(exc.seconds)
            except asyncio.CancelledError:
                raise JobCancelled() from None
            except Exception as exc:
                if attempt >= 3:
                    raise
                # With the reason: an attempt that says only that it is starting again
                # leaves the log unable to answer why a file took four times as long.
                log.warning(
                    "Retrying the upload of %s (attempt %d): %s: %s",
                    file_name, attempt + 1, type(exc).__name__, exc,
                )
                # The bytes of the failed attempt were already counted: the slice restarts
                # from zero and the speed estimate settles again by itself.
                await asyncio.sleep(5 * attempt)


async def execute_job(job_id: int, cancel: StopSignal) -> None:
    """Runs a job and schedules the next run starting from the end of this one."""
    runner = JobRunner(job_id, cancel)

    status = "idle"
    error: str | None = None
    # Stopped by the system rather than by the user: shutdown, or the schedule window
    # closing. In both cases the run is being put back where it was, not finished.
    interrupted_by_system = False
    try:
        # No semaphore here: serializing the whole execution would stop a job from even
        # scanning while another one on the same account uploads, and with runs lasting
        # days it would sit still for days. The semaphore lives inside run(), around the
        # upload phase alone.
        await runner.run()
    except JobCancelled:
        interrupted_by_system = cancel.reason in ("shutdown", "window")
        log.info(
            "Job %d interrupted (%s)",
            job_id,
            cancel.reason if interrupted_by_system else "user request",
        )
    except DeleteGuardTripped as exc:
        # Not an unexpected failure: the run did exactly what it was told to do, which
        # was to stop. The message has to say what to look at and what to press, because
        # this is the one error whose right answer is sometimes "yes, go ahead".
        log.warning("Job %d stopped by the deletion guard: %s", job_id, exc)
        status = "error"
        error = (
            f"Deletion guard: {exc}. Nothing was deleted and the index was left "
            "untouched. Check that the source is mounted and points where it should, "
            "then either fix it or allow the deletion for one run."
        )
    except Exception as exc:
        log.exception("Job %d failed", job_id)
        status = "error"
        error = str(exc)[:1000]

    async with SessionLocal() as session:
        job = await session.get(SyncJob, job_id)
        if job is not None:
            job.status = status
            job.phase = None
            job.last_error = error
            job.last_finished_at = utcnow()
            if status == "idle":
                # A run that went through is the answer to the silence alarm: whatever was
                # reported is over, and the next silence starts counting again from here.
                job.silence_alerted_at = None
            if not interrupted_by_system:
                # The interval starts from the end: a job running for three days does not
                # pile up missed executions to catch up all at once.
                job.next_run_at = datetime.now(UTC) + timedelta(
                    hours=job.interval_hours
                )
            # If shutdown or the window stopped it, next_run_at stays as it was: the job
            # resumes on restart, or when the window opens again, instead of skipping a
            # whole interval.
            await session.commit()

    if not interrupted_by_system:
        # No report when the process is going down or the window closed: the job has not
        # finished, it is being put back where it was, and a message saying it stopped
        # would be noise on every deploy and every morning.
        await _report(job_id, status, error)


async def _report(job_id: int, status: str, error: str | None) -> None:
    """Sends the run report, reading the counters back from the run that just ended."""
    async with SessionLocal() as session:
        job = await session.get(SyncJob, job_id)
        if job is None:
            return
        channel = await session.get(Channel, job.channel_id)
        run = await session.scalar(
            select(JobRun).where(JobRun.job_id == job_id).order_by(JobRun.id.desc()).limit(1)
        )
        failed_files = await session.scalar(
            select(func.count(FileEntry.id)).where(
                FileEntry.job_id == job_id, FileEntry.state == "error"
            )
        )
        source = job.remote if job.source_type == "rclone" else job.local_path
        account_id = job.account_id
        carrier = ""
        if job.bot_set_id is not None:
            bot_set = await session.get(BotSet, job.bot_set_id)
            carrier = bot_set.name if bot_set else f"bot set {job.bot_set_id}"

    outcome = {"idle": "completed", "error": "failed"}.get(status, status)
    lines = [
        f"Source: {source}",
        f"Channel: {channel.title if channel else 'unknown'}",
    ]
    if carrier:
        # Worth naming: a job on a bot set is reported through somebody else's account,
        # so without this the message would arrive with nothing saying who uploaded it.
        lines.append(f"Uploaded by the bot set {carrier}")
    if run is not None:
        lines.append(
            f"Examined {run.scanned}, new {run.added}, modified {run.modified}, "
            f"removed {run.removed}"
        )
        if run.trashed or run.revived:
            # Only when something happened: a job without a trash would otherwise carry
            # two zeroes in every report it ever sends.
            lines.append(
                f"Moved to the trash {run.trashed}, brought back {run.revived}"
            )
        lines.append(
            f"Uploaded {run.uploaded_files} files, {notify.format_bytes(run.uploaded_bytes)}"
        )
        lines.append(
            f"Run started {run.started_at:%Y-%m-%d %H:%M} UTC, lasted "
            f"{notify.format_duration(run.started_at, run.finished_at or utcnow())}"
        )
        if run.limited_events:
            # Said out loud in the report, because a run that took four hours because
            # Telegram was holding the account back reads as a slow run and nothing else.
            lines.append(
                f"Telegram held the account back {run.limited_events} times during this run"
            )
    if failed_files:
        lines.append(f"Files in error: {failed_files}")
    if error:
        lines.append(f"Error: {error}")

    await notify.send_report(
        account_id=account_id,
        title=f"sync job {job.name}",
        outcome=outcome,
        lines=lines,
        failed=status == "error" or bool(failed_files),
    )
