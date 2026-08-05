"""Reports on how a run ended, sent to Telegram.

A job that fails is discovered by opening the interface, which means it is discovered late
or never. The Telegram client is already connected and the account always has a chat with
itself, so the report goes to Saved Messages: no webhook to configure, no second service
to keep alive, and it arrives where the backup already lives.

The preferences live in the `settings` table rather than in a column of their own: they
are two values for the whole installation and needed no migration.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy import select
from telethon.tl.types import InputPeerSelf

from .db import SessionLocal
from .models import Setting, utcnow
from .telegram.manager import manager

log = logging.getLogger(__name__)

EVENTS_KEY = "notify_events"
ACCOUNT_KEY = "notify_account"
# After how many days without a successful run a job is reported as silent. 0 turns the
# alarm off. See sync/scheduler.py, _check_silence.
SILENCE_KEY = "notify_silence_days"
DEFAULT_SILENCE_DAYS = 3

# off: nothing. errors: only runs that failed or left files behind. all: every run.
EVENTS = ("off", "errors", "all")


async def load_preferences(session) -> tuple[str, int]:
    """Returns (events, account id). Account 0 means the account of the job that ran."""
    rows = await session.execute(
        select(Setting).where(Setting.key.in_((EVENTS_KEY, ACCOUNT_KEY)))
    )
    values = {row.key: row.value for row in rows.scalars()}
    events = values.get(EVENTS_KEY, "off")
    if events not in EVENTS:
        events = "off"
    try:
        account_id = int(values.get(ACCOUNT_KEY, "0"))
    except ValueError:
        account_id = 0
    return events, account_id


async def load_silence_days(session) -> int:
    """Days without a successful run after which a job is reported. 0 is off."""
    row = await session.scalar(select(Setting).where(Setting.key == SILENCE_KEY))
    if row is None:
        return DEFAULT_SILENCE_DAYS
    try:
        return max(0, int(row.value))
    except ValueError:
        return DEFAULT_SILENCE_DAYS


async def save_preferences(session, events: str, account_id: int, silence_days: int) -> None:
    if events not in EVENTS:
        raise ValueError(f"Unknown notification mode: {events}")
    for key, value in (
        (EVENTS_KEY, events),
        (ACCOUNT_KEY, str(account_id)),
        (SILENCE_KEY, str(max(0, silence_days))),
    ):
        row = await session.scalar(select(Setting).where(Setting.key == key))
        if row is None:
            session.add(Setting(key=key, value=value))
        else:
            row.value = value
            row.updated_at = utcnow()
    await session.commit()


def format_bytes(value: int) -> str:
    size = float(max(0, value))
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def format_duration(start: datetime | None, end: datetime | None) -> str:
    if start is None or end is None:
        return "unknown"
    if start.tzinfo is None:
        start = start.replace(tzinfo=UTC)
    if end.tzinfo is None:
        end = end.replace(tzinfo=UTC)
    seconds = int((end - start).total_seconds())
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m {seconds % 60}s"
    if seconds < 86400:
        return f"{seconds // 3600}h {(seconds % 3600) // 60}m"
    return f"{seconds // 86400}d {(seconds % 86400) // 3600}h"


async def send_alert(account_id: int, title: str, lines: list[str]) -> None:
    """A report about something that did not happen.

    It goes out as a failure, so the `errors` mode delivers it: the whole point of the
    silence alarm is that it reaches somebody who only wants to hear about problems.
    """
    await send_report(
        account_id=account_id, title=title, outcome="no news", lines=lines, failed=True
    )


async def send_report(
    account_id: int, title: str, outcome: str, lines: list[str], failed: bool
) -> None:
    """Sends a report, unless the preferences say otherwise.

    A notification must never be able to break a job: everything here is inside a try, and
    a failure to send is a line in the log and nothing more.
    """
    try:
        async with SessionLocal() as session:
            events, configured_account = await load_preferences(session)

        if events == "off" or (events == "errors" and not failed):
            return

        target_account = configured_account or account_id
        client = await manager.get_client(target_account)
        body = "\n".join([f"tgbackup: {title}", f"Outcome: {outcome}", *lines])
        # To itself: the Saved Messages chat exists for every account and needs no setup.
        await client.send_message(InputPeerSelf(), body[:4000])
    except Exception as exc:
        log.warning("Sending the notification for %s failed: %s", title, exc)
