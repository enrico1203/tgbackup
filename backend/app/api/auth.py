from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from ..deps import SessionDep, UserDep
from ..models import User
from ..schemas import ChangePasswordIn, LoginIn, MeOut, TokenOut
from ..security import create_token, hash_password, verify_password
from ..models import utcnow

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/login", response_model=TokenOut)
async def login(payload: LoginIn, session: SessionDep) -> TokenOut:
    result = await session.execute(select(User).where(User.username == payload.username))
    user = result.scalar_one_or_none()
    if user is None or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Wrong username or password")

    return TokenOut(
        token=create_token(user.id),
        must_change_password=user.must_change_password,
        username=user.username,
    )


@router.get("/me", response_model=MeOut)
async def me(user: UserDep) -> User:
    return user


@router.post("/change-password", response_model=MeOut)
async def change_password(
    payload: ChangePasswordIn, user: UserDep, session: SessionDep
) -> User:
    if not verify_password(payload.current_password, user.password_hash):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "The current password is not correct")
    if payload.new_password == payload.current_password:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "The new password must differ from the current one"
        )

    user.password_hash = hash_password(payload.new_password)
    user.must_change_password = False
    user.password_changed_at = utcnow()
    await session.commit()
    await session.refresh(user)
    return user
