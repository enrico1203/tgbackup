"""Weekly schedule windows: the hours of the week a job is allowed to run in.

The window is a gate, not a replacement for the interval. A job becomes due the way it
always did, from `next_run_at`, and the window only decides whether it may start at that
moment: if it falls outside, the job waits for the opening instead of skipping a whole
interval. Two jobs sharing a window do not queue behind each other either, the account
semaphore is still what serializes the transfers.

The window is 168 characters, one per hour, Monday 00:00 first and Sunday 23:00 last,
"1" open and "0" closed. A string rather than a table of rows because it is always read
whole, never queried, and 168 bytes on a job row cost nothing.

The hours are local hours: "not between 8 and 18" means nothing in UTC to somebody who
lives at UTC+2. The timezone is one value for the installation, kept in `settings` like
the notification preferences, and every conversion goes through it. Arithmetic stays in
UTC and only the reading of the slot converts, so a DST change shifts the window by one
hour for one day, which is what a wall clock does too, and can never produce a loop that
skips or repeats a run.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select

from ..models import SCHEDULE_ALWAYS, Setting, utcnow

log = logging.getLogger(__name__)

HOURS = 168
ALWAYS = SCHEDULE_ALWAYS
TIMEZONE_KEY = "schedule_timezone"
DEFAULT_TIMEZONE = "UTC"


def normalise(spec: str | None) -> str:
    """Brings any stored value back to 168 characters of 0 and 1.

    Anything unreadable becomes an open window: a schedule that cannot be parsed must
    never be the reason a backup stops running.
    """
    if not spec:
        return ALWAYS
    cleaned = "".join("1" if char == "1" else "0" for char in spec.strip())
    if len(cleaned) != HOURS:
        return ALWAYS
    return cleaned


def is_always_open(spec: str | None) -> bool:
    return normalise(spec) == ALWAYS


def load_zone(name: str | None) -> ZoneInfo:
    try:
        return ZoneInfo(name or DEFAULT_TIMEZONE)
    except (ZoneInfoNotFoundError, ValueError):
        log.warning("Unknown timezone %r, falling back to UTC", name)
        return ZoneInfo(DEFAULT_TIMEZONE)


def _slot(moment: datetime, zone: ZoneInfo) -> int:
    local = moment.astimezone(zone)
    return local.weekday() * 24 + local.hour


def is_open(spec: str | None, zone: ZoneInfo, moment: datetime) -> bool:
    return normalise(spec)[_slot(moment, zone)] == "1"


def next_opening(spec: str | None, zone: ZoneInfo, after: datetime) -> datetime | None:
    """When the window is next open, `after` itself if it is open right now.

    None when the window is closed for all 168 hours, which is a job that will never
    run: the interface says so rather than the scheduler pretending to schedule it.
    """
    schedule = normalise(spec)
    if "1" not in schedule:
        return None
    start = after.astimezone(UTC).replace(minute=0, second=0, microsecond=0)
    for step in range(HOURS + 1):
        moment = start + timedelta(hours=step)
        if schedule[_slot(moment, zone)] == "1":
            return after if step == 0 else moment
    return None


def closes_at(spec: str | None, zone: ZoneInfo, moment: datetime) -> datetime | None:
    """The first hour boundary at which the window closes, None if it never does."""
    schedule = normalise(spec)
    if "0" not in schedule:
        return None
    start = moment.astimezone(UTC).replace(minute=0, second=0, microsecond=0)
    for step in range(1, HOURS + 1):
        boundary = start + timedelta(hours=step)
        if schedule[_slot(boundary, zone)] == "0":
            return boundary
    return None


async def load_timezone(session) -> str:
    row = await session.scalar(select(Setting).where(Setting.key == TIMEZONE_KEY))
    return row.value if row is not None and row.value else DEFAULT_TIMEZONE


async def save_timezone(session, name: str) -> None:
    # Raises on an unknown name before anything is written: a timezone the container
    # cannot resolve would silently move every window back to UTC.
    ZoneInfo(name)
    row = await session.scalar(select(Setting).where(Setting.key == TIMEZONE_KEY))
    if row is None:
        session.add(Setting(key=TIMEZONE_KEY, value=name))
    else:
        row.value = name
        row.updated_at = utcnow()
    await session.commit()
