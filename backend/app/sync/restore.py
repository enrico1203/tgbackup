"""Rebuilding files from their parts on Telegram, one or a whole folder at a time.

The parts are separate documents, each with its own message id. Put back together they
give a file byte for byte identical to the original.

Where it lands is up to the caller. With no destination it goes to `data/restore/`, which
is what the Files page has always done; the explorer can also ask for a folder mounted in
the container or an rclone remote, and then it is the same write a download job performs,
through the same destinations, for one file instead of a channel.

A folder is the same machinery with more than one file in the list. It is worth being one
operation rather than a hundred started by the browser: the destination is prepared once,
the progress is one bar with a file count, a file that fails does not take the rest down
with it, and the whole thing can be stopped. Restoring a deleted subtree is the reason the
trash exists, so it cannot be something the user has to do file by file.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import uuid
from dataclasses import dataclass

from sqlalchemy import select

from ..config import settings
from ..db import SessionLocal
from ..models import (
    READABLE_STATES,
    Channel,
    FileEntry,
    FilePart,
    SyncJob,
    TelegramAccount,
)
from ..telegram.fast_transfer import MAX_CONNECTIONS, download_document, stream_document
from ..telegram.manager import manager
from ..telegram.throttle import installation_limiter
from .destination import DestinationError, LocalDestination, RcloneDestination
from .progress import RestoreProgress, hub
from .runner import StopSignal

log = logging.getLogger(__name__)

# The largest code point there is, the same bound the explorer uses to select a subtree
# from the index: `prefix <= rel_path < prefix + this` is everything inside the folder.
PATH_CEILING = "\U0010ffff"

# How many failures are kept to be shown. A restore of a folder whose channel has been
# emptied would otherwise carry thousands of identical lines to every connected browser.
MAX_REPORTED_ERRORS = 20

# The event loop keeps only a weak reference to a running task: without a strong one
# here, a restore in progress can be collected halfway through and stop with no trace.
_running: set[asyncio.Task] = set()

# Stop signals of the restores in flight, by restore id. A folder can take hours, so it
# has to be possible to change one's mind without restarting the process.
_cancels: dict[str, StopSignal] = {}


@dataclass(slots=True)
class _Item:
    """One file to rebuild: where it goes and which messages it is made of."""

    rel_path: str
    name: str
    size: int
    mtime_ns: int
    parts: list[tuple[int, int, int, int]]


def _build_destination(
    dest_type: str, target: str, restore_id: str
) -> LocalDestination | RcloneDestination:
    """Where the rebuilt file goes, as one of the destinations a download job writes to."""
    if dest_type == "rclone":
        if not target.strip():
            raise ValueError("No remote given")
        return RcloneDestination(target)
    if dest_type == "local":
        if not target.strip():
            raise ValueError("No folder given")
        return LocalDestination(target.strip())
    # The historical destination: a folder of its own inside data/restore, so two
    # restores of the same path cannot land on each other.
    return LocalDestination(str(settings.restore_dir / restore_id))


async def _parts_of(session, file_id: int) -> list[tuple[int, int, int, int]]:
    result = await session.execute(
        select(FilePart).where(FilePart.file_id == file_id).order_by(FilePart.part_index)
    )
    return [
        (part.part_index, part.offset, part.size, part.message_id)
        for part in result.scalars()
    ]


async def _budget(session, account_id: int) -> tuple[int, int]:
    """How many jobs may transfer at once on this account, and the connections each gets.

    A restore spends from the same ceiling of 20 connections per data center as the jobs
    do, and it goes through the same semaphore. Asking for the whole budget while a job
    is uploading is what gets both blocked by Telegram.
    """
    account = await session.get(TelegramAccount, account_id)
    concurrency = max(1, account.max_concurrent_jobs if account else 2)
    return concurrency, max(1, MAX_CONNECTIONS // concurrency)


async def _start(
    items: list[_Item],
    account_id: int,
    peer,
    concurrency: int,
    max_connections: int,
    dest_type: str,
    target: str,
    label: str,
    strip: str,
    shown: str,
) -> str:
    """Prepares the destination and puts the restore in flight. Common to both entries."""
    restore_id = uuid.uuid4().hex[:12]
    destination = _build_destination(dest_type, target, restore_id)

    if isinstance(destination, LocalDestination) and dest_type == "container":
        # This one belongs to the application and is created on demand. The two the user
        # picks are not: a folder that is not there is a mistake worth reporting.
        await asyncio.to_thread(os.makedirs, destination.root, 0o755, True)

    # Checked before anything starts, so a wrong path is an error on the button and not a
    # line in a log ten minutes later.
    try:
        await destination.prepare()
    except DestinationError as exc:
        raise ValueError(str(exc)) from exc

    separator = "" if destination.label.endswith((":", "/")) else "/"
    progress = RestoreProgress(
        restore_id=restore_id,
        file_name=label,
        target_path=f"{destination.label}{separator}{shown}",
        bytes_total=sum(item.size for item in items),
        files_total=len(items),
    )
    hub.start_restore(progress)

    cancel = StopSignal()
    _cancels[restore_id] = cancel

    task = asyncio.create_task(
        _run_restore(
            progress, account_id, peer, items, destination, strip, cancel,
            concurrency, max_connections,
        ),
        name=f"restore-{restore_id}",
    )
    _running.add(task)
    task.add_done_callback(_running.discard)
    return restore_id


async def restore_file(file_id: int, dest_type: str = "container", target: str = "") -> str:
    """Starts the restore of one file and returns the identifier used to follow it."""
    async with SessionLocal() as session:
        entry = await session.get(FileEntry, file_id)
        if entry is None:
            raise ValueError("File not found")
        if entry.state not in READABLE_STATES:
            raise ValueError("The file has not been uploaded to Telegram")

        job = await session.get(SyncJob, entry.job_id)
        channel = await session.get(Channel, job.channel_id)
        item = _Item(
            rel_path=entry.rel_path,
            name=entry.name,
            size=entry.size,
            mtime_ns=entry.mtime_ns,
            parts=await _parts_of(session, file_id),
        )
        account_id = job.account_id
        peer = manager.input_peer(channel)
        concurrency, max_connections = await _budget(session, account_id)

    if not item.parts:
        raise ValueError("No parts recorded for this file")

    return await _start(
        [item], account_id, peer, concurrency, max_connections,
        dest_type, target, item.name, "", item.rel_path,
    )


async def restore_folder(
    channel_id: int, path: str, dest_type: str, target: str
) -> tuple[str, int, int]:
    """Starts the restore of a whole subtree. Returns (id, files, bytes).

    The files are the ones the explorer would list at that path: everything still in the
    channel below it, one row per path with the most recently uploaded winning, and the
    trash included, since a folder somebody wants back is very often a folder that is
    gone. Each file keeps its path **relative to the chosen folder**, so restoring
    `Photos/2024` into `/mnt/restored` gives `/mnt/restored/january/...` and not the whole
    original tree repeated underneath.
    """
    prefix = f"{path}/" if path else ""

    async with SessionLocal() as session:
        channel = await session.get(Channel, channel_id)
        if channel is None:
            raise ValueError("Channel not found")

        stmt = select(FileEntry).where(
            FileEntry.job_id.in_(select(SyncJob.id).where(SyncJob.channel_id == channel_id)),
            FileEntry.state.in_(READABLE_STATES),
        )
        if prefix:
            stmt = stmt.where(
                FileEntry.rel_path >= prefix,
                FileEntry.rel_path < prefix + PATH_CEILING,
            )
        rows = list((await session.execute(stmt)).scalars())

        # Same rule as the listing: two jobs writing one path into one channel are
        # resolved by keeping the one uploaded last.
        unique: dict[str, FileEntry] = {}
        for entry in rows:
            current = unique.get(entry.rel_path)
            if current is None or (entry.uploaded_at or entry.first_seen_at) >= (
                current.uploaded_at or current.first_seen_at
            ):
                unique[entry.rel_path] = entry

        items: list[_Item] = []
        for entry in sorted(unique.values(), key=lambda row: row.rel_path):
            parts = await _parts_of(session, entry.id)
            if not parts:
                # Nothing recorded to rebuild it from. Skipped rather than failed: it was
                # never in the channel to begin with.
                log.warning("Restore of %s skipped, no parts recorded", entry.rel_path)
                continue
            items.append(
                _Item(
                    rel_path=entry.rel_path,
                    name=entry.name,
                    size=entry.size,
                    mtime_ns=entry.mtime_ns,
                    parts=parts,
                )
            )

        if not items:
            raise ValueError("There is nothing to restore in this folder")

        account_id = channel.account_id
        channel_title = channel.title
        peer = manager.input_peer(channel)
        concurrency, max_connections = await _budget(session, account_id)

    restore_id = await _start(
        items, account_id, peer, concurrency, max_connections,
        dest_type, target, path.rsplit("/", 1)[-1] or channel_title, prefix, prefix,
    )
    return restore_id, len(items), sum(item.size for item in items)


def cancel_restore(restore_id: str) -> bool:
    """Asks a restore to stop at its next part. False if there is nothing to stop."""
    cancel = _cancels.get(restore_id)
    if cancel is None or cancel.is_set():
        return False
    cancel.set("user")
    return True


async def _restore_one(
    client,
    entity,
    item: _Item,
    destination: LocalDestination | RcloneDestination,
    rel_path: str,
    progress: RestoreProgress,
    cancel: StopSignal,
    max_connections: int,
    limiter,
) -> None:
    async with destination.sink(rel_path, item.size, item.mtime_ns) as sink:
        for part_index, offset, part_size, message_id in item.parts:
            if cancel.is_set():
                raise asyncio.CancelledError("Restore interrupted")

            messages = await client.get_messages(entity, ids=message_id)
            message = messages if not isinstance(messages, list) else messages[0]
            if message is None or message.document is None:
                raise RuntimeError(
                    f"Message {message_id} of part {part_index + 1} "
                    "no longer exists in the channel"
                )

            if sink.random_access:
                # A local file takes every part at its own offset, so the parts
                # download in parallel.
                written = await download_document(
                    client,
                    message.document,
                    sink.fd,
                    offset,
                    on_progress=progress.add_bytes,
                    cancel=cancel,
                    max_connections=max_connections,
                    limiter=limiter,
                )
            else:
                # A remote is a pipe: parallel download, bytes handed over in order.
                written = 0
                async with contextlib.aclosing(
                    stream_document(
                        client,
                        message.document,
                        on_progress=progress.add_bytes,
                        cancel=cancel,
                        max_connections=max_connections,
                        limiter=limiter,
                    )
                ) as stream:
                    async for chunk in stream:
                        await sink.write(chunk)
                        written += len(chunk)

            if written != part_size:
                raise RuntimeError(
                    f"Part {part_index + 1} returned {written} bytes "
                    f"instead of {part_size}"
                )

        await sink.commit()


async def _run_restore(
    progress: RestoreProgress,
    account_id: int,
    entity,
    items: list[_Item],
    destination: LocalDestination | RcloneDestination,
    strip: str,
    cancel: StopSignal,
    concurrency: int,
    max_connections: int,
) -> None:
    # No job ceiling here, this was asked for by hand, but the limit of the installation
    # still applies: it is there to keep the line usable.
    limiter = installation_limiter()

    try:
        client = await manager.get_client(account_id)
        lock = manager.transfer_lock(account_id, concurrency)

        for item in items:
            if cancel.is_set():
                break
            rel_path = item.rel_path[len(strip) :] if strip else item.rel_path
            progress.current_file = rel_path

            try:
                # Per file and not around the whole batch: a folder of two terabytes
                # would otherwise hold the connection budget of the account for hours,
                # and the jobs would sit in `waiting` for all of it.
                async with lock:
                    await _restore_one(
                        client, entity, item, destination, rel_path,
                        progress, cancel, max_connections, limiter,
                    )
            except asyncio.CancelledError:
                # The signal, not the task: raised by the transfer when the user asked to
                # stop, and the loop above is what decides to end.
                break
            except Exception as exc:
                # One file must not take the rest of the folder with it, and a single
                # file restore reports it exactly as it always did.
                log.exception("Restore of %s failed", item.rel_path)
                progress.failed += 1
                if len(progress.errors) < MAX_REPORTED_ERRORS:
                    progress.errors.append(f"{rel_path}: {str(exc)[:200]}")
                if len(items) == 1:
                    progress.error = str(exc)[:500]
            finally:
                progress.files_done += 1

        progress.current_file = None
        if cancel.is_set():
            progress.phase = "cancelled"
            log.info("Restore to %s stopped by the user", progress.target_path)
        elif progress.failed and progress.failed == len(items):
            progress.phase = "error"
            progress.error = progress.error or progress.errors[0]
        else:
            progress.phase = "done"
            log.info(
                "Restore completed at %s, %d file(s), %d failed",
                progress.target_path, len(items), progress.failed,
            )
    except Exception as exc:
        log.exception("Restore failed")
        progress.phase = "error"
        progress.error = str(exc)[:500]
    finally:
        _cancels.pop(progress.restore_id, None)
