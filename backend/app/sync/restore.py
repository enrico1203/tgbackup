"""Ricostruzione di un file a partire dalle sue parti su Telegram.

Le parti sono documenti separati, ognuno con il proprio message id. Si riscaricano in
ordine e si scrivono alla loro posizione dentro un unico file di destinazione, quindi
il risultato e byte per byte identico all'originale.
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
    """Avvia il restore e restituisce l'identificativo con cui seguirlo."""
    async with SessionLocal() as session:
        entry = await session.get(FileEntry, file_id)
        if entry is None:
            raise ValueError("File non trovato")
        if entry.state != "uploaded":
            raise ValueError("Il file non e stato caricato su Telegram")

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
        raise ValueError("Nessuna parte registrata per questo file")

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
            # Si dimensiona subito il file: le scritture posizionali delle parti
            # successive non devono estendere il file una alla volta.
            await asyncio.to_thread(os.ftruncate, fd, progress.bytes_total)

            for part_index, offset, size, message_id in parts:
                messages = await client.get_messages(entity, ids=message_id)
                message = messages if not isinstance(messages, list) else messages[0]
                if message is None or message.document is None:
                    raise RuntimeError(
                        f"Il messaggio {message_id} della parte {part_index + 1} "
                        "non esiste piu sul canale"
                    )
                written = await download_document(
                    client, message.document, fd, offset, on_progress=progress.add_bytes
                )
                if written != size:
                    raise RuntimeError(
                        f"La parte {part_index + 1} ha restituito {written} byte "
                        f"invece di {size}"
                    )
        finally:
            await asyncio.to_thread(os.close, fd)

        progress.phase = "done"
        log.info("Restore completato in %s", target)
    except Exception as exc:
        log.exception("Restore fallito")
        progress.phase = "error"
        progress.error = str(exc)[:500]
