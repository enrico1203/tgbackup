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
from datetime import UTC, datetime, timedelta
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select

from ..deps import ActiveUserDep, SessionDep
from ..models import (
    READABLE_STATES,
    Channel,
    FileEntry,
    FilePart,
    SyncJob,
    TelegramAccount,
    User,
)
from ..schemas import (
    DownloadTicketIn,
    DownloadTicketOut,
    ExplorerFetchFolderIn,
    ExplorerFetchIn,
    ExplorerFile,
    ExplorerFolder,
    ExplorerListing,
    RestoreFolderOut,
    RestoreOut,
)
from ..security import (
    DOWNLOAD_TICKET_TTL_SECONDS,
    create_download_ticket,
    decode_download_ticket,
)
from ..sync.restore import cancel_restore, restore_file, restore_folder
from ..telegram.fast_transfer import MAX_CONNECTIONS, stream_document
from ..telegram.flood import FloodGate
from ..telegram.manager import manager
from ..telegram.throttle import installation_limiter

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
    q: str = "",
    trash: bool = False,
    as_of: datetime | None = None,
    offset: int = 0,
    limit: int = Query(500, ge=1, le=2000),
) -> ExplorerListing:
    """The channel as a folder tree: as it is now, as its trash, or as it was on a day.

    The three are one query with a different filter on the state. `as_of` is the reason
    the trash exists at all: a file is in the channel on a given day if it had been
    uploaded by then and had not yet been trashed, which is exactly what the two dates on
    the row say. What it cannot give back is the previous content of a file modified
    since: a modification deletes the old parts and uploads new ones, so only one version
    of a path is ever in the channel.
    """
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
        FileEntry.trashed_at,
        FileEntry.job_id,
    ).where(
        FileEntry.job_id.in_(select(SyncJob.id).where(SyncJob.channel_id == channel_id)),
    )
    if as_of is not None:
        moment = as_of if as_of.tzinfo is not None else as_of.replace(tzinfo=UTC)
        stmt = stmt.where(
            FileEntry.state.in_(READABLE_STATES),
            FileEntry.uploaded_at.is_not(None),
            FileEntry.uploaded_at <= moment,
            (FileEntry.trashed_at.is_(None)) | (FileEntry.trashed_at > moment),
        )
    elif trash:
        stmt = stmt.where(FileEntry.state == "trashed")
    else:
        stmt = stmt.where(FileEntry.state == "uploaded")
    if prefix:
        stmt = stmt.where(
            FileEntry.rel_path >= prefix,
            FileEntry.rel_path < prefix + PATH_CEILING,
        )

    rows = (await session.execute(stmt)).all()

    # One query for the whole channel rather than one per row: a channel is written by a
    # handful of jobs, and only the trashed rows will ask.
    retention = dict(
        await session.execute(
            select(SyncJob.id, SyncJob.trash_days).where(SyncJob.channel_id == channel_id)
        )
    )

    # Two jobs writing the same path into one channel: the most recently uploaded wins,
    # which is the rule the file browser and the download jobs already apply.
    unique: dict[str, tuple] = {}
    for row in rows:
        current = unique.get(row.rel_path)
        if current is None or (row.uploaded_at or NO_DATE) >= (current.uploaded_at or NO_DATE):
            unique[row.rel_path] = row

    # A search looks through this folder and everything below it, which is what a search
    # box in a file manager does. The rows have already been read for the listing, so it
    # costs no second query: what changes is that a match at any depth is kept, instead
    # of only the first segment after the prefix.
    needle = q.strip().casefold()

    folders: dict[str, list[int]] = {}
    files: list[ExplorerFile] = []
    bytes_total = 0

    def as_file(row, rel_path: str, fallback: str) -> ExplorerFile:
        # The day the messages go for good, which is the one thing a trashed row cannot
        # say on its own: the retention belongs to the job, not to the file.
        purge_at = None
        if row.trashed_at is not None:
            days = retention.get(row.job_id, 0)
            if days > 0:
                trashed_at = row.trashed_at
                if trashed_at.tzinfo is None:
                    trashed_at = trashed_at.replace(tzinfo=UTC)
                purge_at = trashed_at + timedelta(days=days)
        return ExplorerFile(
            id=row.id,
            name=row.name or fallback,
            path=rel_path,
            size=row.size,
            parts=row.parts_total,
            uploaded_at=row.uploaded_at,
            trashed_at=row.trashed_at,
            purge_at=purge_at,
            job_id=row.job_id,
        )

    for rel_path, row in unique.items():
        bytes_total += row.size
        rest = rel_path[len(prefix) :]

        if needle:
            name = row.name or rest.rsplit("/", 1)[-1]
            if needle in name.casefold():
                files.append(as_file(row, rel_path, name))
            # Every folder on the way down whose own name matches, with the weight of
            # what it holds. A folder deep in the tree is worth finding too.
            branches = rest.split("/")[:-1]
            for depth, segment in enumerate(branches):
                if needle not in segment.casefold():
                    continue
                found = f"{prefix}{'/'.join(branches[: depth + 1])}"
                counters = folders.get(found)
                if counters is None:
                    folders[found] = [1, row.size]
                else:
                    counters[0] += 1
                    counters[1] += row.size
            continue

        cut = rest.find("/")
        if cut == -1:
            files.append(as_file(row, rel_path, rest))
            continue

        name = rest[:cut]
        counters = folders.get(f"{prefix}{name}")
        if counters is None:
            folders[f"{prefix}{name}"] = [1, row.size]
        else:
            counters[0] += 1
            counters[1] += row.size

    # Folders first and then files, each in alphabetical order ignoring case, which is
    # the order every file manager shows and the only one that reads as sorted.
    ordered_folders = [
        ExplorerFolder(
            name=found.rsplit("/", 1)[-1],
            path=found,
            files=counters[0],
            bytes=counters[1],
        )
        for found, counters in sorted(
            folders.items(), key=lambda item: item[0].rsplit("/", 1)[-1].casefold()
        )
    ]
    files.sort(key=lambda item: item.name.casefold())

    entries_total = len(ordered_folders) + len(files)
    page = ([*ordered_folders, *files])[offset : offset + limit]

    return ExplorerListing(
        channel_id=channel_id,
        channel_title=channel.title,
        path=folder,
        query=q.strip(),
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
    if entry.state not in READABLE_STATES:
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


@router.post("/fetch", response_model=RestoreOut)
async def fetch_to_destination(payload: ExplorerFetchIn, _: ActiveUserDep) -> RestoreOut:
    """Writes one file to a folder or a remote of this installation, not to the browser.

    This is what a download job does, for a single file: same destinations, same sinks,
    same refusal to write into a folder mounted read-only. The transfer runs in the
    background and its progress travels on the same socket as a restore, because that is
    exactly what it is.
    """
    try:
        restore_id = await restore_file(payload.file_id, payload.dest_type, payload.path)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    from ..sync.progress import hub

    progress = hub.restores[restore_id]
    return RestoreOut(restore_id=restore_id, target_path=progress.target_path)


@router.post("/fetch-folder", response_model=RestoreFolderOut)
async def fetch_folder(payload: ExplorerFetchFolderIn, _: ActiveUserDep) -> RestoreFolderOut:
    """Writes a whole folder of the channel to a destination of this installation.

    One operation and not one per file: the destination is prepared once, a file that
    fails does not stop the others, and the whole thing has a single progress and a stop
    button. What it writes is what the listing of that folder shows, the trash included,
    each file keeping its path relative to the folder that was asked for.
    """
    try:
        restore_id, files, total = await restore_folder(
            payload.channel_id, _clean_path(payload.path), payload.dest_type, payload.path_to
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    from ..sync.progress import hub

    return RestoreFolderOut(
        restore_id=restore_id,
        target_path=hub.restores[restore_id].target_path,
        files=files,
        bytes=total,
    )


@router.post("/restore/{restore_id}/cancel", status_code=status.HTTP_204_NO_CONTENT)
async def stop_restore(restore_id: str, _: ActiveUserDep) -> None:
    """Stops a restore at its next part. What is already written stays where it is."""
    if not cancel_restore(restore_id):
        raise HTTPException(status.HTTP_409_CONFLICT, "That restore is not running")


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

    # No panel to show it on, this one goes straight to the browser, but the gate is what
    # keeps a flood wait from ending the response: without it the senders would raise and
    # the file would arrive truncated, which is worse than arriving late.
    flood = FloodGate(f"browser download of {label}")

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
                stream_document(
                    client,
                    message.document,
                    max_connections=max_connections,
                    # A download to a browser spends the same line as everything else.
                    limiter=installation_limiter(),
                    flood=flood,
                )
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
    if entry.state not in READABLE_STATES:
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
