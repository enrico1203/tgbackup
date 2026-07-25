"""Registry dei client Telegram connessi e flusso di login.

I client restano connessi per tutta la vita del processo: un sync job puo partire in
qualsiasi momento e non deve rifare l'autenticazione. Si disconnettono solo quando
l'account viene rimosso dalla UI.
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
    """Errore da mostrare direttamente all'utente."""


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
        # Piu job sullo stesso account si contenderebbero il tetto di 20 connessioni
        # per DC, rallentandosi a vicenda: la fase di upload viene serializzata.
        self._upload_locks: dict[int, asyncio.Semaphore] = {}

    # Ciclo di vita

    async def restore_sessions(self) -> None:
        async with SessionLocal() as session:
            result = await session.execute(
                select(TelegramAccount).where(TelegramAccount.session_enc.is_not(None))
            )
            accounts = list(result.scalars())

        for account in accounts:
            try:
                await self._connect(account)
                log.info("Account Telegram %s riconnesso", account.label)
            except Exception as exc:
                log.warning("Riconnessione dell'account %s fallita: %s", account.label, exc)
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
            raise TelegramError("La sessione non e piu valida, ricollega l'account")

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

    # Accesso

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
            raise TelegramError("Account Telegram non collegato")
        return await self._connect(account)

    def upload_lock(self, account_id: int) -> asyncio.Semaphore:
        if account_id not in self._upload_locks:
            self._upload_locks[account_id] = asyncio.Semaphore(1)
        return self._upload_locks[account_id]

    # Login

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
            raise TelegramError("api_id o api_hash non validi") from exc
        except PhoneNumberInvalidError as exc:
            await client.disconnect()
            raise TelegramError("Numero di telefono non valido") from exc
        except Exception as exc:
            await client.disconnect()
            raise TelegramError(f"Invio del codice fallito: {exc}") from exc

        self._pending[account_id] = _Pending(client, phone, sent.phone_code_hash)

    async def submit_code(self, account_id: int, code: str) -> str:
        pending = self._pending.get(account_id)
        if pending is None:
            raise TelegramError("Sessione di login scaduta, ricomincia dall'inizio")

        try:
            await pending.client.sign_in(
                phone=pending.phone, code=code, phone_code_hash=pending.phone_code_hash
            )
        except SessionPasswordNeededError:
            return "password"
        except PhoneCodeInvalidError as exc:
            raise TelegramError("Codice errato") from exc
        except PhoneCodeExpiredError as exc:
            await self._drop_pending(account_id)
            raise TelegramError("Codice scaduto, richiedine uno nuovo") from exc

        await self._finalize(account_id)
        return "done"

    async def submit_password(self, account_id: int, password: str) -> str:
        pending = self._pending.get(account_id)
        if pending is None:
            raise TelegramError("Sessione di login scaduta, ricomincia dall'inizio")
        try:
            await pending.client.sign_in(password=password)
        except Exception as exc:
            raise TelegramError(f"Password a due fattori rifiutata: {exc}") from exc

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
                raise TelegramError("Account non trovato")

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

    # Canali

    @staticmethod
    def input_peer(channel) -> InputPeerChannel | InputPeerChat:
        """Costruisce il peer dai dati salvati, senza risolverlo su Telegram.

        Non si passa mai l'id numerico nudo a get_entity: un intero positivo e
        ambiguo e Telethon lo interpreta come utente. Inoltre StringSession non
        conserva la cache delle entita, quindi dopo ogni riavvio la risoluzione per
        id fallirebbe. Con id e access_hash salvati il peer si costruisce a mano, non
        serve rete e funziona sempre.
        """
        if channel.kind == "group":
            return InputPeerChat(chat_id=channel.tg_id)
        if channel.access_hash is None:
            raise TelegramError(
                f"Il canale {channel.title} non ha access_hash salvato: "
                "aggiorna l'elenco dei canali dell'account"
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
