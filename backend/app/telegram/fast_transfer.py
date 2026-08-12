"""Parallel transfer over MTProto.

Telethon uploads a file's chunks one after another on a single connection, so the speed
is bound by the round trip rather than by bandwidth. Here several MTProtoSender share the
same auth key and the parts are spread across them.

Protocol constraints to respect:
  - at most 20 connections per data center, beyond that Telegram blocks them all
  - a part is at most 512KB, and every part but the last must be full
  - at most 8000 parts per file, that is 3.90625 GiB with 512KB parts
  - files over 10MB use SaveBigFilePartRequest and carry no md5

The reader stays sequential while the senders work in parallel, which is what allows an
arbitrary slice of a large file to be uploaded without producing temporary files.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import math
import os
from collections.abc import Callable

from telethon import TelegramClient, helpers
from telethon.errors import (
    FloodPremiumWaitError,
    FloodWaitError,
    InterdcCallErrorError,
    InterdcCallRichErrorError,
    RpcCallFailError,
    RpcMcgetFailError,
    ServerError,
    TimedOutError,
)
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

from ..config import settings
from .flood import FloodGate
from .throttle import ChainedLimiter

log = logging.getLogger(__name__)

MAX_CONNECTIONS = 20
UPLOAD_PART_SIZE = 512 * 1024
DOWNLOAD_PART_SIZE = 1024 * 1024
BIG_FILE_THRESHOLD = 10 * 1024 * 1024
MAX_PARTS = 8000

# How many parts an ordered download may hold in memory while waiting for the missing
# one. It bounds the buffer at 32MB per file, whatever the size of the file.
STREAM_WINDOW_PARTS = 32

ProgressCallback = Callable[[int], None]


def connection_count(size: int, maximum: int = MAX_CONNECTIONS) -> int:
    """One connection per 5MB, up to the ceiling. Below 5MB parallelism buys nothing."""
    if size <= 0:
        return 1
    return max(1, min(maximum, math.ceil(size / (5 * 1024 * 1024))))


async def call_with_timeout(awaitable, what: str, cancel=None, timeout: float | None = None):
    """Awaits a Telegram call, giving up if the answer never arrives or the run is stopped.

    `MTProtoSender.send` hands back a future that is resolved by the receive loop when
    the response comes in, and nothing anywhere resolves it if the response never does.
    A connection dropped at the wrong moment, which is what a flood wait storm produces,
    therefore leaves the call pending for ever: the reader stops being read, the source
    blocks with a full buffer, the job keeps its account slot and the transfer sits at
    zero without a single error to show for it. Turning that into an exception is what
    lets the retry above take over, rebuild the reader and start the slice again.

    The stop signal is the other way out, and it is here because the timeout is far too
    long to be one. A stop is read between parts, which is never reached while a part is
    pending, so exactly in the state this timeout exists for the button did nothing at
    all for ten minutes: the transfer showed zero bytes, Stop looked broken, and the job
    went on holding the account. Racing the call against the signal costs nothing, since
    a stopped transfer has no use for the answer: the parts already sent are recorded and
    a slice abandoned here is one the next run does again from its start.

    `timeout` is for the caller that knows better what its call is worth. A part has its
    own, much shorter, since silence there is answered by sending it again rather than by
    giving up on everything.
    """
    limit = settings.telegram_request_timeout if timeout is None else timeout
    call = asyncio.ensure_future(awaitable)
    stop = asyncio.ensure_future(cancel.wait()) if cancel is not None else None
    try:
        done, _ = await asyncio.wait(
            {call} if stop is None else {call, stop},
            timeout=limit,
            return_when=asyncio.FIRST_COMPLETED,
        )
        if call in done:
            return call.result()
        if stop is not None and stop in done:
            raise asyncio.CancelledError(f"Stopped while waiting for {what}")
        raise TimeoutError(f"Telegram did not answer for {what} within {limit:.0f}s")
    finally:
        if stop is not None:
            stop.cancel()
        if not call.done():
            call.cancel()


# What Telethon retries by itself inside `_call`: the server is having a moment and the
# same request usually goes through a second later.
TRANSIENT_ERRORS = (
    ServerError,
    RpcCallFailError,
    RpcMcgetFailError,
    InterdcCallErrorError,
    InterdcCallRichErrorError,
    TimedOutError,
)
TRANSIENT_RETRIES = 5
TRANSIENT_DELAY = 2.0

# How many times one part may be sent again after silence before the slice is given up.
# Three is a connection that was unlucky; beyond that the account is being held back and
# a fourth attempt would only be a fourth request against something that is not answering.
SILENCE_RETRIES = 3


async def renew_sender(factory, old: MTProtoSender) -> MTProtoSender:
    """Drops a connection that stopped answering and builds another one in its place.

    The old one goes first and not after: twenty connections per data center is a ceiling
    Telegram applies to all of them together, and a renewal that overlapped would be the
    twenty first. Without a factory there is nothing to build, and the part is simply sent
    again on the connection it has, which is the lesser half of the same answer.
    """
    if factory is None:
        return old
    try:
        await old.disconnect()
    except Exception:
        pass
    return await factory()


async def send_transfer_request(
    sender: MTProtoSender,
    request,
    what: str,
    flood: FloodGate | None,
    cancel=None,
    renew=None,
):
    """Sends one part request and decides by itself what to do when it comes back badly.

    `client._call` is deliberately bypassed. It would give three things this loop has to
    take back. It counts a flood wait as a used attempt, so a limit that outlasts five
    sleeps turns into `ValueError: Request was unsuccessful 6 time(s)`, an exception that
    names nothing and lets the caller destroy a slice that was only being asked to wait.
    It records the wait in `_flood_waited_requests`, keyed by request type on the shared
    client, so every later part of every job sleeps up front for a limit it has not hit.
    And it decides the delay per request, while the thing being limited is the account:
    the gate is what makes twenty connections wait as one.

    A raw `MTProtoSender.send` hands back the future the receive loop resolves, raising
    the actual RPC error, which is all this needs.

    Silence is the third case, and the one that has to be answered here rather than by the
    caller. Telegram takes the part, acknowledges it at the level of the connection and
    never replies, with no flood wait and no error: measured on this installation after
    two or three hundred megabytes on a fresh set of senders. The old answer was the ten
    minute timeout followed by the whole slice being read and uploaded again from its
    first byte, which on a file of several gigabytes never gets to the end. So the part
    gets its own deadline and, when it passes, `renew` builds this sender's connection
    again and the same part is sent on it. Re-sending a part is free of consequence: the
    server keys it by file id and index, so the second one lands exactly where the first
    was meant to. A second silence in a row is no longer one connection being unlucky,
    and the gate is closed for every connection of the run, which is the same answer a
    flood wait gets because it is the same thing being said less politely.
    """
    attempt = 0
    silences = 0
    while True:
        if flood is not None:
            await flood.hold()
        try:
            result = await call_with_timeout(
                sender.send(request), what, cancel, settings.telegram_part_timeout
            )
        except (TimeoutError, OSError) as exc:
            # Silence and a broken connection are the same event seen from two sides. When
            # the set of senders is cut, some are left waiting for an answer that never
            # comes and the others are told at once, as ConnectionError, that there is
            # nothing to write to; observed together, fifteen parts timing out while four
            # raised. Answering only the first half meant the four killed the slice half a
            # second after the fifteen had been saved, so the whole file was read again
            # from its first byte anyway. Both are answered by the same thing: this
            # connection is finished, build another and send the part on it.
            silences += 1
            if flood is not None:
                # Counted whether or not it is answered by a wait: this is the account
                # being held back, and it is what the job says out loud.
                flood.cut()
            if renew is None or silences > SILENCE_RETRIES:
                raise
            log.warning(
                "%s, sending it again on a new connection (silence %d of %d)",
                f"No answer for {what} in {settings.telegram_part_timeout:.0f}s"
                if isinstance(exc, TimeoutError)
                else f"The connection carrying {what} broke ({exc})",
                silences,
                SILENCE_RETRIES,
            )
            if silences > 1 and flood is not None:
                await flood.stalled()
            sender = await renew(sender)
            continue
        except (FloodWaitError, FloodPremiumWaitError) as exc:
            if flood is None:
                raise
            await flood.flooded(exc.seconds)
            continue
        except TRANSIENT_ERRORS as exc:
            attempt += 1
            if attempt >= TRANSIENT_RETRIES:
                raise
            log.warning(
                "Telegram is having internal issues on %s (%s), attempt %d",
                what, type(exc).__name__, attempt + 1,
            )
            await asyncio.sleep(TRANSIENT_DELAY)
            continue
        if flood is not None:
            flood.cleared()
        return result


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
        flood: FloodGate | None = None,
        cancel=None,
        new_sender=None,
    ) -> None:
        self._client = client
        self._sender = sender
        self._stride = stride
        self._on_part = on_part
        self._flood = flood
        self._cancel = cancel
        # How this sender builds itself a new connection when the one it has stops
        # answering. Without it silence has no answer but giving up the slice.
        self._new_sender = new_sender
        self._pending: asyncio.Task | None = None
        if big:
            self._request = SaveBigFilePartRequest(file_id, index, part_count, b"")
        else:
            self._request = SaveFilePartRequest(file_id, index, b"")

    async def send(self, data: bytes) -> None:
        # Parts belonging to the same sender must stay in order: the previous one is
        # awaited before queueing the next, while different senders proceed in parallel.
        if self._pending is not None:
            await self._pending
        self._pending = asyncio.create_task(self._send(data))

    async def _send(self, data: bytes) -> None:
        self._request.bytes = data
        await send_transfer_request(
            self._sender,
            self._request,
            f"part {self._request.file_part}",
            self._flood,
            self._cancel,
            self._renew,
        )
        self._request.file_part += self._stride
        if self._on_part is not None:
            self._on_part(len(data))

    async def _renew(self, old: MTProtoSender) -> MTProtoSender:
        self._sender = await renew_sender(self._new_sender, old)
        return self._sender

    async def drain(self) -> None:
        if self._pending is not None:
            await self._pending
            self._pending = None

    async def abort(self) -> None:
        """Gives up the part still in flight, when the slice is being abandoned.

        The part travels in a task of its own, so it outlives the loop that queued it:
        without this its failure would surface later as an exception nobody retrieved,
        once per sender and once per attempt.
        """
        if self._pending is None:
            return
        self._pending.cancel()
        await asyncio.gather(self._pending, return_exceptions=True)
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
        flood: FloodGate | None = None,
        cancel=None,
        new_sender=None,
    ) -> None:
        self._client = client
        self._sender = sender
        self._request = GetFileRequest(location, offset, limit)
        self._stride = stride
        self._remaining = count
        self._flood = flood
        self._cancel = cancel
        self._new_sender = new_sender

    async def _renew(self, old: MTProtoSender) -> MTProtoSender:
        self._sender = await renew_sender(self._new_sender, old)
        return self._sender

    async def next(self) -> tuple[int, bytes] | None:
        if self._remaining <= 0:
            return None
        offset = self._request.offset
        result = await send_transfer_request(
            self._sender,
            self._request,
            f"the part at offset {offset}",
            self._flood,
            self._cancel,
            self._renew,
        )
        self._remaining -= 1
        self._request.offset += self._stride
        return offset, result.bytes

    async def disconnect(self) -> None:
        await self._sender.disconnect()


class ParallelTransferrer:
    def __init__(self, client: TelegramClient, dc_id: int | None = None) -> None:
        self._client = client
        self._dc_id = dc_id or client.session.dc_id
        # On the home data center the existing auth key is reused; on another one the
        # authorization must be exported once and then shared by all the senders.
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
    """Sequential reader over a byte range of a local file.

    It exposes the same interface as the rclone reader, so the uploader does not know
    where the bytes come from. Positional reads are used, so the file cursor never moves.
    """

    def __init__(self, path: str, offset: int, length: int) -> None:
        self._path = path
        self._position = offset
        self._remaining = length
        self._handle: int | None = None

    async def __aenter__(self) -> LocalSliceReader:
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
    cancel=None,
    source: str = "",
    max_connections: int = MAX_CONNECTIONS,
    limiter: ChainedLimiter | None = None,
    flood: FloodGate | None = None,
) -> InputFile | InputFileBig:
    """Uploads `length` bytes taken from `reader` and returns the Telegram handle.

    `reader` is an async context manager with a read(size) method: it can be a local file
    or an rclone stream. In neither case is anything written to disk.

    `limiter`, when there is one, is asked before every chunk goes to a sender. This loop
    is the only place the whole upload passes through, whatever the source and however
    many connections are open, which is what makes one limit here a limit on everything.

    `flood` is the shared answer to a flood wait: the senders all consult it, so a limit
    found by one holds the whole file rather than being met again by the other nineteen.
    """
    if length <= 0:
        raise ValueError("The slice to upload is empty")

    part_count = math.ceil(length / UPLOAD_PART_SIZE)
    if part_count > MAX_PARTS:
        raise ValueError(
            f"The slice needs {part_count} parts, the protocol maximum is {MAX_PARTS}"
        )

    file_id = helpers.generate_random_long()
    is_big = length > BIG_FILE_THRESHOLD
    # The ceiling comes from the caller: when several jobs upload on the same account,
    # the budget of 20 connections per data center has already been divided among them.
    # The ceiling of the caller, lowered by the gate for as long as Telegram keeps cutting
    # this run: twenty connections against an account being held back are twenty ways of
    # being refused at once. See flood.FloodGate.allowance.
    allowed = max_connections if flood is None else flood.allowance(max_connections)
    connections = connection_count(length, max(1, min(allowed, MAX_CONNECTIONS)))

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
            flood,
            cancel,
            transferrer._create_sender,
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
                    raise asyncio.CancelledError("Upload interrupted")
                size = min(UPLOAD_PART_SIZE, remaining)
                chunk = await reader.read(size)
                if not chunk:
                    raise OSError(
                        f"Source {source or file_name} ran out earlier than expected: "
                        f"{remaining} bytes missing"
                    )
                if md5 is not None:
                    md5.update(chunk)
                if limiter is not None:
                    await limiter.take(len(chunk))
                await senders[ticker].send(chunk)
                ticker = (ticker + 1) % connections
                remaining -= len(chunk)

            for sender in senders:
                await sender.drain()
    finally:
        # When one sender fails the others are still holding a part: they are dropped
        # before the connections go, or their tasks would outlive the upload. On the
        # way out of a slice that worked this finds nothing to do, drain emptied them.
        await asyncio.gather(
            *(sender.abort() for sender in senders), return_exceptions=True
        )
        await asyncio.gather(
            *(sender.disconnect() for sender in senders), return_exceptions=True
        )

    if is_big:
        return InputFileBig(file_id, part_count, file_name)
    return InputFile(file_id, part_count, file_name, md5.hexdigest())


def _file_location(document: Document) -> InputDocumentFileLocation:
    return InputDocumentFileLocation(
        id=document.id,
        access_hash=document.access_hash,
        file_reference=document.file_reference,
        thumb_size="",
    )


async def _download_senders(
    client: TelegramClient,
    document: Document,
    connections: int,
    flood: FloodGate | None = None,
    cancel=None,
) -> list[_DownloadSender]:
    """One sender per connection, each taking one part out of `connections`."""
    location = _file_location(document)
    part_count = math.ceil(document.size / DOWNLOAD_PART_SIZE)
    stride = DOWNLOAD_PART_SIZE * connections
    transferrer = ParallelTransferrer(client, document.dc_id)
    return [
        _DownloadSender(
            client,
            await transferrer._create_sender(),
            location,
            index * DOWNLOAD_PART_SIZE,
            DOWNLOAD_PART_SIZE,
            stride,
            # Parts assigned to this sender: index, index + connections, and so on.
            len(range(index, part_count, connections)),
            flood,
            cancel,
            transferrer._create_sender,
        )
        for index in range(connections)
    ]


async def download_document(
    client: TelegramClient,
    document: Document,
    out_fd: int,
    base_offset: int,
    on_progress: ProgressCallback | None = None,
    cancel=None,
    max_connections: int = MAX_CONNECTIONS,
    limiter: ChainedLimiter | None = None,
    flood: FloodGate | None = None,
) -> int:
    """Downloads a document in parallel, writing it at `base_offset` inside `out_fd`.

    Positional writes are used, so the senders can write at the same time without
    fighting over the file position.
    """
    size = document.size
    allowed = max_connections if flood is None else flood.allowance(max_connections)
    connections = connection_count(size, max(1, min(allowed, MAX_CONNECTIONS)))
    senders = await _download_senders(client, document, connections, flood, cancel)

    written = 0
    lock = asyncio.Lock()

    async def pump(sender: _DownloadSender) -> None:
        nonlocal written
        while True:
            if cancel is not None and cancel.is_set():
                raise asyncio.CancelledError("Download interrupted")
            item = await sender.next()
            if item is None:
                return
            offset, data = item
            if not data:
                return
            # After the part arrived rather than before asking for it: a sender that has
            # to wait here does not ask for its next one, which is what holds the average
            # down. The overshoot is bounded by one part per connection.
            if limiter is not None:
                await limiter.take(len(data))
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


async def stream_document(
    client: TelegramClient,
    document: Document,
    on_progress: ProgressCallback | None = None,
    cancel=None,
    max_connections: int = MAX_CONNECTIONS,
    limiter: ChainedLimiter | None = None,
    flood: FloodGate | None = None,
):
    """Yields the bytes of a document in order, downloading them in parallel.

    `download_document` writes each part where it belongs with pwrite, which a pipe cannot
    do: a destination reached through `rclone rcat` needs the bytes in order, from the
    first to the last. Parallelism is kept anyway, chunks that arrive early wait in a
    window of `STREAM_WINDOW_PARTS` parts until the missing one shows up, so memory stays
    bounded whatever the size of the file.

    No sender can block for good: a sender only waits when it is a whole window ahead of
    what has been yielded, and the part that is being waited for always belongs to a
    sender that is not ahead at all, because a single sender delivers its own parts in
    order.
    """
    size = document.size
    allowed = max_connections if flood is None else flood.allowance(max_connections)
    connections = connection_count(size, max(1, min(allowed, MAX_CONNECTIONS)))
    senders = await _download_senders(client, document, connections, flood, cancel)

    window = STREAM_WINDOW_PARTS * DOWNLOAD_PART_SIZE
    ready: dict[int, bytes] = {}
    emitted = 0
    condition = asyncio.Condition()

    async def pump(sender: _DownloadSender) -> None:
        try:
            while True:
                if cancel is not None and cancel.is_set():
                    raise asyncio.CancelledError("Download interrupted")
                item = await sender.next()
                if item is None:
                    return
                offset, data = item
                if not data:
                    return
                if limiter is not None:
                    await limiter.take(len(data))
                async with condition:
                    while offset - emitted >= window:
                        await condition.wait()
                    ready[offset] = data
                    condition.notify_all()
        finally:
            # A sender that stops, whether it is done or has failed, has to wake the
            # consumer up: otherwise a missing part would leave it waiting for ever.
            async with condition:
                condition.notify_all()

    tasks = [asyncio.create_task(pump(sender)) for sender in senders]
    try:
        while emitted < size:
            async with condition:
                while emitted not in ready:
                    if all(task.done() for task in tasks):
                        for task in tasks:
                            if task.exception() is not None:
                                raise task.exception()
                        raise RuntimeError(
                            f"The document ended at {emitted} bytes out of {size}"
                        )
                    await condition.wait()
                chunk = ready.pop(emitted)
                emitted += len(chunk)
                condition.notify_all()

            # Outside the lock: while the caller writes this chunk to its destination the
            # senders have to keep downloading the next ones.
            if on_progress is not None:
                on_progress(len(chunk))
            yield chunk
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await asyncio.gather(
            *(sender.disconnect() for sender in senders), return_exceptions=True
        )
