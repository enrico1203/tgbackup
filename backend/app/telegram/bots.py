"""Registry of the bot clients, and the pool that hands one bot to one file.

A bot is a Telegram account like any other as far as the protocol goes: it signs in with a
token instead of a code, it gets its own auth key, its own twenty connections per data
center and its own flood limits. That last part is the whole point of a set: five bots
uploading five different files are five accounts being limited separately, where one
account uploading five files is one account being limited once.

Three things a bot cannot do, and they shape everything here:

  - **It has no dialog list.** `iter_dialogs` on a bot returns nothing, so a channel can
    never be picked from a list the way an account picks one. It is named instead, by id
    or by username, and resolved with `channels.getChannels` passing `access_hash=0`,
    which the server accepts from a bot for a channel it belongs to. That is also the
    membership test: a bot that is not in the channel gets an error rather than a peer.
  - **It has no Saved Messages.** There is nowhere to send a run report, which is why the
    reports of a bot-set job travel through an account (see notify.py).
  - **It is never Premium.** 2 GB per file, so the part size of a bot-set job is the one a
    standard account gets.

The access hash is issued per user, and a bot is a user of its own: the value stored on the
channel row belongs to whichever account discovered the channel and is worth nothing here.
Each bot resolves its own peer and the answer is cached for the life of the process.
"""

from __future__ import annotations

import asyncio
import logging

from sqlalchemy import select
from telethon import TelegramClient, utils
from telethon.errors import (
    AccessTokenExpiredError,
    AccessTokenInvalidError,
    ApiIdInvalidError,
    ChannelInvalidError,
    ChannelPrivateError,
)
from telethon.sessions import StringSession
from telethon.tl.functions.channels import GetChannelsRequest, GetParticipantRequest
from telethon.tl.types import (
    ChannelParticipantAdmin,
    ChannelParticipantCreator,
    InputChannel,
    InputPeerChannel,
    InputPeerSelf,
)

from ..db import SessionLocal
from ..models import DEFAULT_BOT_CONNECTIONS, Bot, BotSet
from ..security import decrypt, encrypt
from .fast_transfer import MAX_CONNECTIONS
from .manager import DEVICE, TelegramError

log = logging.getLogger(__name__)

# How often a worker waiting for a free bot looks again at whether there is still one to
# wait for. See BotManager.lease.
LEASE_POLL_SECONDS = 5.0


def bot_set_budget(bot_set: BotSet | None) -> tuple[int, int]:
    """How many jobs may transfer on this set at once, and the connections each bot opens.

    The mirror of `manager.account_budget`, with one difference that matters: the budget of
    an account is divided among the jobs sharing it, because Telegram counts the twenty
    connections per account. Here every bot has its own twenty, so the number is not
    divided at all. What limits it is the line of the host, and that is a number the user
    sets on the set.
    """
    concurrency = max(1, bot_set.max_concurrent_jobs if bot_set else 1)
    ceiling = bot_set.max_connections if bot_set else DEFAULT_BOT_CONNECTIONS
    return concurrency, max(1, min(MAX_CONNECTIONS, ceiling))


def normalise_channel_id(value: int) -> int:
    """The bare channel id, from either form a user copies.

    Telegram shows a channel as -1001234567890 in some clients and as 1234567890 in
    others; the rows here hold the bare id, which is what `InputChannel` wants. Only the
    negative form carries the 100 prefix, and that is the only one it is stripped from: a
    positive id that happens to begin with those digits is already bare, and cutting them
    off would point the bot at another channel entirely.
    """
    if value < 0:
        text = str(-value)
        if text.startswith("100") and len(text) > 3:
            return int(text[3:])
        return -value
    return value


class _Pool:
    """The bots of one set, handed out one at a time.

    A bot is leased for one file and returned, never held for the length of a run. That is
    what keeps two jobs on the same set from deadlocking: a worker waits before it holds
    anything and never holds one bot while asking for another, so there is no cycle to get
    stuck in. It also means a job configured for five parallel files on a set whose bots
    are busy simply uploads fewer at a time instead of failing.
    """

    def __init__(self) -> None:
        self.queue: asyncio.Queue[int] = asyncio.Queue()
        self.known: set[int] = set()

    def sync(self, bot_ids: list[int]) -> None:
        for bot_id in bot_ids:
            if bot_id not in self.known:
                self.known.add(bot_id)
                self.queue.put_nowait(bot_id)

    def forget(self, bot_id: int) -> None:
        self.known.discard(bot_id)


class BotManager:
    def __init__(self) -> None:
        self._clients: dict[int, TelegramClient] = {}
        # (bot_id, channel tg_id) -> the peer that bot resolved for itself.
        self._peers: dict[tuple[int, int], InputPeerChannel] = {}
        self._pools: dict[int, _Pool] = {}
        self._transfer_locks: dict[int, asyncio.Semaphore] = {}
        self._transfer_limits: dict[int, int] = {}

    # Lifecycle

    async def restore_sessions(self) -> None:
        async with SessionLocal() as session:
            result = await session.execute(select(Bot).where(Bot.enabled.is_(True)))
            enabled = list(result.scalars())

        for bot in enabled:
            try:
                await self.get_client(bot.id)
                log.info("Bot %s reconnected", bot.username or bot.id)
            except Exception as exc:
                log.warning("Reconnecting bot %s failed: %s", bot.username or bot.id, exc)

    async def shutdown(self) -> None:
        for client in list(self._clients.values()):
            try:
                await client.disconnect()
            except Exception:
                pass
        self._clients.clear()
        self._peers.clear()

    async def disconnect(self, bot_id: int) -> None:
        client = self._clients.pop(bot_id, None)
        for key in [key for key in self._peers if key[0] == bot_id]:
            self._peers.pop(key, None)
        if client is not None:
            try:
                await client.disconnect()
            except Exception:
                pass

    # Access

    def is_connected(self, bot_id: int) -> bool:
        client = self._clients.get(bot_id)
        return client is not None and client.is_connected()

    async def get_client(self, bot_id: int) -> TelegramClient:
        client = self._clients.get(bot_id)
        if client is not None and client.is_connected():
            return client

        async with SessionLocal() as session:
            bot = await session.get(Bot, bot_id)
            if bot is None:
                raise TelegramError("Bot not found")
            bot_set = await session.get(BotSet, bot.bot_set_id)
            if bot_set is None:
                raise TelegramError("The bot set no longer exists")
            token = decrypt(bot.token_enc)
            saved = decrypt(bot.session_enc) if bot.session_enc else ""
            api_id = bot_set.api_id
            api_hash = decrypt(bot_set.api_hash_enc)

        client = TelegramClient(
            StringSession(saved),
            api_id,
            api_hash,
            device_model=DEVICE,
            system_version="1.0",
            app_version="1.0",
            connection_retries=None,
            retry_delay=5,
            auto_reconnect=True,
            # The same reason an account wants it: without it a call that spends its
            # attempts raises `ValueError: Request was unsuccessful N time(s)` and every
            # handler written for the real error is bypassed.
            raise_last_call_error=True,
        )
        try:
            await client.connect()
            if not await client.is_user_authorized():
                # A token signs in as many times as it likes and the previous session
                # stays valid, so this costs nothing when the stored one was unreadable.
                await client.sign_in(bot_token=token)
            me = await client.get_me()
        except (AccessTokenInvalidError, AccessTokenExpiredError) as exc:
            await client.disconnect()
            await self._mark(bot_id, "error", "The bot token is invalid or was revoked")
            raise TelegramError(
                "The bot token is invalid or was revoked. Ask BotFather for a new one."
            ) from exc
        except ApiIdInvalidError as exc:
            await client.disconnect()
            await self._mark(bot_id, "error", "Invalid api_id or api_hash on the bot set")
            raise TelegramError(
                "The api_id or api_hash of the bot set is invalid"
            ) from exc
        except Exception as exc:
            await client.disconnect()
            await self._mark(bot_id, "error", str(exc))
            raise TelegramError(f"Connecting the bot failed: {exc}") from exc

        async with SessionLocal() as session:
            bot = await session.get(Bot, bot_id)
            if bot is not None:
                bot.session_enc = encrypt(client.session.save())
                bot.tg_id = me.id
                bot.username = me.username
                bot.first_name = me.first_name
                bot.status = "connected"
                bot.last_error = None
                await session.commit()

        self._clients[bot_id] = client
        return client

    async def _mark(self, bot_id: int, status: str, error: str | None) -> None:
        async with SessionLocal() as session:
            bot = await session.get(Bot, bot_id)
            if bot is not None:
                bot.status = status
                bot.last_error = error[:1000] if error else None
                await session.commit()

    # Channels

    async def peer(self, bot_id: int, tg_id: int) -> InputPeerChannel:
        """The peer this bot holds for a channel, resolved once and kept.

        `access_hash=0` is not a trick: the server accepts it from a bot for a channel the
        bot belongs to, which is exactly the check that has to be made anyway. So the
        answer is both the peer and the proof of membership, and it is why a bot set needs
        nothing at all from the access hash stored on the channel row, which belongs to
        whichever account happened to discover the channel.
        """
        cached = self._peers.get((bot_id, tg_id))
        if cached is not None:
            return cached

        client = await self.get_client(bot_id)
        try:
            result = await client(GetChannelsRequest([InputChannel(tg_id, access_hash=0)]))
        except (ChannelInvalidError, ChannelPrivateError) as exc:
            raise TelegramError(
                "This bot is not in the channel. Add it as an administrator with "
                "permission to post and delete messages, then try again."
            ) from exc
        if not result.chats:
            raise TelegramError("This bot is not in the channel")

        peer = utils.get_input_peer(result.chats[0])
        self._peers[(bot_id, tg_id)] = peer
        return peer

    async def channel_info(self, bot_id: int, tg_id: int) -> dict:
        """Title, username and what the bot is allowed to do in the channel.

        The rights are worth reading and showing: a bot that can post but not delete
        uploads perfectly and then leaves in the channel every file that disappears from
        the source, which is half of what a sync job is for and would otherwise only be
        discovered as messages that never go away.
        """
        client = await self.get_client(bot_id)
        peer = await self.peer(bot_id, tg_id)
        result = await client(GetChannelsRequest([InputChannel(tg_id, access_hash=0)]))
        chat = result.chats[0]

        admin = False
        can_delete = False
        try:
            participant = await client(
                GetParticipantRequest(channel=peer, participant=InputPeerSelf())
            )
            role = participant.participant
            admin = isinstance(role, ChannelParticipantAdmin | ChannelParticipantCreator)
            rights = getattr(role, "admin_rights", None)
            can_delete = isinstance(role, ChannelParticipantCreator) or bool(
                rights and rights.delete_messages
            )
        except Exception as exc:
            # Not fatal: the peer resolved, so the bot is in the channel. Only the detail
            # of what it may do is missing, and the job says what is needed anyway.
            log.info("Reading the rights of bot %d failed: %s", bot_id, exc)

        return {
            "tg_id": chat.id,
            "title": chat.title,
            "username": getattr(chat, "username", None),
            "is_private": getattr(chat, "username", None) is None,
            "kind": "supergroup" if getattr(chat, "megagroup", False) else "channel",
            "participants": getattr(chat, "participants_count", None),
            "admin": admin,
            "can_delete": can_delete,
        }

    async def resolve_channel(self, bot_id: int, identifier: str) -> dict:
        """Finds a channel from what the user typed: an id in either form, or a username."""
        text = identifier.strip()
        if not text:
            raise TelegramError("Give a channel id or username")

        if text.lstrip("-").isdigit():
            return await self.channel_info(bot_id, normalise_channel_id(int(text)))

        client = await self.get_client(bot_id)
        try:
            entity = await client.get_entity(text if text.startswith("@") else f"@{text}")
        except Exception as exc:
            raise TelegramError(f"Channel {text} not found: {exc}") from exc
        return await self.channel_info(bot_id, entity.id)

    # The pool

    def transfer_lock(self, bot_set_id: int, limit: int = 1) -> asyncio.Semaphore:
        """How many jobs may be in their upload phase on this set at the same time."""
        limit = max(1, limit)
        existing = self._transfer_locks.get(bot_set_id)
        if existing is None or self._transfer_limits.get(bot_set_id) != limit:
            self._transfer_locks[bot_set_id] = asyncio.Semaphore(limit)
            self._transfer_limits[bot_set_id] = limit
        return self._transfer_locks[bot_set_id]

    def pool(self, bot_set_id: int, bot_ids: list[int]) -> _Pool:
        pool = self._pools.get(bot_set_id)
        if pool is None:
            pool = _Pool()
            self._pools[bot_set_id] = pool
        pool.sync(bot_ids)
        return pool

    def drop_from_pool(self, bot_set_id: int, bot_id: int) -> None:
        pool = self._pools.get(bot_set_id)
        if pool is not None:
            pool.forget(bot_id)

    async def lease(self, bot_set_id: int, valid: set[int]) -> int | None:
        """Takes a bot out of the pool, or None when the caller has none left to wait for.

        `valid` is read again at every turn rather than once, and that is what the timeout
        is for. A worker waiting here has a file in its hands and will get a bot as soon as
        another file finishes, but if every bot of the run is retired while it waits there
        would be nothing left to wake it: it would hold the run open for ever on a set that
        no longer has a single usable bot. Five seconds late is nothing next to that.
        """
        pool = self._pools[bot_set_id]
        while True:
            if not valid:
                return None
            try:
                bot_id = await asyncio.wait_for(pool.queue.get(), timeout=LEASE_POLL_SECONDS)
            except TimeoutError:
                continue
            if bot_id in valid:
                return bot_id
            # Removed or disabled since the pool was filled: it is not put back, and
            # `known` no longer holds it, so a later sync can add it again.
            pool.forget(bot_id)

    def release(self, bot_set_id: int, bot_id: int) -> None:
        pool = self._pools.get(bot_set_id)
        if pool is None:
            return
        if bot_id in pool.known:
            pool.queue.put_nowait(bot_id)


bots = BotManager()
