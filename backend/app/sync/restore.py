"""Rebuilding a file from its parts on Telegram.

The parts are separate documents, each with its own message id. They are downloaded in
order and written at their position inside a single destination file, so the result is
byte for byte identical to the original.
"""

from __future__ import annotations

import asyncio
import logging
import os
import uuid

from sqlalchemy import select

from ..config import settings
from ..db import SessionLocal
from ..models import Channel, FileEntry, FilePart, SyncJob
from ..telegram.fast_transfer import download_document
from ..telegram.manager import manager
from .progress import RestoreProgress, hub

log = logging.getLogger(__name__)


async def restore_file(file_id: int) -> str:
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
        account_id = job.account_id
        peer = manager.input_peer(channel)

    if not parts:
        raise ValueError("No parts recorded for this file")

    restore_id = uuid.uuid4().hex[:12]
    target = settings.restore_dir / restore_id / rel_path
    progress = RestoreProgress(
        restore_id=restore_id,
        file_name=name,
        target_path=str(target),
        bytes_total=size,
    )
    hub.start_restore(progress)

    asyncio.create_task(
        _run_restore(progress, account_id, peer, parts, target),
        name=f"restore-{restore_id}",
    )
    return restore_id


async def _run_restore(
    progress: RestoreProgress,
    account_id: int,
    entity,
    parts: list[tuple[int, int, int, int]],
    target,
) -> None:
    try:
        client = await manager.get_client(account_id)

        await asyncio.to_thread(os.makedirs, target.parent, 0o755, True)
        fd = await asyncio.to_thread(os.open, str(target), os.O_WRONLY | os.O_CREAT | os.O_TRUNC)
        try:
            # The file is sized upfront: the positional writes of later parts should not
            # have to extend the file one at a time.
            await asyncio.to_thread(os.ftruncate, fd, progress.bytes_total)

            for part_index, offset, size, message_id in parts:
                messages = await client.get_messages(entity, ids=message_id)
                message = messages if not isinstance(messages, list) else messages[0]
                if message is None or message.document is None:
                    raise RuntimeError(
                        f"Message {message_id} of part {part_index + 1} "
                        "no longer exists in the channel"
                    )
                written = await download_document(
                    client, message.document, fd, offset, on_progress=progress.add_bytes
                )
                if written != size:
                    raise RuntimeError(
                        f"Part {part_index + 1} returned {written} bytes "
                        f"instead of {size}"
                    )
        finally:
            await asyncio.to_thread(os.close, fd)

        progress.phase = "done"
        log.info("Restore completed at %s", target)
    except Exception as exc:
        log.exception("Restore failed")
        progress.phase = "error"
        progress.error = str(exc)[:500]
