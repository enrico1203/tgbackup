from fastapi import APIRouter, HTTPException, status

from .. import notify
from ..deps import ActiveUserDep, SessionDep
from ..models import TelegramAccount
from ..schemas import (
    NotifyPreferencesIn,
    NotifyPreferencesOut,
    SchedulePreferencesIn,
    SchedulePreferencesOut,
)
from ..sync import window

router = APIRouter(prefix="/api/preferences", tags=["preferences"])


@router.get("/notifications", response_model=NotifyPreferencesOut)
async def read_notifications(session: SessionDep, _: ActiveUserDep) -> NotifyPreferencesOut:
    events, account_id = await notify.load_preferences(session)
    return NotifyPreferencesOut(events=events, account_id=account_id)


@router.put("/notifications", response_model=NotifyPreferencesOut)
async def write_notifications(
    payload: NotifyPreferencesIn, session: SessionDep, _: ActiveUserDep
) -> NotifyPreferencesOut:
    if payload.account_id:
        account = await session.get(TelegramAccount, payload.account_id)
        if account is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Telegram account not found")

    await notify.save_preferences(session, payload.events, payload.account_id)
    return NotifyPreferencesOut(events=payload.events, account_id=payload.account_id)


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
