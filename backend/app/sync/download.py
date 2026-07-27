"""Execution of a download job: index, comparison with the destination, download.

The inverse of `runner.py`. There the source is the truth and the channel is made to
match it; here the channel is the truth and what is missing at the destination is written
there.

The list of what the channel holds is the index in the database, that is the file entries
of the sync jobs writing to that channel: identity triple and message ids of every part,
which is exactly what rebuilding a file needs. A channel whose index has been imported
from another machine works the same way, which is the point: export the index there,
import it here, and a download job puts the files back.

Nothing is ever deleted at the destination. A sync job deletes from the channel what has
gone from the source, because the channel is a copy this application owns; the destination
of a download job holds the user's own files, and a file that the channel does not know
about is not a mistake to correct.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import aclosing
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import selectinload
from telethon.errors import FloodWaitError

from ..db import SessionLocal
from ..models import Channel, DownloadJob, DownloadRun, FileEntry, SyncJob, TelegramAccount, utcnow
from ..telegram.fast_transfer import MAX_CONNECTIONS, download_document, stream_document
from ..telegram.manager import manager
from .destination import build_destination
from .progress import hub
from .runner import JobCancelled, StopSignal

log = logging.getLogger(__name__)

# Files whose messages are asked for in one round trip.
MESSAGE_BATCH = 100


@dataclass(slots=True)
class IndexedPart:
    part_index: int
    offset: int
    size: int
    message_id: int


@dataclass(slots=True)
class IndexedFile:
    rel_path: str
    name: str
    size: int
    mtime_ns: int
    parts: list[IndexedPart]


async def channel_index(session, channel_id: int) -> list[IndexedFile]:
    """What the channel holds, according to the index of the jobs writing to it.

    A channel can be the destination of several sync jobs, and two of them can hold the
    same relative path. Only one copy can be written to a single destination path: the one
    uploaded most recently wins, which is the same rule the file browser applies when it
    shows a channel.
    """
    result = await session.execute(
        select(FileEntry)
        .where(
            FileEntry.job_id.in_(select(SyncJob.id).where(SyncJob.channel_id == channel_id)),
            FileEntry.state == "uploaded",
        )
        .options(selectinload(FileEntry.parts))
        .order_by(FileEntry.rel_path)
    )

    epoch = datetime.fromtimestamp(0, UTC)
    best: dict[str, tuple[datetime, int, IndexedFile]] = {}
    for entry in result.scalars():
        if not entry.parts:
            # Uploaded with no recorded part: there is nothing to fetch.
            continue
        uploaded = entry.uploaded_at or epoch
        if uploaded.tzinfo is None:
            uploaded = uploaded.replace(tzinfo=UTC)
        current = best.get(entry.rel_path)
        if current is not None and (uploaded, entry.id) <= (current[0], current[1]):
            continue
        best[entry.rel_path] = (
            uploaded,
            entry.id,
            IndexedFile(
                rel_path=entry.rel_path,
                name=entry.name,
                size=entry.size,
                mtime_ns=entry.mtime_ns,
                parts=[
                    IndexedPart(part.part_index, part.offset, part.size, part.message_id)
                    for part in sorted(entry.parts, key=lambda p: p.part_index)
                ],
            ),
        )

    return [item[2] for item in best.values()]


class DownloadRunner:
    def __init__(self, job_id: int, cancel: StopSignal) -> None:
        self.job_id = job_id
        self.cancel = cancel

    def _check_cancel(self) -> None:
        if self.cancel.is_set():
            raise JobCancelled()

    async def run(self) -> None:
        async with SessionLocal() as session:
            job = await session.get(DownloadJob, self.job_id)
            if job is None:
                return
            channel = await session.get(Channel, job.channel_id)
            if channel is None:
                raise RuntimeError("The job channel no longer exists")

            # Both of these can refuse the job as it is configured, and they run before
            # the run row exists: a row written first would stay at "running" for ever,
            # since nothing has started that could close it.
            peer = manager.input_peer(channel)
            destination = build_destination(job)

            run = DownloadRun(job_id=job.id)
            session.add(run)
            job.last_run_at = utcnow()
            job.status = "running"
            job.phase = "index"
            job.last_error = None
            await session.commit()
            run_id = run.id
            job_name = job.name
            account_id = job.account_id
            channel_id = job.channel_id

            account = await session.get(TelegramAccount, job.account_id)
            concurrency = max(1, account.max_concurrent_jobs if account else 2)
            # The ceiling of 20 connections is per data center and is shared with the
            # uploads: divided among the jobs allowed to transfer at the same time.
            max_connections = max(1, MAX_CONNECTIONS // concurrency)

        progress = hub.start_download(self.job_id, job_name)
        progress.phase = "index"

        try:
            client = await manager.get_client(account_id)
            await destination.prepare()

            async with SessionLocal() as session:
                indexed = await channel_index(session, channel_id)
            indexed_bytes = sum(item.size for item in indexed)
            progress.indexed_files = len(indexed)
            progress.indexed_bytes = indexed_bytes
            self._check_cancel()

            progress.phase = "scan"
            await self._set_phase("scan")
            existing = await self._list_destination(destination, progress)
            self._check_cancel()

            progress.phase = "diff"
            progress.dest_where = None
            await self._set_phase("diff")

            missing = [
                item
                for item in indexed
                # Size is the only comparison: the date of a file at the destination says
                # when it was written there, not when the original was last changed, and
                # on a remote it may not be stored at all.
                if existing.get(item.rel_path) != item.size
            ]
            present = len(indexed) - len(missing)
            present_bytes = sum(
                item.size for item in indexed if existing.get(item.rel_path) == item.size
            )

            async with SessionLocal() as session:
                run = await session.get(DownloadRun, run_id)
                if run is not None:
                    run.indexed_files = len(indexed)
                    run.indexed_bytes = indexed_bytes
                    run.present_files = present
                    run.present_bytes = present_bytes
                    await session.commit()

            progress.present_files = present
            progress.files_total = len(missing)
            progress.bytes_total = sum(item.size for item in missing)
            progress.files_done = 0
            progress.bytes_done = 0

            log.info(
                "Download job %d: %d files in the channel, %d already at %s, %d to download",
                self.job_id, len(indexed), present, destination.label, len(missing),
            )

            # The same budget of 20 connections per data center that the uploads share:
            # a download job queues behind them on the same account.
            lock = manager.transfer_lock(account_id, concurrency)
            if lock.locked():
                progress.phase = "waiting"
                await self._set_phase("waiting")
                log.info("Download job %d waiting for account %d", self.job_id, account_id)

            async with lock:
                self._check_cancel()
                progress.phase = "download"
                await self._set_phase("download")
                files, written, failed, last_error = await self._download_all(
                    client, peer, destination, missing, progress, max_connections
                )

            async with SessionLocal() as session:
                run = await session.get(DownloadRun, run_id)
                run.status = "ok"
                run.finished_at = utcnow()
                run.downloaded_files = files
                run.downloaded_bytes = written
                run.failed_files = failed
                # A file that fails does not stop the run, but it must not disappear
                # either: the count and the last message are the only place it is kept,
                # a download job has no file table of its own.
                run.error = (
                    f"{failed} files failed, last error: {last_error}" if failed else None
                )
                await session.commit()

        except JobCancelled:
            await self._close_run(run_id, "stopped", None)
            raise
        except Exception as exc:
            # The run row is closed here and not by the caller: left at "running" it would
            # show up in the history as a job still going, for ever.
            await self._close_run(run_id, "error", str(exc)[:1000])
            raise
        finally:
            hub.end_download(self.job_id)

    async def _close_run(self, run_id: int, status: str, error: str | None) -> None:
        async with SessionLocal() as session:
            run = await session.get(DownloadRun, run_id)
            if run is not None:
                run.status = status
                run.finished_at = utcnow()
                if error is not None:
                    run.error = error
                await session.commit()

    async def _set_phase(self, phase: str) -> None:
        async with SessionLocal() as session:
            job = await session.get(DownloadJob, self.job_id)
            if job is not None:
                job.phase = phase
                await session.commit()

    async def _list_destination(self, destination, progress) -> dict[str, int]:
        def report(files: int, _dirs: int, _total_bytes: int, where: str) -> None:
            progress.dest_files = files
            progress.dest_where = where or None

        return await destination.list_files(on_progress=report)

    async def _download_all(
        self, client, entity, destination, missing: list[IndexedFile], progress, max_connections
    ) -> tuple[int, int, int, str | None]:
        files = 0
        written = 0
        failed = 0
        last_error: str | None = None

        for item in missing:
            self._check_cancel()
            progress.current_file = item.rel_path
            progress.current_part = 0
            progress.current_parts = len(item.parts)

            try:
                await self._download_one(
                    client, entity, destination, item, progress, max_connections
                )
            except JobCancelled:
                raise
            except Exception as exc:
                failed += 1
                last_error = str(exc)[:400]
                log.exception("Download of %s failed", item.rel_path)
                progress.files_done += 1
                continue

            files += 1
            written += item.size
            progress.files_done += 1

        return files, written, failed, last_error

    async def _download_one(
        self, client, entity, destination, item: IndexedFile, progress, max_connections: int
    ) -> None:
        """Rebuilds one file at the destination from the parts of its message.

        The messages of every part are asked for in one round trip, then written in order.
        A local destination takes each part at its own offset, so the parts are downloaded
        in parallel; a remote is a pipe and takes the bytes in order, which the ordered
        stream provides without losing the parallel download.
        """
        documents = await self._documents(client, entity, item)

        async with destination.sink(item.rel_path, item.size, item.mtime_ns) as sink:
            for part, document in zip(item.parts, documents, strict=True):
                self._check_cancel()
                progress.current_part = part.part_index + 1

                if sink.random_access:
                    got = await self._with_retry(
                        lambda document=document, part=part: download_document(
                            client,
                            document,
                            sink.fd,
                            part.offset,
                            on_progress=progress.add_bytes,
                            cancel=self.cancel,
                            max_connections=max_connections,
                        ),
                        item.rel_path,
                        progress,
                    )
                else:
                    got = 0
                    # aclosing and not a bare async for: if the write to the destination
                    # fails halfway the generator has to be closed there and then, or its
                    # senders would stay connected until the garbage collector gets to
                    # them, holding on to part of the account connection budget.
                    async with aclosing(
                        stream_document(
                            client,
                            document,
                            on_progress=progress.add_bytes,
                            cancel=self.cancel,
                            max_connections=max_connections,
                        )
                    ) as stream:
                        async for chunk in stream:
                            await sink.write(chunk)
                            got += len(chunk)

                if got != part.size:
                    raise RuntimeError(
                        f"Part {part.part_index + 1} of {item.rel_path} returned {got} bytes "
                        f"instead of {part.size}"
                    )

            await sink.commit()

    async def _documents(self, client, entity, item: IndexedFile) -> list:
        """The document of every part, in order, in as few round trips as possible."""
        ids = [part.message_id for part in item.parts]
        messages: list = []
        for start in range(0, len(ids), MESSAGE_BATCH):
            chunk = ids[start : start + MESSAGE_BATCH]
            try:
                got = await client.get_messages(entity, ids=chunk)
            except FloodWaitError as exc:
                log.warning("Flood wait of %ss while reading the messages", exc.seconds)
                await asyncio.sleep(exc.seconds + 1)
                got = await client.get_messages(entity, ids=chunk)
            messages.extend(got if isinstance(got, list) else [got])

        documents = []
        for part, message in zip(item.parts, messages, strict=True):
            if message is None or message.document is None:
                raise RuntimeError(
                    f"Message {part.message_id} of part {part.part_index + 1} "
                    "no longer exists in the channel"
                )
            documents.append(message.document)
        return documents

    async def _with_retry(self, call, rel_path: str, progress) -> int:
        attempt = 0
        while True:
            attempt += 1
            try:
                return await call()
            except FloodWaitError as exc:
                log.warning("Flood wait of %ss while downloading %s", exc.seconds, rel_path)
                await asyncio.sleep(exc.seconds + 1)
            except asyncio.CancelledError:
                raise JobCancelled() from None
            except Exception:
                if attempt >= 3:
                    raise
                log.warning("Retrying the download of %s (attempt %d)", rel_path, attempt + 1)
                # The bytes of the failed attempt were already counted: the part restarts
                # from zero and the speed estimate settles again by itself.
                await asyncio.sleep(5 * attempt)


async def execute_download_job(job_id: int, cancel: StopSignal) -> None:
    """Runs a download job and schedules the next run from the end of this one."""
    runner = DownloadRunner(job_id, cancel)

    status = "idle"
    error: str | None = None
    interrupted_by_shutdown = False
    try:
        await runner.run()
    except JobCancelled:
        interrupted_by_shutdown = cancel.reason == "shutdown"
        log.info(
            "Download job %d interrupted (%s)",
            job_id,
            "shutdown" if interrupted_by_shutdown else "user request",
        )
    except Exception as exc:
        log.exception("Download job %d failed", job_id)
        status = "error"
        error = str(exc)[:1000]

    async with SessionLocal() as session:
        job = await session.get(DownloadJob, job_id)
        if job is not None:
            job.status = status
            job.phase = None
            job.last_error = error
            job.last_finished_at = utcnow()
            if not interrupted_by_shutdown:
                # The interval starts from the end of the run, as it does for a sync job.
                job.next_run_at = datetime.now(UTC) + timedelta(hours=job.interval_hours)
            # If shutdown stopped it, next_run_at stays as it was and the job resumes on
            # restart instead of skipping a whole interval.
            await session.commit()
