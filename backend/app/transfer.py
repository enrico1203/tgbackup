"""Export and import of the index of a single channel.

What is exported is the database side of a channel, never the bytes: the channel
coordinates, the jobs writing to it, and for every file its identity triple plus the
message ids of its parts. That is exactly what is needed to restore a file, so an export
carried to another machine gives that machine the ability to rebuild everything the
channel holds, while the content stays on Telegram.

Two things do not travel and cannot. The Telegram session, because it is bound to
`APP_SECRET` and to the machine that created it: the target instance signs the account in
on its own. And `access_hash`, which is issued per user: the exported one only works if
the import lands on the same Telegram account, so on import the value is taken from the
target account's own dialog list whenever it is reachable, and the exported one is only a
fallback.

Imported jobs always arrive disabled. A job whose source folder is missing on the new
machine fails cleanly, but one pointing at a folder that exists and is empty would see
every known file as removed and would delete the whole channel on its first run. Enabling
is left to the user, after the source has been pointed at the right place.
"""

from __future__ import annotations

import gzip
import io
import json
import logging
import re
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Channel, FileEntry, FilePart, SyncJob, TelegramAccount, utcnow
from .telegram.manager import TelegramError, manager

log = logging.getLogger(__name__)

FORMAT = "tgbackup.channel"
VERSION = 1

# Rows per INSERT. A channel with tens of thousands of files is inserted in one
# transaction anyway, this only keeps a single statement from carrying it all.
CHUNK = 500

# The upload is authenticated, so these are guards against a corrupt or wrong file, not
# against an attacker. A real export of 30,000 files is a few megabytes compressed.
MAX_UPLOAD_BYTES = 64 * 1024 * 1024
MAX_DECOMPRESSED_BYTES = 512 * 1024 * 1024

GZIP_MAGIC = b"\x1f\x8b"


class TransferError(Exception):
    """Error meant to be shown directly to the user."""


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    # Rows written before timezone awareness, and other instances, can hand back a naive
    # value: it is read as UTC, which is what everything in this application writes.
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


# Export


async def channel_summaries(session: AsyncSession) -> list[dict]:
    """Channels that are the destination of at least one job, with what an export holds."""
    rows = await session.execute(
        select(
            Channel.id,
            Channel.tg_id,
            Channel.title,
            TelegramAccount.id,
            TelegramAccount.label,
            func.count(func.distinct(SyncJob.id)),
            func.count(FileEntry.id),
            func.coalesce(func.sum(FileEntry.size), 0),
        )
        .join(SyncJob, SyncJob.channel_id == Channel.id)
        .join(TelegramAccount, TelegramAccount.id == Channel.account_id)
        .outerjoin(FileEntry, FileEntry.job_id == SyncJob.id)
        .group_by(Channel.id)
        .order_by(Channel.title)
    )

    # Counted apart: joining the parts too would multiply the file rows and every other
    # total with them.
    part_rows = await session.execute(
        select(SyncJob.channel_id, func.count(FilePart.id))
        .join(FileEntry, FileEntry.job_id == SyncJob.id)
        .join(FilePart, FilePart.file_id == FileEntry.id)
        .group_by(SyncJob.channel_id)
    )
    parts_by_channel = dict(part_rows.all())

    return [
        {
            "channel_id": channel_id,
            "tg_id": tg_id,
            "title": title,
            "account_id": account_id,
            "account_label": account_label,
            "jobs": jobs,
            "files": files,
            "parts": parts_by_channel.get(channel_id, 0),
            "bytes_total": bytes_total or 0,
        }
        for channel_id, tg_id, title, account_id, account_label, jobs, files, bytes_total in rows
    ]


async def build_export(session: AsyncSession, channel_id: int) -> dict:
    channel = await session.get(Channel, channel_id)
    if channel is None:
        raise TransferError("Channel not found")
    account = await session.get(TelegramAccount, channel.account_id)

    jobs_result = await session.execute(
        select(SyncJob).where(SyncJob.channel_id == channel_id).order_by(SyncJob.id)
    )

    jobs: list[dict] = []
    total_files = 0
    total_parts = 0
    total_bytes = 0

    for job in jobs_result.scalars():
        parts_result = await session.execute(
            select(
                FilePart.file_id,
                FilePart.part_index,
                FilePart.offset,
                FilePart.size,
                FilePart.message_id,
            )
            .join(FileEntry, FileEntry.id == FilePart.file_id)
            .where(FileEntry.job_id == job.id)
            .order_by(FilePart.file_id, FilePart.part_index)
        )
        parts_by_file: dict[int, list[dict]] = {}
        for file_id, part_index, offset, size, message_id in parts_result:
            parts_by_file.setdefault(file_id, []).append(
                {"i": part_index, "o": offset, "s": size, "m": message_id}
            )

        files_result = await session.execute(
            select(FileEntry).where(FileEntry.job_id == job.id).order_by(FileEntry.rel_path)
        )
        files: list[dict] = []
        for entry in files_result.scalars():
            parts = parts_by_file.get(entry.id, [])
            files.append(
                {
                    "rel_path": entry.rel_path,
                    "name": entry.name,
                    "size": entry.size,
                    "mtime_ns": entry.mtime_ns,
                    "state": entry.state,
                    "parts_total": entry.parts_total,
                    # Truncated: it travels only so a file in error does not arrive with
                    # the state and no reason for it.
                    "error": entry.error[:300] if entry.error else None,
                    "first_seen_at": _iso(entry.first_seen_at),
                    "uploaded_at": _iso(entry.uploaded_at),
                    "parts": parts,
                }
            )
            total_parts += len(parts)
            total_bytes += entry.size

        total_files += len(files)
        jobs.append(
            {
                "name": job.name,
                "source_type": job.source_type,
                "local_path": job.local_path,
                "remote": job.remote,
                "interval_hours": job.interval_hours,
                "scan_files_per_sec": job.scan_files_per_sec,
                "part_size_bytes": job.part_size_bytes,
                "files": files,
            }
        )

    return {
        "format": FORMAT,
        "version": VERSION,
        "exported_at": _iso(utcnow()),
        # Which Telegram account owned the channel. Read on import to tell whether the
        # exported access_hash can be trusted at all.
        "account": {
            "label": account.label if account else "",
            "tg_user_id": account.tg_user_id if account else None,
            "username": account.username if account else None,
        },
        "channel": {
            "tg_id": channel.tg_id,
            "access_hash": channel.access_hash,
            "title": channel.title,
            "username": channel.username,
            "is_private": channel.is_private,
            "kind": channel.kind,
            "participants": channel.participants,
        },
        "totals": {
            "jobs": len(jobs),
            "files": total_files,
            "parts": total_parts,
            "bytes": total_bytes,
        },
        "jobs": jobs,
    }


def encode(payload: dict) -> bytes:
    """Serialises and compresses. Runs in a thread: on a large channel it is not quick."""
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
    return gzip.compress(raw, compresslevel=6)


def export_filename(title: str, tg_id: int) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:48] or "channel"
    day = datetime.now(UTC).strftime("%Y%m%d")
    return f"tgbackup-{slug}-{tg_id}-{day}.json.gz"


# Import


def decode(raw: bytes) -> dict:
    """Reads an uploaded export, gzipped or plain. Runs in a thread, like encode."""
    if not raw:
        raise TransferError("The file is empty")
    if len(raw) > MAX_UPLOAD_BYTES:
        raise TransferError(
            f"The file is larger than {MAX_UPLOAD_BYTES // (1024 * 1024)} MB, "
            "it does not look like an export"
        )

    data = raw
    if raw[:2] == GZIP_MAGIC:
        try:
            with gzip.GzipFile(fileobj=io.BytesIO(raw)) as stream:
                data = stream.read(MAX_DECOMPRESSED_BYTES + 1)
        except OSError as exc:
            raise TransferError(f"The file is not readable as gzip: {exc}") from exc
        if len(data) > MAX_DECOMPRESSED_BYTES:
            raise TransferError("The file expands beyond 512 MB, it does not look like an export")

    try:
        payload = json.loads(data)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise TransferError(f"The file does not hold valid JSON: {exc}") from exc

    if not isinstance(payload, dict) or payload.get("format") != FORMAT:
        raise TransferError("This is not a tgbackup channel export")
    if payload.get("version") != VERSION:
        raise TransferError(
            f"Export in version {payload.get('version')}, this instance reads version {VERSION}"
        )
    if not isinstance(payload.get("channel"), dict) or not isinstance(payload.get("jobs"), list):
        raise TransferError("The export is incomplete: channel or jobs missing")
    return payload


def describe(payload: dict) -> dict:
    """What the file holds, without writing anything."""
    channel = payload["channel"]
    jobs = []
    for job in payload["jobs"]:
        files = job.get("files") or []
        jobs.append(
            {
                "name": job.get("name", ""),
                "source_type": job.get("source_type", "local"),
                "source": job.get("remote") or job.get("local_path") or "",
                "files": len(files),
                "parts": sum(len(f.get("parts") or []) for f in files),
                "bytes_total": sum(int(f.get("size") or 0) for f in files),
            }
        )

    return {
        "exported_at": payload.get("exported_at"),
        "account_label": (payload.get("account") or {}).get("label") or "",
        "account_tg_user_id": (payload.get("account") or {}).get("tg_user_id"),
        "channel_tg_id": channel.get("tg_id", 0),
        "channel_title": channel.get("title", ""),
        "channel_username": channel.get("username"),
        "jobs": jobs,
        "files": sum(job["files"] for job in jobs),
        "parts": sum(job["parts"] for job in jobs),
        "bytes_total": sum(job["bytes_total"] for job in jobs),
    }


async def _resolve_channel(
    session: AsyncSession, account: TelegramAccount, exported: dict, warnings: list[str]
) -> tuple[Channel, bool]:
    """Finds or creates the channel row on the target account.

    The exported `access_hash` belongs to the account that produced the file. If the
    target account is connected its own dialog list is asked first, which is the only
    value guaranteed to work here.
    """
    tg_id = int(exported["tg_id"])
    live: dict | None = None
    try:
        for item in await manager.list_channels(account.id):
            if item["tg_id"] == tg_id:
                live = item
                break
        if live is None:
            warnings.append(
                f"The account {account.label} does not see the channel among its own: "
                "join it with this account, otherwise restores will fail"
            )
    except (TelegramError, OSError, ValueError) as exc:
        # An account that is not connected, or a network that is not answering, must not
        # stop the import: the index is still worth having, the channel is only unverified.
        warnings.append(f"Channel not verified against Telegram: {exc}")

    channel = await session.scalar(
        select(Channel).where(Channel.account_id == account.id, Channel.tg_id == tg_id)
    )
    created = channel is None

    if channel is not None and live is None:
        # The row is already here and Telegram could not confirm anything: what it holds
        # was written by this account, which knows better than a file from another one.
        # Overwriting its access_hash with the exported one would break restores on a
        # channel that was working.
        warnings.append(
            f"Channel {channel.title} was already on this instance: its coordinates were "
            "left as they were and only the index was added"
        )
        await session.flush()
        return channel, created

    if channel is None:
        channel = Channel(account_id=account.id, tg_id=tg_id)
        session.add(channel)
        if live is None:
            warnings.append(
                "The channel was added with the coordinates from the file, which only work "
                "if this is the same Telegram account. If a restore fails, refresh the "
                "channel list of the account from the Telegram accounts page"
            )

    source = live or exported
    channel.access_hash = source.get("access_hash")
    channel.title = source.get("title") or exported.get("title") or f"Channel {tg_id}"
    channel.username = source.get("username")
    channel.is_private = bool(source.get("is_private", True))
    channel.kind = source.get("kind") or "channel"
    channel.participants = source.get("participants")
    if live is not None:
        channel.last_seen_at = utcnow()
    await session.flush()
    return channel, created


def _file_rows(job_id: int, files: list[dict]) -> tuple[list[dict], dict[str, list[dict]]]:
    rows: list[dict] = []
    parts_by_path: dict[str, list[dict]] = {}
    for item in files:
        rel_path = item.get("rel_path")
        if not rel_path:
            continue
        parts = sorted(item.get("parts") or [], key=lambda part: part["i"])
        state = item.get("state") or "pending"
        if state == "uploading":
            # Nothing is in flight on this machine. With parts already in the channel the
            # file has to go through stale, so those messages are deleted before it is
            # sent again, exactly as the runner does after a crash.
            state = "stale" if parts else "pending"

        rows.append(
            {
                "job_id": job_id,
                "rel_path": rel_path,
                "name": item.get("name") or rel_path.rsplit("/", 1)[-1],
                "size": int(item.get("size") or 0),
                "mtime_ns": int(item.get("mtime_ns") or 0),
                "state": state,
                "parts_total": len(parts) or int(item.get("parts_total") or 1),
                "error": item.get("error") if state == "error" else None,
                "first_seen_at": _parse_dt(item.get("first_seen_at")) or utcnow(),
                "uploaded_at": _parse_dt(item.get("uploaded_at")),
            }
        )
        if parts:
            parts_by_path[rel_path] = parts
    return rows, parts_by_path


async def insert_files(
    session: AsyncSession, job_id: int, rows: list[dict], parts_by_path: dict[str, list[dict]]
) -> int:
    for start in range(0, len(rows), CHUNK):
        await session.execute(insert(FileEntry), rows[start : start + CHUNK])
    if not parts_by_path:
        return 0

    # The ids are read back instead of being asked for at insert time: SQLite returns
    # them one row at a time, and one round trip per file would undo the bulk insert.
    result = await session.execute(
        select(FileEntry.id, FileEntry.rel_path).where(FileEntry.job_id == job_id)
    )
    ids = {rel_path: file_id for file_id, rel_path in result}

    part_rows = [
        {
            "file_id": ids[rel_path],
            "part_index": part["i"],
            "offset": part["o"],
            "size": part["s"],
            "message_id": part["m"],
            "uploaded_at": utcnow(),
        }
        for rel_path, parts in parts_by_path.items()
        if rel_path in ids
        for part in parts
    ]
    for start in range(0, len(part_rows), CHUNK):
        await session.execute(insert(FilePart), part_rows[start : start + CHUNK])
    return len(part_rows)


async def import_payload(
    session: AsyncSession, payload: dict, account_id: int, merge: bool
) -> dict:
    account = await session.get(TelegramAccount, account_id)
    if account is None:
        raise TransferError("Telegram account not found")

    warnings: list[str] = []
    exported_account = payload.get("account") or {}
    exported_user = exported_account.get("tg_user_id")
    if exported_user and account.tg_user_id and exported_user != account.tg_user_id:
        warnings.append(
            f"The export comes from another Telegram account ({exported_account.get('label')}). "
            "It only works if this account is a member of the channel"
        )

    channel, channel_created = await _resolve_channel(
        session, account, payload["channel"], warnings
    )

    results = []
    for job_payload in payload["jobs"]:
        name = job_payload.get("name") or "Imported job"
        files = job_payload.get("files") or []

        job = None
        if merge:
            job = await session.scalar(
                select(SyncJob).where(SyncJob.channel_id == channel.id, SyncJob.name == name)
            )

        if job is None:
            # The name is kept as exported, even if this instance already has one like it.
            # It is what merge matches on: renaming here would make a second import of the
            # same file miss the job it created the first time and duplicate everything.
            job = SyncJob(
                name=name,
                account_id=account.id,
                channel_id=channel.id,
                source_type=job_payload.get("source_type") or "local",
                local_path=job_payload.get("local_path") or "",
                remote=job_payload.get("remote"),
                interval_hours=float(job_payload.get("interval_hours") or 24.0),
                scan_files_per_sec=int(job_payload.get("scan_files_per_sec") or 0),
                part_size_bytes=int(
                    job_payload.get("part_size_bytes") or account.default_part_size
                ),
                # Never enabled by importing: the source of this job belongs to the machine
                # that produced the export, and a run against the wrong folder would delete
                # the channel one message at a time.
                enabled=False,
            )
            session.add(job)
            await session.flush()
            action = "created"
        else:
            action = "merged"

        rows, parts_by_path = _file_rows(job.id, files)
        skipped = 0
        if action == "merged":
            known = await session.execute(
                select(FileEntry.rel_path).where(FileEntry.job_id == job.id)
            )
            existing = {rel_path for (rel_path,) in known}
            if existing:
                kept = [row for row in rows if row["rel_path"] not in existing]
                skipped = len(rows) - len(kept)
                rows = kept
                parts_by_path = {
                    path: parts for path, parts in parts_by_path.items() if path not in existing
                }

        parts_imported = await insert_files(session, job.id, rows, parts_by_path)
        results.append(
            {
                "name": job.name,
                "action": action,
                "files_imported": len(rows),
                "files_skipped": skipped,
                "parts_imported": parts_imported,
            }
        )

    await session.commit()
    log.info(
        "Imported channel %s into account %d: %d jobs",
        channel.title,
        account.id,
        len(results),
    )

    return {
        "channel_id": channel.id,
        "channel_title": channel.title,
        "channel_created": channel_created,
        "jobs": results,
        "files_imported": sum(job["files_imported"] for job in results),
        "files_skipped": sum(job["files_skipped"] for job in results),
        "parts_imported": sum(job["parts_imported"] for job in results),
        "warnings": warnings,
    }
