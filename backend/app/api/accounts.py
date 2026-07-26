from fastapi import APIRouter, HTTPException, status
from sqlalchemy import func, select

from ..deps import ActiveUserDep, SessionDep
from ..models import Channel, SyncJob, TelegramAccount, utcnow
from ..schemas import (
    AccountCodeIn,
    AccountOut,
    AccountPasswordIn,
    AccountStartIn,
    AccountStepOut,
    AccountUpdate,
    ChannelOut,
)
from ..security import encrypt
from ..telegram.manager import TelegramError, manager

router = APIRouter(prefix="/api/accounts", tags=["accounts"])


async def _to_out(session, account: TelegramAccount) -> AccountOut:
    count = await session.scalar(
        select(func.count(Channel.id)).where(Channel.account_id == account.id)
    )
    out = AccountOut.model_validate(account)
    out.connected = manager.is_connected(account.id)
    out.channels_count = count or 0
    return out


@router.get("", response_model=list[AccountOut])
async def list_accounts(session: SessionDep, _: ActiveUserDep) -> list[AccountOut]:
    result = await session.execute(select(TelegramAccount).order_by(TelegramAccount.id))
    return [await _to_out(session, account) for account in result.scalars()]


@router.post("/start", response_model=AccountStepOut)
async def start_account(
    payload: AccountStartIn, session: SessionDep, _: ActiveUserDep
) -> AccountStepOut:
    account = TelegramAccount(
        label=payload.label,
        api_id=payload.api_id,
        api_hash_enc=encrypt(payload.api_hash),
        phone=payload.phone,
        status="pending_code",
    )
    session.add(account)
    await session.commit()
    await session.refresh(account)

    try:
        await manager.start_login(account.id, payload.api_id, payload.api_hash, payload.phone)
    except TelegramError as exc:
        await session.delete(account)
        await session.commit()
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    return AccountStepOut(account_id=account.id, status="pending_code", needs="code")


@router.post("/{account_id}/code", response_model=AccountStepOut)
async def submit_code(
    account_id: int, payload: AccountCodeIn, session: SessionDep, _: ActiveUserDep
) -> AccountStepOut:
    account = await session.get(TelegramAccount, account_id)
    if account is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Account not found")

    try:
        outcome = await manager.submit_code(account_id, payload.code)
    except TelegramError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    if outcome == "password":
        account.status = "pending_password"
        await session.commit()
        return AccountStepOut(account_id=account_id, status="pending_password", needs="password")

    await session.refresh(account)
    return AccountStepOut(
        account_id=account_id,
        status="connected",
        needs=None,
        account=await _to_out(session, account),
    )


@router.post("/{account_id}/password", response_model=AccountStepOut)
async def submit_password(
    account_id: int, payload: AccountPasswordIn, session: SessionDep, _: ActiveUserDep
) -> AccountStepOut:
    account = await session.get(TelegramAccount, account_id)
    if account is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Account not found")

    try:
        await manager.submit_password(account_id, payload.password)
    except TelegramError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    await session.refresh(account)
    return AccountStepOut(
        account_id=account_id,
        status="connected",
        needs=None,
        account=await _to_out(session, account),
    )


@router.patch("/{account_id}", response_model=AccountOut)
async def update_account(
    account_id: int, payload: AccountUpdate, session: SessionDep, _: ActiveUserDep
) -> AccountOut:
    account = await session.get(TelegramAccount, account_id)
    if account is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Account not found")

    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(account, field, value)
    await session.commit()
    await session.refresh(account)
    return await _to_out(session, account)


@router.delete("/{account_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_account(account_id: int, session: SessionDep, _: ActiveUserDep) -> None:
    account = await session.get(TelegramAccount, account_id)
    if account is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Account not found")

    jobs = await session.scalar(
        select(func.count(SyncJob.id)).where(SyncJob.account_id == account_id)
    )
    if jobs:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"The account is used by {jobs} sync jobs, delete them before disconnecting it",
        )

    await manager.logout(account_id)
    await session.delete(account)
    await session.commit()


@router.get("/{account_id}/channels", response_model=list[ChannelOut])
async def list_channels(
    account_id: int, session: SessionDep, _: ActiveUserDep, refresh: bool = False
) -> list[Channel]:
    account = await session.get(TelegramAccount, account_id)
    if account is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Account not found")

    known = await session.execute(
        select(Channel).where(Channel.account_id == account_id).order_by(Channel.title)
    )
    cached = list(known.scalars())

    if cached and not refresh:
        return cached

    try:
        fetched = await manager.list_channels(account_id)
    except TelegramError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    by_tg_id = {channel.tg_id: channel for channel in cached}
    for item in fetched:
        channel = by_tg_id.get(item["tg_id"])
        if channel is None:
            channel = Channel(account_id=account_id, tg_id=item["tg_id"])
            session.add(channel)
        channel.access_hash = item["access_hash"]
        channel.title = item["title"]
        channel.username = item["username"]
        channel.is_private = item["is_private"]
        channel.kind = item["kind"]
        channel.participants = item["participants"]
        channel.last_seen_at = utcnow()

    await session.commit()

    result = await session.execute(
        select(Channel).where(Channel.account_id == account_id).order_by(Channel.title)
    )
    return list(result.scalars())
