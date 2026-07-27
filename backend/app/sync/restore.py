"""Rebuilding a single file from its parts on Telegram.

The parts are separate documents, each with its own message id. Put back together they
give a file byte for byte identical to the original.

Where it lands is up to the caller. With no destination it goes to `data/restore/`, which
is what the Files page has always done; the explorer can also ask for a folder mounted in
the container or an rclone remote, and then it is the same write a download job performs,
through the same destinations, for one file instead of a channel.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import uuid

from sqlalchemy import select

from ..config import settings
from ..db import SessionLocal
from ..models import Channel, FileEntry, FilePart, SyncJob
from ..telegram.fast_transfer import download_document, stream_document
from ..telegram.manager import manager
from .destination import DestinationError, LocalDestination, RcloneDestination
from .progress import RestoreProgress, hub

log = logging.getLogger(__name__)

# The event loop keeps only a weak reference to a running task: without a strong one
# here, a restore in progress can be collected halfway through and stop with no trace.
_running: set[asyncio.Task] = set()


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


async def restore_file(file_id: int, dest_type: str = "container", target: str = "") -> str:
    """Starts the restore and returns the identifier used to follow it."""
    async with SessionLocal() as session:
        entry = await session.get(FileEntry, file_id)
        if entry is None:
            raise ValueError("File not found")
        if entry.state != "uploaded":
            raise ValueError("The file has not been uploaded to Telegram")

        job = await session.get(SyncJob, entry.job_id)
        channel = await session.get(Channel, job.channel_id)
        parts_result = await session.execute(
            select(FilePart).where(FilePart.file_id == file_id).order_by(FilePart.part_index)
        )
        parts = [
            (part.part_index, part.offset, part.size, part.message_id)
            for part in parts_result.scalars()
        ]
        rel_path = entry.rel_path
        name = entry.name
        size = entry.size
        mtime_ns = entry.mtime_ns
        account_id = job.account_id
        peer = manager.input_peer(channel)

    if not parts:
        raise ValueError("No parts recorded for this file")

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
        file_name=name,
        target_path=f"{destination.label}{separator}{rel_path}",
        bytes_total=size,
    )
    hub.start_restore(progress)

    task = asyncio.create_task(
        _run_restore(progress, account_id, peer, parts, destination, rel_path, size, mtime_ns),
        name=f"restore-{restore_id}",
    )
    _running.add(task)
    task.add_done_callback(_running.discard)
    return restore_id


async def _run_restore(
    progress: RestoreProgress,
    account_id: int,
    entity,
    parts: list[tuple[int, int, int, int]],
    destination: LocalDestination | RcloneDestination,
    rel_path: str,
    size: int,
    mtime_ns: int,
) -> None:
    try:
        client = await manager.get_client(account_id)

        async with destination.sink(rel_path, size, mtime_ns) as sink:
            for part_index, offset, part_size, message_id in parts:
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
                        client, message.document, sink.fd, offset, on_progress=progress.add_bytes
                    )
                else:
                    # A remote is a pipe: parallel download, bytes handed over in order.
                    written = 0
                    async with contextlib.aclosing(
                        stream_document(
                            client, message.document, on_progress=progress.add_bytes
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

        progress.phase = "done"
        log.info("Restore completed at %s", progress.target_path)
    except Exception as exc:
        log.exception("Restore failed")
        progress.phase = "error"
        progress.error = str(exc)[:500]
