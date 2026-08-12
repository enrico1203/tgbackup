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

    # The channel rows of an account go with it, and a job on a bot set may be writing to
    # one of them: it would lose its destination and its whole index without ever having
    # named this account. So the row has to be freed first, by moving that job elsewhere.
    borrowed = await session.scalar(
        select(func.count(SyncJob.id)).where(
            SyncJob.channel_id.in_(
                select(Channel.id).where(Channel.account_id == account_id)
            )
        )
    )
    if borrowed:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"{borrowed} sync jobs write to channels discovered with this account: move "
            "them onto another account before disconnecting it",
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


async def channel_for_account(session, account_id: int, tg_id: int) -> Channel:
    """The row this account holds for one Telegram channel, fetched from its dialogs if new.

    Channel rows are per account, because the access_hash is issued per user: moving a job
    to another account means moving it to that account's own row for the same channel, and
    never keeping the row of the account it is leaving. What the job holds is untouched by
    that: message ids belong to the channel, so an index uploaded by one account is read,
    downloaded and deleted by another exactly as it was.

    A row that is already here is taken as it is, since it was written from the dialogs of
    this account and therefore proves it saw the channel. When there is none, or it has no
    access_hash to build a peer from, the dialogs are read and the failure to find the
    channel there is the answer to give the user: this account is not in it.
    """
    channel = await session.scalar(
        select(Channel).where(Channel.account_id == account_id, Channel.tg_id == tg_id)
    )
    if channel is not None and (channel.kind == "group" or channel.access_hash is not None):
        return channel

    account = await session.get(TelegramAccount, account_id)
    label = account.label if account else str(account_id)
    try:
        fetched = await manager.list_channels(account_id)
    except (TelegramError, OSError, ValueError) as exc:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"The channels of the account {label} could not be read: {exc}",
        ) from exc

    live = next((item for item in fetched if item["tg_id"] == tg_id), None)
    if live is None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"The account {label} is not in this channel. Join it with that account, "
            "then try again.",
        )

    if channel is None:
        # A row with no account behind it is one a bot set created by naming the channel,
        # and it is adopted rather than duplicated: one Telegram channel is one row, so a
        # job moved from a bot set onto an account keeps pointing at the same index.
        channel = await session.scalar(
            select(Channel).where(Channel.account_id.is_(None), Channel.tg_id == tg_id)
        )
    if channel is None:
        channel = Channel(account_id=account_id, tg_id=tg_id)
        session.add(channel)
    channel.account_id = account_id
    channel.access_hash = live["access_hash"]
    channel.title = live["title"]
    channel.username = live["username"]
    channel.is_private = live["is_private"]
    channel.kind = live["kind"]
    channel.participants = live["participants"]
    channel.last_seen_at = utcnow()
    await session.flush()
    return channel


async def carry_channel_check(session, old_channel_id: int, new_channel_id: int) -> None:
    """Moves an automatic check onto the row a job has just been moved to.

    The scheduled check is configured on the channel row and finds what to compare through
    the sync jobs writing to that row, so a row every job has left would go on running
    against nothing at all and report a healthy channel for ever. It is carried only when
    the old row is left with no sync job, and only onto a row that has no check of its own,
    so nothing configured by hand is overwritten and no channel ends up checked twice.
    """
    old = await session.get(Channel, old_channel_id)
    new = await session.get(Channel, new_channel_id)
    if old is None or new is None or old.id == new.id:
        return
    if not old.check_interval_days or new.check_interval_days:
        return

    remaining = await session.scalar(
        select(func.count(SyncJob.id)).where(SyncJob.channel_id == old.id)
    )
    if remaining:
        return

    new.check_interval_days = old.check_interval_days
    new.check_hour = old.check_hour
    new.check_repair = old.check_repair
    new.last_check_at = old.last_check_at
    new.last_check_result = old.last_check_result
    old.check_interval_days = 0
