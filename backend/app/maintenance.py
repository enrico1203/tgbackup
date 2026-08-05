"""Channel maintenance: checking the index against the channel, and rebuilding it from it.

The index in the database is the only thing that knows what the files on Telegram are. Two
operations keep that relationship honest, in the two directions:

**Check** goes from the index to the channel. It asks Telegram for every message the index
records and reports the ones that are gone or that carry a document of the wrong size. A
message deleted by hand, or a channel emptied by somebody else, is otherwise discovered on
the day the file is needed. The damaged files can be marked for re-upload in the same pass:
they go through `stale`, which is the state the runner already uses for a file whose parts
have to be replaced.

**Rebuild** goes from the channel to the index. It reads every message and puts the index
back together out of the captions, which carry the name, the folder and the part number.
This is what turns a lost database into an inconvenience: the files are still up there, and
without the index nobody knows what they are. An export is still the better way to move a
channel, because it carries the dates too, but it has to have been made in advance.

What the channel cannot give back is the modification time, which lives nowhere in a
message. Rebuilt entries are written with `MTIME_UNKNOWN` and the first scan of the job
adopts the date of the source instead of taking every file for modified.

Both run as background tasks with their progress in memory: they take minutes on a large
channel, which is too long for a request to wait, and there is nothing to keep across a
restart, since running them again costs nothing and repeats the same work.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import select, update
from telethon.errors import FloodWaitError

from . import notify
from .db import SessionLocal
from .models import (
    MTIME_UNKNOWN,
    READABLE_STATES,
    Channel,
    FileEntry,
    FilePart,
    SyncJob,
    utcnow,
)
from .telegram.manager import manager
from .transfer import insert_files

log = logging.getLogger(__name__)

# Messages asked for in one round trip. The protocol takes no more than this.
MESSAGE_BATCH = 100

# A caption is cut at 1024 characters when a document is attached. One that is exactly that
# long was almost certainly truncated, so the path it carries cannot be trusted.
CAPTION_LIMIT = 1024

# How many finished tasks are kept, so a result can still be read after the page reloads.
HISTORY = 20

FILENAME_RE = re.compile(r"^FileName:\s*(.*)$", re.MULTILINE)
PATH_RE = re.compile(r"^Path:\s*(.*)$", re.MULTILINE)
PART_RE = re.compile(r"^Part:\s*(\d+)\s*/\s*(\d+)\s*$", re.MULTILINE)


class MaintenanceError(Exception):
    """Error meant to be shown directly to the user."""


@dataclass
class Task:
    id: str
    kind: str
    channel_id: int
    channel_title: str
    # running, done, error
    phase: str = "running"
    step: str = ""
    processed: int = 0
    total: int = 0
    started_at: datetime = field(default_factory=utcnow)
    finished_at: datetime | None = None
    result: dict = field(default_factory=dict)
    error: str | None = None

    def snapshot(self) -> dict:
        return {
            "id": self.id,
            "kind": self.kind,
            "channel_id": self.channel_id,
            "channel_title": self.channel_title,
            "phase": self.phase,
            "step": self.step,
            "processed": self.processed,
            "total": self.total,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "result": self.result,
            "error": self.error,
        }


class Registry:
    def __init__(self) -> None:
        self.tasks: dict[str, Task] = {}
        # The event loop keeps only a weak reference to a running task: without a strong
        # one here a check could be collected halfway through and stop with no trace.
        self._running: set[asyncio.Task] = set()

    def active_on(self, channel_id: int) -> Task | None:
        for task in self.tasks.values():
            if task.channel_id == channel_id and task.phase == "running":
                return task
        return None

    def start(self, kind: str, channel: Channel, coroutine_factory) -> Task:
        task = Task(
            id=uuid.uuid4().hex[:12],
            kind=kind,
            channel_id=channel.id,
            channel_title=channel.title,
        )
        self.tasks[task.id] = task
        self._prune()

        async def wrapper() -> None:
            try:
                await coroutine_factory(task)
                task.phase = "done"
            except Exception as exc:
                log.exception("Maintenance task %s on channel %d failed", kind, channel.id)
                task.phase = "error"
                task.error = str(exc)[:500]
            finally:
                task.finished_at = utcnow()

        running = asyncio.create_task(wrapper(), name=f"{kind}-{task.id}")
        self._running.add(running)
        running.add_done_callback(self._running.discard)
        return task

    def _prune(self) -> None:
        finished = sorted(
            (task for task in self.tasks.values() if task.phase != "running"),
            key=lambda item: item.finished_at or item.started_at,
        )
        for task in finished[: max(0, len(finished) - HISTORY)]:
            self.tasks.pop(task.id, None)


registry = Registry()


async def _channel(session, channel_id: int) -> Channel:
    channel = await session.get(Channel, channel_id)
    if channel is None:
        raise MaintenanceError("Channel not found")
    return channel


async def running_job_on(session, channel_id: int) -> str | None:
    """The name of a sync job running on this channel, if there is one."""
    return await session.scalar(
        select(SyncJob.name).where(
            SyncJob.channel_id == channel_id, SyncJob.status == "running"
        )
    )


async def _fetch_messages(client, peer, ids: list[int]) -> dict[int, object]:
    """The messages of those ids, in as few round trips as the protocol allows."""
    found: dict[int, object] = {}
    for start in range(0, len(ids), MESSAGE_BATCH):
        chunk = ids[start : start + MESSAGE_BATCH]
        try:
            messages = await client.get_messages(peer, ids=chunk)
        except FloodWaitError as exc:
            log.warning("Flood wait of %ss while reading the messages", exc.seconds)
            await asyncio.sleep(exc.seconds + 1)
            messages = await client.get_messages(peer, ids=chunk)
        for message_id, message in zip(chunk, messages, strict=True):
            found[message_id] = message
    return found


# Check


async def check_channel(task: Task, channel_id: int, repair: bool) -> None:
    async with SessionLocal() as session:
        channel = await _channel(session, channel_id)
        peer = manager.input_peer(channel)
        account_id = channel.account_id

        rows = await session.execute(
            select(FilePart.message_id, FilePart.size, FilePart.part_index, FileEntry.id)
            .join(FileEntry, FilePart.file_id == FileEntry.id)
            .where(
                FileEntry.job_id.in_(
                    select(SyncJob.id).where(SyncJob.channel_id == channel_id)
                ),
                # The trash counts: its messages are in the channel and are exactly the
                # ones somebody will come looking for, so a check that skipped them would
                # certify a channel it had not looked at.
                FileEntry.state.in_(READABLE_STATES),
            )
        )
        parts = list(rows)

    client = await manager.get_client(account_id)
    ids = sorted({part[0] for part in parts})
    task.total = len(ids)
    task.step = "reading the messages from the channel"

    messages: dict[int, object] = {}
    for start in range(0, len(ids), MESSAGE_BATCH):
        chunk = ids[start : start + MESSAGE_BATCH]
        messages.update(await _fetch_messages(client, peer, chunk))
        task.processed = len(messages)

    task.step = "comparing with the index"
    missing = 0
    wrong_size = 0
    broken_files: dict[int, str] = {}

    for message_id, size, part_index, file_id in parts:
        message = messages.get(message_id)
        document = getattr(message, "document", None) if message is not None else None
        if message is None or document is None:
            missing += 1
            broken_files.setdefault(
                file_id, f"part {part_index + 1} is no longer in the channel"
            )
        elif document.size != size:
            wrong_size += 1
            broken_files.setdefault(
                file_id,
                f"part {part_index + 1} is {document.size} bytes instead of {size}",
            )

    marked = 0
    if repair and broken_files:
        task.step = "marking the damaged files"
        async with SessionLocal() as session:
            for file_id, reason in broken_files.items():
                result = await session.execute(
                    update(FileEntry)
                    .where(FileEntry.id == file_id, FileEntry.state == "uploaded")
                    # stale and not error: the runner deletes whatever parts are still
                    # recorded, ignores the ones already gone, and uploads the file again.
                    # Only a file the source still has, though: one in the trash cannot be
                    # uploaded again by anybody, and marking it would only turn a damaged
                    # copy into a job that fails at every run.
                    .values(state="stale", error=f"Check: {reason}")
                )
                marked += result.rowcount or 0
            await session.commit()

    sample = []
    if broken_files:
        async with SessionLocal() as session:
            paths = await session.execute(
                select(FileEntry.rel_path, FileEntry.id).where(
                    FileEntry.id.in_(list(broken_files)[:20])
                )
            )
            sample = [
                f"{rel_path}: {broken_files[file_id]}" for rel_path, file_id in paths
            ]

    task.result = {
        "files_checked": len({part[3] for part in parts}),
        "parts_checked": len(parts),
        "parts_missing": missing,
        "parts_wrong_size": wrong_size,
        "files_broken": len(broken_files),
        "files_marked": marked,
        "sample": sample,
    }
    log.info(
        "Check of channel %d: %d parts, %d broken files, %d marked",
        channel_id, len(parts), len(broken_files), marked,
    )


async def scheduled_check(task: Task, channel_id: int, repair: bool) -> None:
    """The automatic check: the same work, plus a record of it and a report.

    What distinguishes a backup from a folder of files is that somebody looks at it
    without being asked. The outcome is written on the channel row, because the registry
    keeps twenty tasks in memory and loses them at every restart, while a check that runs
    once a month has to still be readable the month after. The report goes out as a
    failure only when something is actually broken, so the `errors` notification mode
    stays quiet on a healthy channel and `all` gets the monthly confirmation.
    """
    outcome = "completed"
    error: str | None = None
    try:
        await check_channel(task, channel_id, repair)
    except Exception as exc:
        outcome = "failed"
        error = str(exc)[:400]
        raise
    finally:
        broken = int(task.result.get("files_broken", 0) or 0)
        async with SessionLocal() as session:
            channel = await session.get(Channel, channel_id)
            if channel is not None:
                channel.last_check_at = utcnow()
                channel.last_check_result = json.dumps(
                    {**task.result, "outcome": outcome, "error": error}
                )[:4000]
                account_id = channel.account_id
                title = channel.title
                await session.commit()
            else:
                account_id = None

        if account_id is not None:
            lines = [
                f"Channel: {title}",
                f"Files checked: {task.result.get('files_checked', 0)}, "
                f"parts: {task.result.get('parts_checked', 0)}",
            ]
            if error:
                lines.append(f"Error: {error}")
            elif broken:
                lines.append(
                    f"Damaged files: {broken}, of which "
                    f"{task.result.get('files_marked', 0)} marked for re-upload"
                )
                lines.extend(task.result.get("sample", [])[:5])
            else:
                lines.append("Every recorded message is in the channel with the right size")

            await notify.send_report(
                account_id=account_id,
                title=f"channel check {title}",
                outcome=outcome,
                lines=lines,
                failed=bool(error) or broken > 0,
            )


# Rebuild


@dataclass(slots=True)
class _Group:
    name: str
    total: int
    parts: dict[int, tuple[int, int]] = field(default_factory=dict)  # index -> (id, size)
    last_date: datetime | None = None


def parse_caption(caption: str | None, file_name: str | None) -> tuple[str, str, int, int]:
    """Reads name, folder and part number out of a message.

    Returns (name, folder, part index, part count). The caption is what the uploader
    writes; when there is none the filename attribute of the document is all there is, and
    the message is treated as a whole file rather than a part, because a name alone cannot
    say how many parts there were.
    """
    if caption:
        if len(caption) >= CAPTION_LIMIT:
            raise ValueError("caption truncated by Telegram, the path cannot be trusted")
        name_match = FILENAME_RE.search(caption)
        if name_match:
            name = name_match.group(1).strip()
            path_match = PATH_RE.search(caption)
            folder = path_match.group(1).strip() if path_match else ""
            part_match = PART_RE.search(caption)
            if part_match:
                index = int(part_match.group(1)) - 1
                total = int(part_match.group(2))
                if index < 0 or total < 1 or index >= total:
                    raise ValueError(f"part number out of range: {part_match.group(0)}")
                return name, folder, index, total
            return name, folder, 0, 1

    if file_name:
        return file_name, "", 0, 1
    raise ValueError("no caption and no file name")


async def rebuild_index(
    task: Task, channel_id: int, mode: str, job_id: int | None, job_name: str
) -> None:
    async with SessionLocal() as session:
        channel = await _channel(session, channel_id)
        peer = manager.input_peer(channel)
        account_id = channel.account_id

    client = await manager.get_client(account_id)
    task.step = "reading the channel"
    probe = await client.get_messages(peer, limit=1)
    task.total = getattr(probe, "total", 0) or 0

    groups: dict[str, _Group] = {}
    seen = 0
    no_document = 0
    unreadable = 0

    async for message in client.iter_messages(peer):
        seen += 1
        task.processed = seen
        document = getattr(message, "document", None)
        if document is None:
            no_document += 1
            continue

        file_name = None
        for attribute in document.attributes:
            if getattr(attribute, "file_name", None):
                file_name = attribute.file_name
                break

        try:
            name, folder, index, total = parse_caption(message.message, file_name)
        except ValueError as exc:
            unreadable += 1
            log.debug("Message %d skipped: %s", message.id, exc)
            continue

        rel_path = f"{folder.strip('/')}/{name}" if folder.strip("/") else name
        group = groups.get(rel_path)
        if group is None:
            group = _Group(name=name, total=total)
            groups[rel_path] = group
        # The highest count wins: a file uploaded twice with a different split would
        # otherwise be assembled out of a mix of the two.
        group.total = max(group.total, total)
        group.parts[index] = (message.id, document.size)
        if group.last_date is None or (message.date and message.date > group.last_date):
            group.last_date = message.date

    task.step = "putting the index back together"
    rows: list[dict] = []
    parts_by_path: dict[str, list[dict]] = {}
    incomplete = 0

    for rel_path, group in groups.items():
        if len(group.parts) != group.total or sorted(group.parts) != list(range(group.total)):
            # A file whose parts are not all there cannot be rebuilt: indexing it would
            # promise a restore that would fail.
            incomplete += 1
            continue

        offset = 0
        parts = []
        for index in range(group.total):
            message_id, size = group.parts[index]
            parts.append({"i": index, "o": offset, "s": size, "m": message_id})
            offset += size

        rows.append(
            {
                "job_id": 0,  # filled in once the target job is known
                "rel_path": rel_path,
                "name": group.name,
                "size": offset,
                # The channel does not carry the date. The first scan of the job adopts
                # the one of the source instead of re-uploading everything.
                "mtime_ns": MTIME_UNKNOWN,
                "state": "uploaded",
                "parts_total": group.total,
                "error": None,
                "first_seen_at": utcnow(),
                "uploaded_at": group.last_date,
            }
        )
        parts_by_path[rel_path] = parts

    task.step = "writing the index"
    async with SessionLocal() as session:
        job = None
        if mode == "merge":
            if job_id is None:
                raise MaintenanceError("Pick the job to merge into")
            job = await session.get(SyncJob, job_id)
            if job is None or job.channel_id != channel_id:
                raise MaintenanceError("The job does not write to this channel")
            action = "merged"
        else:
            channel = await _channel(session, channel_id)
            job = SyncJob(
                name=job_name or f"{channel.title} rebuilt",
                account_id=channel.account_id,
                channel_id=channel_id,
                source_type="local",
                local_path="",
                # Never enabled by a rebuild: the job has no source yet, and a run against
                # the wrong folder would see every file as removed and empty the channel.
                enabled=False,
            )
            session.add(job)
            await session.flush()
            action = "created"

        # What the index already knows is looked up across the whole channel, not only in
        # the target job: a path indexed by another job writing here is already accounted
        # for, and writing it again would give the same messages two entries.
        known = await session.execute(
            select(FileEntry.rel_path).where(
                FileEntry.job_id.in_(
                    select(SyncJob.id).where(SyncJob.channel_id == channel_id)
                )
            )
        )
        existing = {rel_path for (rel_path,) in known}
        kept = [dict(row, job_id=job.id) for row in rows if row["rel_path"] not in existing]
        skipped = len(rows) - len(kept)
        parts_kept = {
            path: parts for path, parts in parts_by_path.items() if path not in existing
        }

        parts_written = await insert_files(session, job.id, kept, parts_kept)
        await session.commit()
        job_label = job.name

    task.result = {
        "messages_read": seen,
        "messages_without_document": no_document,
        "messages_unreadable": unreadable,
        "files_found": len(groups),
        "files_incomplete": incomplete,
        "files_written": len(kept),
        "files_skipped": skipped,
        "parts_written": parts_written,
        "job_name": job_label,
        "job_action": action,
    }
    log.info(
        "Rebuild of channel %d: %d messages, %d files written into job %s",
        channel_id, seen, len(kept), job_label,
    )
