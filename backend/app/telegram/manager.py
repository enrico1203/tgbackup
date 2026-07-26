"""Registry of connected Telegram clients and the sign in flow.

Clients stay connected for the whole life of the process: a sync job can start at any
moment and must not have to authenticate again. They disconnect only when the account is
removed from the interface.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field

from sqlalchemy import select
from telethon import TelegramClient
from telethon.errors import (
    ApiIdInvalidError,
    AuthKeyUnregisteredError,
    PhoneCodeExpiredError,
    PhoneCodeInvalidError,
    PhoneNumberInvalidError,
    SessionPasswordNeededError,
)
from telethon.sessions import StringSession
from telethon.tl.types import Channel as TLChannel
from telethon.tl.types import Chat as TLChat
from telethon.tl.types import InputPeerChannel, InputPeerChat

from ..config import settings
from ..db import SessionLocal
from ..models import TelegramAccount
from ..security import decrypt, encrypt

log = logging.getLogger(__name__)

DEVICE = "tgbackup"
PENDING_TTL_SECONDS = 15 * 60


class TelegramError(Exception):
    """Error meant to be shown directly to the user."""


@dataclass
class _Pending:
    client: TelegramClient
    phone: str
    phone_code_hash: str
    created_at: float = field(default_factory=time.monotonic)


class TelegramManager:
    def __init__(self) -> None:
        self._clients: dict[int, TelegramClient] = {}
        self._pending: dict[int, _Pending] = {}
        # Several jobs on the same account would fight over the ceiling of 20
        # connections per data center: the upload phase is serialized.
        self._upload_locks: dict[int, asyncio.Semaphore] = {}
        self._upload_limits: dict[int, int] = {}

    # Lifecycle

    async def restore_sessions(self) -> None:
        async with SessionLocal() as session:
            result = await session.execute(
                select(TelegramAccount).where(TelegramAccount.session_enc.is_not(None))
            )
            accounts = list(result.scalars())

        for account in accounts:
            try:
                await self._connect(account)
                log.info("Telegram account %s reconnected", account.label)
            except Exception as exc:
                log.warning("Reconnecting account %s failed: %s", account.label, exc)
                await self._mark_status(account.id, "error", str(exc))

    async def shutdown(self) -> None:
        for client in list(self._clients.values()):
            try:
                await client.disconnect()
            except Exception:
                pass
        self._clients.clear()
        for pending in list(self._pending.values()):
            try:
                await pending.client.disconnect()
            except Exception:
                pass
        self._pending.clear()

    async def _connect(self, account: TelegramAccount) -> TelegramClient:
        client = TelegramClient(
            StringSession(decrypt(account.session_enc)),
            account.api_id,
            decrypt(account.api_hash_enc),
            device_model=DEVICE,
            system_version="1.0",
            app_version="1.0",
            connection_retries=None,
            retry_delay=5,
            auto_reconnect=True,
        )
        await client.connect()
        if not await client.is_user_authorized():
            await client.disconnect()
            raise TelegramError("The session is no longer valid, link the account again")

        self._clients[account.id] = client
        await self._mark_status(account.id, "connected", None)
        return client

    async def _mark_status(self, account_id: int, status: str, error: str | None) -> None:
        async with SessionLocal() as session:
            account = await session.get(TelegramAccount, account_id)
            if account is not None:
                account.status = status
                account.last_error = error
                await session.commit()

    # Access

    def is_connected(self, account_id: int) -> bool:
        client = self._clients.get(account_id)
        return client is not None and client.is_connected()

    async def get_client(self, account_id: int) -> TelegramClient:
        client = self._clients.get(account_id)
        if client is not None and client.is_connected():
            return client

        async with SessionLocal() as session:
            account = await session.get(TelegramAccount, account_id)
        if account is None or not account.session_enc:
            raise TelegramError("Telegram account not linked")
        return await self._connect(account)

    def upload_lock(self, account_id: int, limit: int = 2) -> asyncio.Semaphore:
        """Semaphore limiting how many jobs upload at the same time on this account.

        When the limit changes a new semaphore is created: jobs already uploading will
        release the old one, so for the duration of a run the effective concurrency can
        differ from the value just set. That is preferable to blocking the change until
        every job has finished, which with runs lasting days would mean never being able
        to change it.
        """
        limit = max(1, limit)
        existing = self._upload_locks.get(account_id)
        if existing is None or self._upload_limits.get(account_id) != limit:
            self._upload_locks[account_id] = asyncio.Semaphore(limit)
            self._upload_limits[account_id] = limit
        return self._upload_locks[account_id]

    # Sign in

    def _sweep_pending(self) -> None:
        now = time.monotonic()
        for account_id, pending in list(self._pending.items()):
            if now - pending.created_at > PENDING_TTL_SECONDS:
                asyncio.create_task(self._drop_pending(account_id))

    async def _drop_pending(self, account_id: int) -> None:
        pending = self._pending.pop(account_id, None)
        if pending is not None:
            try:
                await pending.client.disconnect()
            except Exception:
                pass

    async def start_login(
        self, account_id: int, api_id: int, api_hash: str, phone: str
    ) -> None:
        self._sweep_pending()
        await self._drop_pending(account_id)

        client = TelegramClient(
            StringSession(),
            api_id,
            api_hash,
            device_model=DEVICE,
            system_version="1.0",
            app_version="1.0",
        )
        try:
            await client.connect()
            sent = await client.send_code_request(phone)
        except ApiIdInvalidError as exc:
            await client.disconnect()
            raise TelegramError("Invalid api_id or api_hash") from exc
        except PhoneNumberInvalidError as exc:
            await client.disconnect()
            raise TelegramError("Invalid phone number") from exc
        except Exception as exc:
            await client.disconnect()
            raise TelegramError(f"Sending the code failed: {exc}") from exc

        self._pending[account_id] = _Pending(client, phone, sent.phone_code_hash)

    async def submit_code(self, account_id: int, code: str) -> str:
        pending = self._pending.get(account_id)
        if pending is None:
            raise TelegramError("Sign in session expired, start again from the beginning")

        try:
            await pending.client.sign_in(
                phone=pending.phone, code=code, phone_code_hash=pending.phone_code_hash
            )
        except SessionPasswordNeededError:
            return "password"
        except PhoneCodeInvalidError as exc:
            raise TelegramError("Wrong code") from exc
        except PhoneCodeExpiredError as exc:
            await self._drop_pending(account_id)
            raise TelegramError("Code expired, request a new one") from exc

        await self._finalize(account_id)
        return "done"

    async def submit_password(self, account_id: int, password: str) -> str:
        pending = self._pending.get(account_id)
        if pending is None:
            raise TelegramError("Sign in session expired, start again from the beginning")
        try:
            await pending.client.sign_in(password=password)
        except Exception as exc:
            raise TelegramError(f"Two-step password rejected: {exc}") from exc

        await self._finalize(account_id)
        return "done"

    async def _finalize(self, account_id: int) -> None:
        pending = self._pending.pop(account_id)
        client = pending.client
        me = await client.get_me()

        async with SessionLocal() as session:
            account = await session.get(TelegramAccount, account_id)
            if account is None:
                await client.disconnect()
                raise TelegramError("Account not found")

            account.session_enc = encrypt(client.session.save())
            account.tg_user_id = me.id
            account.first_name = me.first_name
            account.username = me.username
            account.is_premium = bool(getattr(me, "premium", False))
            account.default_part_size = (
                settings.split_premium if account.is_premium else settings.split_standard
            )
            account.status = "connected"
            account.last_error = None
            await session.commit()

        self._clients[account_id] = client

    async def logout(self, account_id: int) -> None:
        await self._drop_pending(account_id)
        client = self._clients.pop(account_id, None)
        if client is None:
            return
        try:
            await client.log_out()
        except (AuthKeyUnregisteredError, Exception):
            pass
        finally:
            try:
                await client.disconnect()
            except Exception:
                pass

    # Channels

    @staticmethod
    def input_peer(channel) -> InputPeerChannel | InputPeerChat:
        """Builds the peer from stored data, without resolving it against Telegram.

        A bare numeric id is never passed to get_entity: a positive integer is ambiguous
        and Telethon reads it as a user. On top of that StringSession does not keep the
        entity cache, so resolving by id would fail after every restart. With the stored
        id and access_hash the peer is built by hand, needs no network and always works.
        """
        if channel.kind == "group":
            return InputPeerChat(chat_id=channel.tg_id)
        if channel.access_hash is None:
            raise TelegramError(
                f"Channel {channel.title} has no stored access_hash: "
                "refresh the account channel list"
            )
        return InputPeerChannel(channel_id=channel.tg_id, access_hash=channel.access_hash)

    async def list_channels(self, account_id: int) -> list[dict]:
        client = await self.get_client(account_id)
        channels: list[dict] = []
        async for dialog in client.iter_dialogs():
            entity = dialog.entity
            if isinstance(entity, TLChannel):
                kind = "supergroup" if entity.megagroup else "channel"
            elif isinstance(entity, TLChat):
                kind = "group"
            else:
                continue

            username = getattr(entity, "username", None)
            channels.append(
                {
                    "tg_id": entity.id,
                    "access_hash": getattr(entity, "access_hash", None),
                    "title": entity.title,
                    "username": username,
                    "is_private": username is None,
                    "kind": kind,
                    "participants": getattr(entity, "participants_count", None),
                }
            )
        return channels


manager = TelegramManager()
