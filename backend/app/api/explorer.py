"""File explorer over the channel index.

What is browsed here is the database, not Telegram. The index already holds the path, the
size and the parts of every file, so opening a folder costs one query and no API call, and
a channel with 200,000 files opens as fast as an empty one. Telegram is contacted at one
moment only, and it is the moment a file is actually downloaded.

A file split across several messages is one file here, exactly as it was at the source.
The split belongs to the transport: the explorer shows what was backed up.
"""

from __future__ import annotations

import logging
import mimetypes
from contextlib import aclosing
from datetime import UTC, datetime
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select

from ..deps import ActiveUserDep, SessionDep
from ..models import Channel, FileEntry, FilePart, SyncJob, TelegramAccount, User
from ..schemas import (
    DownloadTicketIn,
    DownloadTicketOut,
    ExplorerFile,
    ExplorerFolder,
    ExplorerListing,
)
from ..security import (
    DOWNLOAD_TICKET_TTL_SECONDS,
    create_download_ticket,
    decode_download_ticket,
)
from ..telegram.fast_transfer import MAX_CONNECTIONS, stream_document
from ..telegram.manager import manager

router = APIRouter(prefix="/api/explorer", tags=["explorer"])
log = logging.getLogger(__name__)

# The largest code point there is: `prefix <= rel_path < prefix + this` is every path
# inside the folder and nothing else. `LIKE 'prefix%'` would return the same rows, but
# SQLite cannot answer it from the index, because LIKE ignores the case of ASCII letters
# and the index does not.
PATH_CEILING = "\U0010ffff"

# A file with no upload date loses against one that has any, when the same path was
# uploaded by two jobs into the same channel.
NO_DATE = datetime.min.replace(tzinfo=UTC)


def _clean_path(path: str) -> str:
    """Normalises what arrives from the browser into the form kept in `rel_path`.

    Backslashes, repeated slashes and `..` are dropped rather than rejected: this is a
    prefix over a text column and not a path on a filesystem, so there is nothing to
    escape from, and a malformed path is better shown as the folder above than as an
    error the user cannot act on.
    """
    segments = [
        segment
        for segment in path.replace("\\", "/").split("/")
        if segment not in ("", ".", "..")
    ]
    return "/".join(segments)


@router.get("/list", response_model=ExplorerListing)
async def list_folder(
    session: SessionDep,
    _: ActiveUserDep,
    channel_id: int,
    path: str = "",
    offset: int = 0,
    limit: int = Query(500, ge=1, le=2000),
) -> ExplorerListing:
    channel = await session.get(Channel, channel_id)
    if channel is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Channel not found")

    folder = _clean_path(path)
    prefix = f"{folder}/" if folder else ""

    # A channel can be the destination of several jobs, and what it holds is the union of
    # their entries. Only files that made it to Telegram: the rest are not there to open.
    stmt = select(
        FileEntry.id,
        FileEntry.rel_path,
        FileEntry.name,
        FileEntry.size,
        FileEntry.parts_total,
        FileEntry.uploaded_at,
        FileEntry.job_id,
    ).where(
        FileEntry.job_id.in_(select(SyncJob.id).where(SyncJob.channel_id == channel_id)),
        FileEntry.state == "uploaded",
    )
    if prefix:
        stmt = stmt.where(
            FileEntry.rel_path >= prefix,
            FileEntry.rel_path < prefix + PATH_CEILING,
        )

    rows = (await session.execute(stmt)).all()

    # Two jobs writing the same path into one channel: the most recently uploaded wins,
    # which is the rule the file browser and the download jobs already apply.
    unique: dict[str, tuple] = {}
    for row in rows:
        current = unique.get(row.rel_path)
        if current is None or (row.uploaded_at or NO_DATE) >= (current.uploaded_at or NO_DATE):
            unique[row.rel_path] = row

    folders: dict[str, list[int]] = {}
    files: list[ExplorerFile] = []
    bytes_total = 0

    for rel_path, row in unique.items():
        bytes_total += row.size
        rest = rel_path[len(prefix) :]
        cut = rest.find("/")
        if cut == -1:
            files.append(
                ExplorerFile(
                    id=row.id,
                    name=row.name or rest,
                    path=rel_path,
                    size=row.size,
                    parts=row.parts_total,
                    uploaded_at=row.uploaded_at,
                    job_id=row.job_id,
                )
            )
            continue

        name = rest[:cut]
        counters = folders.get(name)
        if counters is None:
            folders[name] = [1, row.size]
        else:
            counters[0] += 1
            counters[1] += row.size

    # Folders first and then files, each in alphabetical order ignoring case, which is
    # the order every file manager shows and the only one that reads as sorted.
    ordered_folders = [
        ExplorerFolder(
            name=name,
            path=f"{prefix}{name}",
            files=counters[0],
            bytes=counters[1],
        )
        for name, counters in sorted(folders.items(), key=lambda item: item[0].casefold())
    ]
    files.sort(key=lambda item: item.name.casefold())

    entries_total = len(ordered_folders) + len(files)
    page = ([*ordered_folders, *files])[offset : offset + limit]

    return ExplorerListing(
        channel_id=channel_id,
        channel_title=channel.title,
        path=folder,
        folders=[entry for entry in page if isinstance(entry, ExplorerFolder)],
        files=[entry for entry in page if isinstance(entry, ExplorerFile)],
        files_total=len(unique),
        bytes_total=bytes_total,
        entries_total=entries_total,
        offset=offset,
        limit=limit,
    )


@router.post("/ticket", response_model=DownloadTicketOut)
async def create_ticket(
    payload: DownloadTicketIn, session: SessionDep, user: ActiveUserDep
) -> DownloadTicketOut:
    """Issues the pass the browser then uses to download, and nothing else.

    The download itself is a plain navigation, where no Authorization header can be sent.
    Rather than putting the session token in a URL, where the browser history and the
    nginx log would keep it, one is minted here for this file and five minutes.
    """
    entry = await session.get(FileEntry, payload.file_id)
    if entry is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "File not found")
    if entry.state != "uploaded":
        raise HTTPException(
            status.HTTP_409_CONFLICT, "The file has not been uploaded to Telegram"
        )

    # Checked here as well as at the download: this is where the interface can show what
    # is wrong, instead of the browser reporting a download that failed for no reason.
    job = await session.get(SyncJob, entry.job_id)
    if job is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "The job of this file no longer exists")
    await _connected_client(job.account_id)

    ticket = create_download_ticket(user.id, entry.id)
    return DownloadTicketOut(
        url=f"/api/explorer/download/{entry.id}?ticket={quote(ticket)}",
        name=entry.name,
        size=entry.size,
        expires_in=DOWNLOAD_TICKET_TTL_SECONDS,
    )


def _content_disposition(name: str) -> str:
    """Both spellings of the file name, because neither works everywhere on its own.

    `filename*` carries the real name in UTF-8 and is what every current browser reads;
    the plain `filename` is the fallback, and anything outside ASCII is replaced there
    rather than sent raw, which would produce an unparsable header.
    """
    fallback = name.encode("ascii", "replace").decode("ascii").replace('"', "_")
    return f"attachment; filename=\"{fallback}\"; filename*=UTF-8''{quote(name)}"


async def _connected_client(account_id: int):
    """The client, or a clean error before a single header has been sent.

    Once a `StreamingResponse` has started there is no way back: the browser has the 200
    and the length, and a failure there is a download that dies with no reason given. The
    account is therefore connected first, and a failure to do so is an ordinary 409.
    """
    try:
        return await manager.get_client(account_id)
    except Exception as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"The Telegram account of this file is not usable: {exc}",
        ) from exc


async def _stream_file(
    client,
    account_id: int,
    peer,
    parts: list[tuple[int, int, int]],
    label: str,
    concurrency: int,
    max_connections: int,
):
    """Yields the whole file, one part after another, in order.

    The transfer semaphore of the account is taken for the duration: the ceiling of 20
    connections per data center is shared with the jobs, and a download to the browser
    spends from the same budget. It is acquired inside the generator on purpose, since
    that is the only place whose exit is guaranteed, whether the file ends, the transfer
    fails or the browser goes away halfway.
    """
    lock = manager.transfer_lock(account_id, concurrency)
    if lock.locked():
        log.info("Browser download of %s waiting for account %d", label, account_id)

    async with lock:
        for index, (message_id, part_size, part_index) in enumerate(parts):
            messages = await client.get_messages(peer, ids=message_id)
            message = messages if not isinstance(messages, list) else messages[0]
            if message is None or message.document is None:
                raise RuntimeError(
                    f"Message {message_id} of part {part_index + 1} "
                    "no longer exists in the channel"
                )

            got = 0
            # aclosing and not a bare async for: if the browser disconnects halfway, the
            # generator has to be closed there and then, or its senders would stay
            # connected until the garbage collector gets to them, holding on to part of
            # the connection budget of the account.
            async with aclosing(
                stream_document(client, message.document, max_connections=max_connections)
            ) as stream:
                async for chunk in stream:
                    got += len(chunk)
                    yield chunk

            if got != part_size:
                raise RuntimeError(
                    f"Part {part_index + 1} of {label} returned {got} bytes "
                    f"instead of {part_size}"
                )
            log.debug("Browser download of %s: part %d of %d sent", label, index + 1, len(parts))


@router.get("/download/{file_id}")
async def download_file(
    file_id: int, session: SessionDep, ticket: str = Query("")
) -> StreamingResponse:
    """Streams a file to the browser, rebuilt from its parts as they arrive.

    Nothing is staged on disk: the parts are downloaded in parallel and handed over in
    order, the same mechanism a download job uses to write into an rclone pipe. There is
    no support for Range: an interrupted download starts again from the beginning, which
    is the price of never having the whole file anywhere but in flight.
    """
    user_id = decode_download_ticket(ticket, file_id)
    if user_id is None:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, "Download link expired, open the file again"
        )

    user = await session.get(User, user_id)
    if user is None or user.must_change_password:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Not allowed")

    entry = await session.get(FileEntry, file_id)
    if entry is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "File not found")
    if entry.state != "uploaded":
        raise HTTPException(
            status.HTTP_409_CONFLICT, "The file has not been uploaded to Telegram"
        )

    job = await session.get(SyncJob, entry.job_id)
    channel = await session.get(Channel, job.channel_id) if job else None
    if job is None or channel is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "The job of this file no longer exists")

    result = await session.execute(
        select(FilePart).where(FilePart.file_id == entry.id).order_by(FilePart.part_index)
    )
    # Plain tuples and not the rows: the session closes when this function returns, while
    # the generator below runs for as long as the download takes.
    parts = [(part.message_id, part.size, part.part_index) for part in result.scalars()]
    if not parts:
        raise HTTPException(status.HTTP_409_CONFLICT, "No parts recorded for this file")

    account = await session.get(TelegramAccount, job.account_id)
    concurrency = max(1, account.max_concurrent_jobs if account else 2)
    max_connections = max(1, MAX_CONNECTIONS // concurrency)
    peer = manager.input_peer(channel)
    client = await _connected_client(job.account_id)

    log.info(
        "Browser download of %s, %d bytes in %d part(s)", entry.rel_path, entry.size, len(parts)
    )

    return StreamingResponse(
        _stream_file(
            client, job.account_id, peer, parts, entry.rel_path, concurrency, max_connections
        ),
        media_type=mimetypes.guess_type(entry.name)[0] or "application/octet-stream",
        headers={
            "Content-Disposition": _content_disposition(entry.name),
            # The browser can only draw a progress bar if it knows where the end is.
            "Content-Length": str(entry.size),
            "Accept-Ranges": "none",
            "Cache-Control": "no-store",
        },
    )
