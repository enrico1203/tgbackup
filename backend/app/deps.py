from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from .db import get_session
from .models import User
from .security import decode_token

bearer = HTTPBearer(auto_error=False)

SessionDep = Annotated[AsyncSession, Depends(get_session)]


async def current_user(
    session: SessionDep,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> User:
    if credentials is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Authentication required")

    user_id = decode_token(credentials.credentials)
    if user_id is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Session expired, please sign in again")

    user = await session.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "User not found")
    return user


UserDep = Annotated[User, Depends(current_user)]


async def active_user(user: UserDep) -> User:
    """Blocks every application route until the initial password has been changed."""
    if user.must_change_password:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Change your password before using the application",
        )
    return user


ActiveUserDep = Annotated[User, Depends(active_user)]
