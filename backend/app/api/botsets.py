"""Bot sets: several bots in one channel, used by a sync job as one uploader.

The shape mirrors `api/accounts.py`, with the differences a bot brings. There is no sign in
flow in two steps: a token is either valid or it is not, and the answer comes back on the
first request. There is no dialog list to pick a channel from either, so a channel is named
and resolved through a bot, which is at the same time the proof that the bot is in it.
"""

import logging

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import func, select

from ..deps import ActiveUserDep, SessionDep
from ..models import Bot, BotSet, Channel, SyncJob, TelegramAccount, utcnow
from ..schemas import (
    BotChannelStatus,
    BotIn,
    BotOut,
    BotSetChannelCheckOut,
    BotSetChannelIn,
    BotSetIn,
    BotSetOut,
    BotSetUpdate,
    BotUpdate,
    ChannelOut,
)
from ..security import decrypt, encrypt
from ..telegram.bots import bots
from ..telegram.manager import TelegramError

router = APIRouter(prefix="/api/botsets", tags=["botsets"])
log = logging.getLogger(__name__)


def _bot_out(bot: Bot) -> BotOut:
    out = BotOut.model_validate(bot)
    out.connected = bots.is_connected(bot.id)
    return out


async def _to_out(session, bot_set: BotSet) -> BotSetOut:
    result = await session.execute(
        select(Bot).where(Bot.bot_set_id == bot_set.id).order_by(Bot.id)
    )
    members = [_bot_out(bot) for bot in result.scalars()]
    jobs = await session.scalar(
        select(func.count(SyncJob.id)).where(SyncJob.bot_set_id == bot_set.id)
    )
    # Field by field rather than `model_validate`: the row carries a lazy `bots`
    # relationship, and reading it while building the answer is IO in the middle of a
    # serialization, which the async session refuses. The members were loaded above.
    return BotSetOut(
        id=bot_set.id,
        name=bot_set.name,
        api_id=bot_set.api_id,
        max_connections=bot_set.max_connections,
        max_concurrent_jobs=bot_set.max_concurrent_jobs,
        default_part_size=bot_set.default_part_size,
        created_at=bot_set.created_at,
        bots=members,
        bots_ready=sum(1 for bot in members if bot.enabled and bot.connected),
        jobs_count=jobs or 0,
    )


async def _get_set(session, bot_set_id: int) -> BotSet:
    bot_set = await session.get(BotSet, bot_set_id)
    if bot_set is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Bot set not found")
    return bot_set


async def _credentials(session, payload) -> tuple[int, str] | None:
    """The api_id and api_hash to sign the bots in with, from wherever they come.

    An account already linked is the ordinary answer: the credentials identify the
    application, not the user, so the ones that work for it work for any bot. Typing them
    again is offered for an installation that has never linked an account.
    """
    if payload.from_account_id:
        account = await session.get(TelegramAccount, payload.from_account_id)
        if account is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Telegram account not found")
        return account.api_id, decrypt(account.api_hash_enc)
    if payload.api_id and payload.api_hash:
        return payload.api_id, payload.api_hash
    return None


@router.get("", response_model=list[BotSetOut])
async def list_bot_sets(session: SessionDep, _: ActiveUserDep) -> list[BotSetOut]:
    result = await session.execute(select(BotSet).order_by(BotSet.id))
    return [await _to_out(session, bot_set) for bot_set in result.scalars()]


@router.post("", response_model=BotSetOut, status_code=status.HTTP_201_CREATED)
async def create_bot_set(
    payload: BotSetIn, session: SessionDep, _: ActiveUserDep
) -> BotSetOut:
    credentials = await _credentials(session, payload)
    if credentials is None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Pick the account whose api_id and api_hash the bots sign in with, or give "
            "a pair of your own: a bot token alone cannot open an MTProto connection.",
        )
    api_id, api_hash = credentials

    bot_set = BotSet(
        name=payload.name,
        api_id=api_id,
        api_hash_enc=encrypt(api_hash),
        max_connections=payload.max_connections,
        max_concurrent_jobs=payload.max_concurrent_jobs,
    )
    session.add(bot_set)
    await session.commit()
    await session.refresh(bot_set)
    return await _to_out(session, bot_set)


@router.patch("/{bot_set_id}", response_model=BotSetOut)
async def update_bot_set(
    bot_set_id: int, payload: BotSetUpdate, session: SessionDep, _: ActiveUserDep
) -> BotSetOut:
    bot_set = await _get_set(session, bot_set_id)

    credentials = await _credentials(session, payload)
    if credentials is not None:
        bot_set.api_id, api_hash = credentials
        bot_set.api_hash_enc = encrypt(api_hash)
        # The sessions were built with the old credentials and are not worth keeping: the
        # bots sign in again on the next request, which costs one call each.
        result = await session.execute(select(Bot).where(Bot.bot_set_id == bot_set_id))
        for bot in result.scalars():
            bot.session_enc = None
            await bots.disconnect(bot.id)

    data = payload.model_dump(
        exclude_unset=True, exclude={"from_account_id", "api_id", "api_hash"}
    )
    for field, value in data.items():
        if value is not None:
            setattr(bot_set, field, value)
    await session.commit()
    await session.refresh(bot_set)
    return await _to_out(session, bot_set)


@router.delete("/{bot_set_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_bot_set(bot_set_id: int, session: SessionDep, _: ActiveUserDep) -> None:
    bot_set = await _get_set(session, bot_set_id)
    jobs = await session.scalar(
        select(func.count(SyncJob.id)).where(SyncJob.bot_set_id == bot_set_id)
    )
    if jobs:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"The set is used by {jobs} sync jobs, move them onto another transport "
            "before deleting it",
        )

    result = await session.execute(select(Bot.id).where(Bot.bot_set_id == bot_set_id))
    for bot_id in result.scalars():
        await bots.disconnect(bot_id)
        bots.drop_from_pool(bot_set_id, bot_id)
    await session.delete(bot_set)
    await session.commit()


@router.post("/{bot_set_id}/bots", response_model=BotOut, status_code=status.HTTP_201_CREATED)
async def add_bot(
    bot_set_id: int, payload: BotIn, session: SessionDep, _: ActiveUserDep
) -> BotOut:
    """Adds one bot by its token, and connects it right away.

    Connecting here rather than at the first run is the point: a token typed wrong, or one
    whose bot was never added to the channel, has to be an error on this button and not a
    job that fails at three in the morning.
    """
    await _get_set(session, bot_set_id)
    token = payload.token.strip()

    bot = Bot(bot_set_id=bot_set_id, token_enc=encrypt(token))
    session.add(bot)
    await session.commit()
    await session.refresh(bot)

    try:
        await bots.get_client(bot.id)
    except Exception as exc:
        # Anything at all: an invalid token, a network that is not there, or the unique
        # constraint refusing a bot that is already in this set, which is what the sign in
        # discovers when it writes back the id it just learned. The row goes either way,
        # so a failed attempt leaves nothing behind to clean up by hand.
        await bots.disconnect(bot.id)
        await session.delete(bot)
        await session.commit()
        message = (
            str(exc)
            if isinstance(exc, TelegramError)
            else "This bot could not be added: it may already be in the set, or Telegram "
            f"refused it ({type(exc).__name__})"
        )
        raise HTTPException(status.HTTP_400_BAD_REQUEST, message) from exc

    await session.refresh(bot)
    return _bot_out(bot)


@router.patch("/{bot_set_id}/bots/{bot_id}", response_model=BotOut)
async def update_bot(
    bot_set_id: int, bot_id: int, payload: BotUpdate, session: SessionDep, _: ActiveUserDep
) -> BotOut:
    bot = await session.get(Bot, bot_id)
    if bot is None or bot.bot_set_id != bot_set_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Bot not found")

    if payload.enabled is not None:
        bot.enabled = payload.enabled
        if not payload.enabled:
            # Out of the rotation at once: a run already going drops it when the pool
            # hands it over and finds it is no longer valid.
            bots.drop_from_pool(bot_set_id, bot_id)
            await bots.disconnect(bot_id)
            bot.status = "disconnected"
    await session.commit()
    await session.refresh(bot)

    if bot.enabled:
        try:
            await bots.get_client(bot.id)
            await session.refresh(bot)
        except TelegramError as exc:
            log.warning("Bot %d could not be connected: %s", bot_id, exc)
    return _bot_out(bot)


@router.delete(
    "/{bot_set_id}/bots/{bot_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def remove_bot(
    bot_set_id: int, bot_id: int, session: SessionDep, _: ActiveUserDep
) -> None:
    bot = await session.get(Bot, bot_id)
    if bot is None or bot.bot_set_id != bot_set_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Bot not found")
    await bots.disconnect(bot_id)
    bots.drop_from_pool(bot_set_id, bot_id)
    await session.delete(bot)
    await session.commit()


@router.get("/{bot_set_id}/channels", response_model=list[ChannelOut])
async def list_channels(
    bot_set_id: int, session: SessionDep, _: ActiveUserDep
) -> list[Channel]:
    """Every channel this installation knows, whoever discovered it.

    A bot resolves its own peer from the id, so any row here is one a set can write to as
    long as its bots are members, which is what the check below answers. Rows are not
    duplicated per set on purpose: a job moved from an account onto a bot set keeps
    pointing at the same channel and therefore at the same index.
    """
    await _get_set(session, bot_set_id)
    result = await session.execute(select(Channel).order_by(Channel.title))
    return list(result.scalars())


@router.post(
    "/{bot_set_id}/channels", response_model=ChannelOut, status_code=status.HTTP_201_CREATED
)
async def add_channel(
    bot_set_id: int, payload: BotSetChannelIn, session: SessionDep, _: ActiveUserDep
) -> Channel:
    """Names a channel the bots are in, for an installation that has no account to list it.

    The row is reused when the channel is already known here, whichever account found it:
    one Telegram channel is one row, so the index of a job that moves between transports
    never splits in two.
    """
    await _get_set(session, bot_set_id)
    result = await session.execute(
        select(Bot).where(Bot.bot_set_id == bot_set_id, Bot.enabled.is_(True)).order_by(Bot.id)
    )
    members = list(result.scalars())
    if not members:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "Add a bot to the set before adding a channel"
        )

    last: Exception | None = None
    info = None
    for bot in members:
        try:
            info = await bots.resolve_channel(bot.id, payload.identifier)
            break
        except TelegramError as exc:
            last = exc
    if info is None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"No bot of the set could reach this channel: {last}",
        )

    channel = await session.scalar(select(Channel).where(Channel.tg_id == info["tg_id"]))
    if channel is None:
        # No account behind it: nothing here has ever seen this channel as a user, so
        # browsing, restore and the channel check will ask for one when they are used.
        channel = Channel(account_id=None, tg_id=info["tg_id"])
        session.add(channel)
    channel.title = info["title"]
    channel.username = info["username"]
    channel.is_private = info["is_private"]
    channel.kind = info["kind"]
    channel.participants = info["participants"]
    channel.last_seen_at = utcnow()
    await session.commit()
    await session.refresh(channel)
    return channel


@router.get("/{bot_set_id}/channels/{channel_id}/check", response_model=BotSetChannelCheckOut)
async def check_channel(
    bot_set_id: int, channel_id: int, session: SessionDep, _: ActiveUserDep
) -> BotSetChannelCheckOut:
    """What each bot of the set can do in this channel, said one bot at a time.

    Membership is what a job needs to upload. Deleting is what it needs for the other half
    of its work, removing from the channel the files that disappeared from the source, and
    a bot without that right will do everything else perfectly while silently keeping them.
    """
    await _get_set(session, bot_set_id)
    channel = await session.get(Channel, channel_id)
    if channel is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Channel not found")

    result = await session.execute(
        select(Bot).where(Bot.bot_set_id == bot_set_id, Bot.enabled.is_(True)).order_by(Bot.id)
    )
    statuses: list[BotChannelStatus] = []
    for bot in result.scalars():
        label = bot.username or bot.first_name or f"bot {bot.id}"
        try:
            info = await bots.channel_info(bot.id, channel.tg_id)
            statuses.append(
                BotChannelStatus(
                    bot_id=bot.id,
                    label=label,
                    member=True,
                    admin=info["admin"],
                    can_delete=info["can_delete"],
                )
            )
        except Exception as exc:
            statuses.append(
                BotChannelStatus(
                    bot_id=bot.id,
                    label=label,
                    member=False,
                    admin=False,
                    can_delete=False,
                    error=str(exc)[:300],
                )
            )
    return BotSetChannelCheckOut(
        channel_id=channel.id, channel_title=channel.title, bots=statuses
    )
