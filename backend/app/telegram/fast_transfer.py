"""Trasferimento parallelo su MTProto.

Telethon carica i chunk di un file uno dopo l'altro su una sola connessione, quindi la
velocita e limitata dal round trip e non dalla banda. Qui si aprono piu MTProtoSender
sulla stessa auth key e si distribuiscono le parti fra loro.

Vincoli di protocollo da rispettare:
  - massimo 20 connessioni per data center, oltre Telegram le blocca tutte
  - la parte e al massimo 512KB, tutte le parti tranne l'ultima devono essere piene
  - massimo 8000 parti per file, cioe 3.90625 GiB con parti da 512KB
  - i file oltre 10MB usano SaveBigFilePartRequest e non hanno md5

Il lettore resta sequenziale e i sender lavorano in parallelo: questo permette di
caricare una fetta arbitraria di un file grande senza produrre file temporanei.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import math
import os
from collections.abc import Callable

from telethon import TelegramClient, helpers
from telethon.network import MTProtoSender
from telethon.tl.alltlobjects import LAYER
from telethon.tl.functions import InvokeWithLayerRequest
from telethon.tl.functions.auth import ExportAuthorizationRequest, ImportAuthorizationRequest
from telethon.tl.functions.upload import (
    GetFileRequest,
    SaveBigFilePartRequest,
    SaveFilePartRequest,
)
from telethon.tl.types import Document, InputDocumentFileLocation, InputFile, InputFileBig

log = logging.getLogger(__name__)

MAX_CONNECTIONS = 20
UPLOAD_PART_SIZE = 512 * 1024
DOWNLOAD_PART_SIZE = 1024 * 1024
BIG_FILE_THRESHOLD = 10 * 1024 * 1024
MAX_PARTS = 8000

ProgressCallback = Callable[[int], None]


def connection_count(size: int, maximum: int = MAX_CONNECTIONS) -> int:
    """Una connessione ogni 5MB, fino al tetto. Sotto i 5MB il parallelismo non serve."""
    if size <= 0:
        return 1
    return max(1, min(maximum, math.ceil(size / (5 * 1024 * 1024))))


class _UploadSender:
    def __init__(
        self,
        client: TelegramClient,
        sender: MTProtoSender,
        file_id: int,
        part_count: int,
        big: bool,
        index: int,
        stride: int,
        on_part: ProgressCallback | None,
    ) -> None:
        self._client = client
        self._sender = sender
        self._stride = stride
        self._on_part = on_part
        self._pending: asyncio.Task | None = None
        if big:
            self._request = SaveBigFilePartRequest(file_id, index, part_count, b"")
        else:
            self._request = SaveFilePartRequest(file_id, index, b"")

    async def send(self, data: bytes) -> None:
        # Le parti di uno stesso sender devono restare in ordine: si attende la precedente
        # prima di accodare la successiva, ma i sender diversi procedono in parallelo.
        if self._pending is not None:
            await self._pending
        self._pending = asyncio.create_task(self._send(data))

    async def _send(self, data: bytes) -> None:
        self._request.bytes = data
        await self._client._call(self._sender, self._request)
        self._request.file_part += self._stride
        if self._on_part is not None:
            self._on_part(len(data))

    async def drain(self) -> None:
        if self._pending is not None:
            await self._pending
            self._pending = None

    async def disconnect(self) -> None:
        await self._sender.disconnect()


class _DownloadSender:
    def __init__(
        self,
        client: TelegramClient,
        sender: MTProtoSender,
        location,
        offset: int,
        limit: int,
        stride: int,
        count: int,
    ) -> None:
        self._client = client
        self._sender = sender
        self._request = GetFileRequest(location, offset, limit)
        self._stride = stride
        self._remaining = count

    async def next(self) -> tuple[int, bytes] | None:
        if self._remaining <= 0:
            return None
        offset = self._request.offset
        result = await self._client._call(self._sender, self._request)
        self._remaining -= 1
        self._request.offset += self._stride
        return offset, result.bytes

    async def disconnect(self) -> None:
        await self._sender.disconnect()


class ParallelTransferrer:
    def __init__(self, client: TelegramClient, dc_id: int | None = None) -> None:
        self._client = client
        self._dc_id = dc_id or client.session.dc_id
        # Sul DC di casa si riusa la auth key esistente; su un altro DC serve
        # esportare l'autorizzazione una volta e riusarla per tutti i sender.
        self._auth_key = (
            None if dc_id and client.session.dc_id != dc_id else client.session.auth_key
        )

    async def _create_sender(self) -> MTProtoSender:
        dc = await self._client._get_dc(self._dc_id)
        sender = MTProtoSender(self._auth_key, loggers=self._client._log)
        await sender.connect(
            self._client._connection(
                dc.ip_address,
                dc.port,
                dc.id,
                loggers=self._client._log,
                proxy=self._client._proxy,
                local_addr=self._client._local_addr,
            )
        )
        if self._auth_key is None:
            auth = await self._client(ExportAuthorizationRequest(self._dc_id))
            self._client._init_request.query = ImportAuthorizationRequest(
                id=auth.id, bytes=auth.bytes
            )
            await sender.send(InvokeWithLayerRequest(LAYER, self._client._init_request))
            self._auth_key = sender.auth_key
        return sender


class LocalSliceReader:
    """Lettore sequenziale di un intervallo di byte da un file locale.

    Espone la stessa interfaccia del lettore rclone, cosi l'uploader non sa da dove
    arrivino i byte. Usa letture posizionali, quindi non sposta il cursore del file.
    """

    def __init__(self, path: str, offset: int, length: int) -> None:
        self._path = path
        self._position = offset
        self._remaining = length
        self._handle: int | None = None

    async def __aenter__(self) -> "LocalSliceReader":
        self._handle = await asyncio.to_thread(os.open, self._path, os.O_RDONLY)
        return self

    async def read(self, size: int) -> bytes:
        assert self._handle is not None
        wanted = min(size, self._remaining)
        if wanted <= 0:
            return b""
        data = await asyncio.to_thread(os.pread, self._handle, wanted, self._position)
        self._position += len(data)
        self._remaining -= len(data)
        return data

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if self._handle is not None:
            await asyncio.to_thread(os.close, self._handle)
            self._handle = None


async def upload_slice(
    client: TelegramClient,
    reader,
    length: int,
    file_name: str,
    on_progress: ProgressCallback | None = None,
    cancel: asyncio.Event | None = None,
    source: str = "",
) -> InputFile | InputFileBig:
    """Carica `length` byte presi da `reader` e restituisce l'handle Telegram.

    `reader` e un contesto asincrono con un metodo read(size): puo essere un file
    locale o un flusso rclone. In nessuno dei due casi si scrive su disco.
    """
    if length <= 0:
        raise ValueError("La fetta da caricare e vuota")

    part_count = math.ceil(length / UPLOAD_PART_SIZE)
    if part_count > MAX_PARTS:
        raise ValueError(
            f"La fetta richiede {part_count} parti, il massimo di protocollo e {MAX_PARTS}"
        )

    file_id = helpers.generate_random_long()
    is_big = length > BIG_FILE_THRESHOLD
    connections = connection_count(length)

    transferrer = ParallelTransferrer(client)
    senders = [
        _UploadSender(
            client,
            await transferrer._create_sender(),
            file_id,
            part_count,
            is_big,
            index,
            connections,
            on_progress,
        )
        for index in range(connections)
    ]

    md5 = hashlib.md5() if not is_big else None
    try:
        async with reader:
            remaining = length
            ticker = 0
            while remaining > 0:
                if cancel is not None and cancel.is_set():
                    raise asyncio.CancelledError("Upload interrotto")
                size = min(UPLOAD_PART_SIZE, remaining)
                chunk = await reader.read(size)
                if not chunk:
                    raise OSError(
                        f"La sorgente {source or file_name} si e esaurita prima del "
                        f"previsto: mancano {remaining} byte"
                    )
                if md5 is not None:
                    md5.update(chunk)
                await senders[ticker].send(chunk)
                ticker = (ticker + 1) % connections
                remaining -= len(chunk)

            for sender in senders:
                await sender.drain()
    finally:
        await asyncio.gather(
            *(sender.disconnect() for sender in senders), return_exceptions=True
        )

    if is_big:
        return InputFileBig(file_id, part_count, file_name)
    return InputFile(file_id, part_count, file_name, md5.hexdigest())


async def download_document(
    client: TelegramClient,
    document: Document,
    out_fd: int,
    base_offset: int,
    on_progress: ProgressCallback | None = None,
    cancel: asyncio.Event | None = None,
) -> int:
    """Scarica un documento in parallelo scrivendolo a `base_offset` dentro `out_fd`.

    Usa scritture posizionali, quindi i sender possono scrivere contemporaneamente
    senza contendersi la posizione del file.
    """
    location = InputDocumentFileLocation(
        id=document.id,
        access_hash=document.access_hash,
        file_reference=document.file_reference,
        thumb_size="",
    )
    size = document.size
    connections = connection_count(size)
    part_count = math.ceil(size / DOWNLOAD_PART_SIZE)
    stride = DOWNLOAD_PART_SIZE * connections

    transferrer = ParallelTransferrer(client, document.dc_id)
    senders = [
        _DownloadSender(
            client,
            await transferrer._create_sender(),
            location,
            index * DOWNLOAD_PART_SIZE,
            DOWNLOAD_PART_SIZE,
            stride,
            # Parti assegnate a questo sender: index, index+connections, ...
            len(range(index, part_count, connections)),
        )
        for index in range(connections)
    ]

    written = 0
    lock = asyncio.Lock()

    async def pump(sender: _DownloadSender) -> None:
        nonlocal written
        while True:
            if cancel is not None and cancel.is_set():
                raise asyncio.CancelledError("Download interrotto")
            item = await sender.next()
            if item is None:
                return
            offset, data = item
            if not data:
                return
            await asyncio.to_thread(os.pwrite, out_fd, data, base_offset + offset)
            async with lock:
                written += len(data)
            if on_progress is not None:
                on_progress(len(data))

    try:
        await asyncio.gather(*(pump(sender) for sender in senders))
    finally:
        await asyncio.gather(
            *(sender.disconnect() for sender in senders), return_exceptions=True
        )

    return written
