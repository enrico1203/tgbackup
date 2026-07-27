"""Execution of a sync job: scan, diff, deletions, upload.

The goal is for the Telegram channel to be the exact mirror of the source. Every file
missing from the source is deleted from the channel using the stored message ids, and
every modified or renamed file is deleted and uploaded again.
"""

from __future__ import annotations

import asyncio
import logging
import math
import os
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, select
from telethon.errors import FloodWaitError
from telethon.tl.types import DocumentAttributeFilename

from ..db import SessionLocal
from ..models import Channel, FileEntry, FilePart, JobRun, SyncJob, TelegramAccount, utcnow
from ..telegram.fast_transfer import MAX_CONNECTIONS, upload_slice
from ..telegram.manager import manager
from .progress import hub
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


class StopSignal:
    """Request to stop a job, carrying the reason.

    The reason matters: if the job stops because the process is shutting down, the next
    run must not be pushed forward by a whole interval, otherwise every container restart
    would move the backup by hours. If the user stopped it instead, the normal interval
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


class JobRunner:
    def __init__(self, job_id: int, cancel: StopSignal) -> None:
        self.job_id = job_id
        self.cancel = cancel

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
            account_id = job.account_id
            peer = manager.input_peer(channel)
            source = build_source(job)

            account = await session.get(TelegramAccount, job.account_id)
            concurrency = max(1, account.max_concurrent_jobs if account else 2)
            # The ceiling of 20 connections is per data center, not per job: it has to be
            # divided among the jobs allowed to upload together, or Telegram blocks them.
            max_connections = max(1, MAX_CONNECTIONS // concurrency)

        progress = hub.start_job(self.job_id, job_name)
        progress.phase = "scan"

        try:
            client = await manager.get_client(account_id)
            entity = peer

            counters = await self._diff(source, run_id, progress)
            self._check_cancel()

            progress.phase = "delete"
            progress.scanned_where = None
            await self._set_phase("delete")
            removed = await self._apply_deletions(client, entity)

            # Two jobs on the same account would each open 20 connections and exceed the
            # per data center ceiling, blocking each other: the upload phase is serialized
            # per account. Only this phase, though: scanning and cleanup can go on in
            # parallel, and waiting has a phase of its own so a queued job does not look
            # stuck.
            lock = manager.transfer_lock(account_id, concurrency)
            if lock.locked():
                progress.phase = "waiting"
                await self._set_phase("waiting")
                log.info("Job %d waiting for account %d to upload", self.job_id, account_id)

            async with lock:
                self._check_cancel()
                progress.phase = "upload"
                await self._set_phase("upload")
                uploaded_files, uploaded_bytes = await self._upload_pending(
                    client, entity, part_size, progress, source, max_connections
                )

            async with SessionLocal() as session:
                run = await session.get(JobRun, run_id)
                run.status = "ok"
                run.finished_at = utcnow()
                run.scanned = counters["scanned"]
                run.added = counters["added"]
                run.modified = counters["modified"]
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
        finally:
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

        async with SessionLocal() as session:
            result = await session.execute(
                select(FileEntry).where(FileEntry.job_id == self.job_id)
            )
            known = {entry.rel_path: entry for entry in result.scalars()}

            for rel_path, item in on_disk.items():
                entry = known.get(rel_path)
                if entry is None:
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

            for rel_path, entry in known.items():
                if rel_path not in on_disk:
                    entry.state = "to_delete"

            run = await session.get(JobRun, run_id)
            if run is not None:
                run.scanned = len(on_disk)
                run.added = added
                run.modified = modified
            await session.commit()

        return {"scanned": len(on_disk), "added": added, "modified": modified}

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
        self, client, entity, part_size: int, progress, source, max_connections: int
    ) -> tuple[int, int]:
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

        uploaded_files = 0
        uploaded_bytes = 0

        while True:
            self._check_cancel()
            async with SessionLocal() as session:
                result = await session.execute(
                    select(FileEntry)
                    .where(FileEntry.job_id == self.job_id, FileEntry.state == "pending")
                    .order_by(FileEntry.id)
                    .limit(1)
                )
                entry = result.scalar_one_or_none()
                if entry is None:
                    return uploaded_files, uploaded_bytes
                entry.state = "uploading"
                await session.commit()
                file_id = entry.id
                rel_path = entry.rel_path
                name = entry.name
                size = entry.size

            progress.current_file = rel_path

            try:
                sent = await self._upload_one(
                    client, entity, source, rel_path, name, size, part_size,
                    progress, file_id, max_connections
                )
            except JobCancelled:
                async with SessionLocal() as session:
                    entry = await session.get(FileEntry, file_id)
                    if entry is not None and entry.state == "uploading":
                        # If any part had already gone out it must be deleted next round,
                        # otherwise it would stay orphaned in the channel.
                        has_parts = await session.scalar(
                            select(func.count(FilePart.id)).where(FilePart.file_id == file_id)
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
                continue

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
        file_id: int,
        max_connections: int,
    ) -> int:
        """Uploads a file, splitting it above the threshold. Returns the number of parts.

        Every part is recorded in the database as soon as its message has been sent: if the
        process dies in the middle of a large file, the message id is already stored and on
        the next round the part is deleted instead of staying orphaned in the channel.
        """
        slices = part_plan(size, part_size)
        progress.current_parts = len(slices)

        for index, (offset, length) in enumerate(slices):
            self._check_cancel()
            progress.current_part = index + 1
            file_name = part_file_name(name, index, len(slices))

            handle = await self._upload_with_retry(
                client, source, rel_path, offset, length, file_name, progress, max_connections
            )

            caption = build_caption(rel_path, name, index, len(slices))
            message = await client.send_file(
                entity,
                handle,
                caption=caption,
                # Photos and videos must stay documents: no recompression, no quality
                # loss, bytes identical to the original.
                force_document=True,
                attributes=[DocumentAttributeFilename(file_name)],
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

    async def _upload_with_retry(
        self, client, source, rel_path: str, offset: int, length: int, file_name: str,
        progress, max_connections: int,
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
                )
            except FloodWaitError as exc:
                log.warning("Flood wait of %ss while uploading %s", exc.seconds, file_name)
                await asyncio.sleep(exc.seconds + 1)
            except asyncio.CancelledError:
                raise JobCancelled() from None
            except Exception:
                if attempt >= 3:
                    raise
                log.warning("Retrying the upload of %s (attempt %d)", file_name, attempt + 1)
                # The bytes of the failed attempt were already counted: the slice restarts
                # from zero and the speed estimate settles again by itself.
                await asyncio.sleep(5 * attempt)


async def execute_job(job_id: int, cancel: StopSignal) -> None:
    """Runs a job and schedules the next run starting from the end of this one."""
    runner = JobRunner(job_id, cancel)

    status = "idle"
    error: str | None = None
    interrupted_by_shutdown = False
    try:
        # No semaphore here: serializing the whole execution would stop a job from even
        # scanning while another one on the same account uploads, and with runs lasting
        # days it would sit still for days. The semaphore lives inside run(), around the
        # upload phase alone.
        await runner.run()
    except JobCancelled:
        interrupted_by_shutdown = cancel.reason == "shutdown"
        log.info(
            "Job %d interrupted (%s)",
            job_id,
            "shutdown" if interrupted_by_shutdown else "user request",
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
            if not interrupted_by_shutdown:
                # The interval starts from the end: a job running for three days does not
                # pile up missed executions to catch up all at once.
                job.next_run_at = datetime.now(UTC) + timedelta(
                    hours=job.interval_hours
                )
            # If shutdown stopped it, next_run_at stays as it was: on restart the job
            # resumes right away instead of skipping a whole interval.
            await session.commit()
