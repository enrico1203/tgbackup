import asyncio
import contextlib
import json

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ..db import SessionLocal
from ..models import User
from ..security import decode_token
from ..sync.progress import hub

router = APIRouter()


@router.websocket("/ws/progress")
async def progress_socket(websocket: WebSocket, token: str = "") -> None:
    # Authorization headers cannot be set on a WebSocket from the browser, so the token
    # travels in the query string.
    user_id = decode_token(token)
    if user_id is None:
        await websocket.close(code=4401)
        return

    async with SessionLocal() as session:
        user = await session.get(User, user_id)
        if user is None or user.must_change_password:
            await websocket.close(code=4403)
            return

    await websocket.accept()
    queue = hub.subscribe()
    try:
        await websocket.send_text(json.dumps(hub.snapshot()))
        while True:
            payload = await queue.get()
            await websocket.send_text(payload)
    except (WebSocketDisconnect, asyncio.CancelledError, RuntimeError):
        pass
    finally:
        hub.unsubscribe(queue)
        with contextlib.suppress(Exception):
            await websocket.close()
