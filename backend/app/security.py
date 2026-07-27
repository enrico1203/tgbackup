import base64
import hashlib
from datetime import UTC, datetime, timedelta

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from cryptography.fernet import Fernet, InvalidToken

from .config import settings

_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, hashed: str) -> bool:
    try:
        return _hasher.verify(hashed, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def _fernet() -> Fernet:
    # The Fernet key derives from APP_SECRET: changing APP_SECRET makes the stored
    # Telegram sessions and rclone configuration unreadable, and they have to be
    # entered again.
    digest = hashlib.sha256(settings.app_secret.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt(value: str) -> str:
    return _fernet().encrypt(value.encode()).decode()


def decrypt(value: str) -> str:
    try:
        return _fernet().decrypt(value.encode()).decode()
    except InvalidToken as exc:
        raise ValueError(
            "Encrypted value cannot be read: APP_SECRET changed since it was stored"
        ) from exc


def create_token(user_id: int) -> str:
    now = datetime.now(UTC)
    payload = {
        "sub": str(user_id),
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(hours=settings.token_ttl_hours)).timestamp()),
    }
    return jwt.encode(payload, settings.app_secret, algorithm="HS256")


def decode_token(token: str) -> int | None:
    try:
        payload = jwt.decode(token, settings.app_secret, algorithms=["HS256"])
        # A session token carries no scope. Anything that does was issued for one precise
        # operation, and travels where a session token must never be accepted from: a
        # download ticket lives in a URL, which ends up in the browser history and in the
        # nginx log.
        if payload.get("scope") is not None:
            return None
        return int(payload["sub"])
    except (jwt.PyJWTError, KeyError, ValueError):
        return None


# Long enough for a slow phone to follow the redirect, short enough that a ticket left in
# a log is worth nothing by the time anybody reads it.
DOWNLOAD_TICKET_TTL_SECONDS = 300


def create_download_ticket(user_id: int, file_id: int) -> str:
    """One-off pass for a single file download.

    The browser downloads through a plain navigation, where no Authorization header can
    be set: the credential has to be in the URL. This one opens one file, for five
    minutes, and is refused by `decode_token` as a session token.
    """
    now = datetime.now(UTC)
    payload = {
        "sub": str(user_id),
        "fid": file_id,
        "scope": "file-download",
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=DOWNLOAD_TICKET_TTL_SECONDS)).timestamp()),
    }
    return jwt.encode(payload, settings.app_secret, algorithm="HS256")


def decode_download_ticket(token: str, file_id: int) -> int | None:
    """Returns the user the ticket was issued to, if it is valid for this file."""
    try:
        payload = jwt.decode(token, settings.app_secret, algorithms=["HS256"])
        if payload.get("scope") != "file-download" or payload.get("fid") != file_id:
            return None
        return int(payload["sub"])
    except (jwt.PyJWTError, KeyError, ValueError):
        return None
