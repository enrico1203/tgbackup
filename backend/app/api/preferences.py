from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from .. import notify
from ..deps import ActiveUserDep, SessionDep
from ..models import Setting, TelegramAccount, utcnow
from ..schemas import (
    BandwidthPreferencesIn,
    BandwidthPreferencesOut,
    NotifyPreferencesIn,
    NotifyPreferencesOut,
    SchedulePreferencesIn,
    SchedulePreferencesOut,
)
from ..sync import window
from ..telegram import throttle

router = APIRouter(prefix="/api/preferences", tags=["preferences"])

# The ceiling for the whole installation, in bytes per second. A row in `settings` like
# the notification preferences, so it needed no migration.
RATE_LIMIT_KEY = "rate_limit_bps"


async def load_rate_limit(session) -> int:
    """Reads the stored limit and puts it where the transfers will find it."""
    row = await session.scalar(select(Setting).where(Setting.key == RATE_LIMIT_KEY))
    try:
        value = int(row.value) if row is not None else 0
    except ValueError:
        value = 0
    throttle.set_global_rate(value)
    return value


@router.get("/notifications", response_model=NotifyPreferencesOut)
async def read_notifications(session: SessionDep, _: ActiveUserDep) -> NotifyPreferencesOut:
    events, account_id = await notify.load_preferences(session)
    return NotifyPreferencesOut(
        events=events,
        account_id=account_id,
        silence_days=await notify.load_silence_days(session),
    )


@router.put("/notifications", response_model=NotifyPreferencesOut)
async def write_notifications(
    payload: NotifyPreferencesIn, session: SessionDep, _: ActiveUserDep
) -> NotifyPreferencesOut:
    if payload.account_id:
        account = await session.get(TelegramAccount, payload.account_id)
        if account is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Telegram account not found")

    await notify.save_preferences(
        session, payload.events, payload.account_id, payload.silence_days
    )
    return NotifyPreferencesOut(
        events=payload.events,
        account_id=payload.account_id,
        silence_days=payload.silence_days,
    )


@router.get("/bandwidth", response_model=BandwidthPreferencesOut)
async def read_bandwidth(session: SessionDep, _: ActiveUserDep) -> BandwidthPreferencesOut:
    return BandwidthPreferencesOut(rate_limit_bps=await load_rate_limit(session))


@router.put("/bandwidth", response_model=BandwidthPreferencesOut)
async def write_bandwidth(
    payload: BandwidthPreferencesIn, session: SessionDep, _: ActiveUserDep
) -> BandwidthPreferencesOut:
    """Saves the limit and applies it right away, to the transfers already in flight.

    The limiter reads its rate as it refills, so a run that started at full speed is
    slowed down by this without being stopped and started again.
    """
    value = str(payload.rate_limit_bps)
    row = await session.scalar(select(Setting).where(Setting.key == RATE_LIMIT_KEY))
    if row is None:
        session.add(Setting(key=RATE_LIMIT_KEY, value=value))
    else:
        row.value = value
        row.updated_at = utcnow()
    await session.commit()

    throttle.set_global_rate(payload.rate_limit_bps)
    return BandwidthPreferencesOut(rate_limit_bps=payload.rate_limit_bps)


@router.get("/schedule", response_model=SchedulePreferencesOut)
async def read_schedule(session: SessionDep, _: ActiveUserDep) -> SchedulePreferencesOut:
    return SchedulePreferencesOut(timezone=await window.load_timezone(session))


@router.put("/schedule", response_model=SchedulePreferencesOut)
async def write_schedule(
    payload: SchedulePreferencesIn, session: SessionDep, _: ActiveUserDep
) -> SchedulePreferencesOut:
    try:
        await window.save_timezone(session, payload.timezone)
    except Exception as exc:
        # An unknown zone would send every window back to UTC without a word, which on a
        # night-only schedule means uploading in the middle of the afternoon.
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, f"Unknown timezone: {payload.timezone}"
        ) from exc
    return SchedulePreferencesOut(timezone=payload.timezone)
