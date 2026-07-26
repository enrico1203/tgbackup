import asyncio
from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, UploadFile, status
from fastapi.responses import Response

from .. import transfer
from ..deps import ActiveUserDep, SessionDep
from ..schemas import ExportChannelOut, ImportPreviewOut, ImportResultOut

router = APIRouter(prefix="/api/export", tags=["export"])


@router.get("/channels", response_model=list[ExportChannelOut])
async def exportable_channels(session: SessionDep, _: ActiveUserDep) -> list[dict]:
    return await transfer.channel_summaries(session)


@router.get("/channels/{channel_id}")
async def download_channel(channel_id: int, session: SessionDep, _: ActiveUserDep) -> Response:
    try:
        payload = await transfer.build_export(session, channel_id)
    except transfer.TransferError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc

    # Serialising and compressing tens of thousands of files is CPU work: in a thread, or
    # the whole backend stops answering while a large channel is exported.
    body = await asyncio.to_thread(transfer.encode, payload)
    name = transfer.export_filename(payload["channel"]["title"], payload["channel"]["tg_id"])
    return Response(
        content=body,
        media_type="application/gzip",
        headers={
            "Content-Disposition": f'attachment; filename="{name}"',
            # The browser has to read the name to save the file with it.
            "Access-Control-Expose-Headers": "Content-Disposition",
        },
    )


async def _read_upload(file: UploadFile) -> dict:
    raw = await file.read()
    try:
        return await asyncio.to_thread(transfer.decode, raw)
    except transfer.TransferError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc


@router.post("/preview", response_model=ImportPreviewOut)
async def preview_import(_: ActiveUserDep, file: Annotated[UploadFile, File()]) -> dict:
    payload = await _read_upload(file)
    return transfer.describe(payload)


@router.post("/import", response_model=ImportResultOut)
async def run_import(
    session: SessionDep,
    _: ActiveUserDep,
    file: Annotated[UploadFile, File()],
    account_id: Annotated[int, Form()],
    merge: Annotated[bool, Form()] = False,
) -> dict:
    payload = await _read_upload(file)
    try:
        return await transfer.import_payload(session, payload, account_id, merge)
    except transfer.TransferError as exc:
        await session.rollback()
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
