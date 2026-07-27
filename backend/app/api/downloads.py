import asyncio
import os

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import func, select

from ..deps import ActiveUserDep, SessionDep
from ..models import Channel, DownloadJob, DownloadRun, FileEntry, SyncJob, TelegramAccount
from ..rclone import client as rclone
from ..schemas import (
    DownloadJobIn,
    DownloadJobOut,
    DownloadJobUpdate,
    DownloadRunOut,
    DownloadStats,
)
from ..sync.scheduler import DOWNLOAD, scheduler

router = APIRouter(prefix="/api/downloads", tags=["downloads"])


async def _stats(session, job: DownloadJob) -> DownloadStats:
    """What the channel holds, and how much of it reached the destination.

    The index side is read live. The destination side comes from the last finished run:
    counting it again here would mean walking a folder or listing a remote on every call
    to the jobs list, which is the work of a run, not of a page refresh.
    """
    row = await session.execute(
        select(
            func.count(FileEntry.id),
            func.coalesce(func.sum(FileEntry.size), 0),
        ).where(
            FileEntry.job_id.in_(
                select(SyncJob.id).where(SyncJob.channel_id == job.channel_id)
            ),
            FileEntry.state == "uploaded",
        )
    )
    files_indexed, bytes_indexed = row.one()

    last = await session.scalar(
        select(DownloadRun)
        .where(DownloadRun.job_id == job.id, DownloadRun.status.in_(("ok", "error")))
        .order_by(DownloadRun.started_at.desc())
        .limit(1)
    )

    return DownloadStats(
        files_indexed=files_indexed or 0,
        bytes_indexed=bytes_indexed or 0,
        files_at_destination=(last.present_files + last.downloaded_files) if last else 0,
        bytes_at_destination=(last.present_bytes + last.downloaded_bytes) if last else 0,
        files_failed=last.failed_files if last else 0,
        last_run_at=last.started_at if last else None,
    )


async def _to_out(session, job: DownloadJob) -> DownloadJobOut:
    out = DownloadJobOut.model_validate(job)
    account = await session.get(TelegramAccount, job.account_id)
    channel = await session.get(Channel, job.channel_id)
    out.account_label = account.label if account else ""
    out.channel_title = channel.title if channel else ""
    out.channel_tg_id = channel.tg_id if channel else 0
    out.stats = await _stats(session, job)
    return out


@router.get("", response_model=list[DownloadJobOut])
async def list_downloads(session: SessionDep, _: ActiveUserDep) -> list[DownloadJobOut]:
    result = await session.execute(select(DownloadJob).order_by(DownloadJob.id))
    return [await _to_out(session, job) for job in result.scalars()]


@router.get("/{job_id}", response_model=DownloadJobOut)
async def get_download(job_id: int, session: SessionDep, _: ActiveUserDep) -> DownloadJobOut:
    job = await session.get(DownloadJob, job_id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Download job not found")
    return await _to_out(session, job)


async def _validate(
    session,
    account_id: int,
    channel_id: int,
    dest_type: str,
    local_path: str,
    remote: str | None,
) -> None:
    account = await session.get(TelegramAccount, account_id)
    if account is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Telegram account not found")

    channel = await session.get(Channel, channel_id)
    if channel is None or channel.account_id != account_id:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "The channel does not belong to this account"
        )

    if dest_type == "rclone":
        if not remote:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, "Give the rclone remote to write to"
            )
        try:
            await rclone.check_remote(remote)
        except rclone.RcloneError as exc:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, f"Remote {remote} does not answer: {exc}"
            ) from exc
    else:
        if not local_path:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, "Give the local folder to write to"
            )
        # In a thread: the folder can sit on a network mount, where a stat on a server
        # that has stopped answering would block the event loop for the whole timeout.
        if not await asyncio.to_thread(os.path.isdir, local_path):
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"Folder {local_path} does not exist inside the container. "
                "Add it as a writable volume in docker-compose.yml and restart the backend.",
            )
        # Checked here rather than at three in the morning by the job: the folders to back
        # up are mounted read-only on purpose, and a destination among them would fail on
        # the very first file.
        if not await asyncio.to_thread(os.access, local_path, os.W_OK):
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"Folder {local_path} is mounted read-only. A download destination needs "
                "its own volume in docker-compose.yml, without the :ro suffix.",
            )


@router.post("", response_model=DownloadJobOut, status_code=status.HTTP_201_CREATED)
async def create_download(
    payload: DownloadJobIn, session: SessionDep, _: ActiveUserDep
) -> DownloadJobOut:
    await _validate(
        session,
        payload.account_id,
        payload.channel_id,
        payload.dest_type,
        payload.local_path,
        payload.remote,
    )

    local_path = ""
    if payload.dest_type == "local":
        local_path = payload.local_path.rstrip("/") or "/"

    job = DownloadJob(
        name=payload.name,
        account_id=payload.account_id,
        channel_id=payload.channel_id,
        dest_type=payload.dest_type,
        local_path=local_path,
        remote=payload.remote.strip() if payload.remote else None,
        interval_hours=payload.interval_hours,
        enabled=payload.enabled,
    )
    session.add(job)
    await session.commit()
    await session.refresh(job)
    return await _to_out(session, job)


@router.patch("/{job_id}", response_model=DownloadJobOut)
async def update_download(
    job_id: int, payload: DownloadJobUpdate, session: SessionDep, _: ActiveUserDep
) -> DownloadJobOut:
    job = await session.get(DownloadJob, job_id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Download job not found")
    if job.status == "running":
        raise HTTPException(
            status.HTTP_409_CONFLICT, "The job is running, stop it before editing it"
        )

    data = payload.model_dump(exclude_unset=True)
    if {"channel_id", "local_path", "remote", "dest_type"} & data.keys():
        await _validate(
            session,
            job.account_id,
            data.get("channel_id", job.channel_id),
            data.get("dest_type", job.dest_type),
            data.get("local_path", job.local_path),
            data.get("remote", job.remote),
        )

    for field, value in data.items():
        setattr(job, field, value)
    await session.commit()
    await session.refresh(job)
    return await _to_out(session, job)


@router.delete("/{job_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_download(job_id: int, session: SessionDep, _: ActiveUserDep) -> None:
    job = await session.get(DownloadJob, job_id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Download job not found")
    if scheduler.is_running(DOWNLOAD, job_id):
        raise HTTPException(
            status.HTTP_409_CONFLICT, "The job is running, stop it before deleting it"
        )
    await session.delete(job)
    await session.commit()


@router.post("/{job_id}/run", response_model=DownloadJobOut)
async def run_download(job_id: int, session: SessionDep, _: ActiveUserDep) -> DownloadJobOut:
    job = await session.get(DownloadJob, job_id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Download job not found")
    if not await scheduler.trigger(DOWNLOAD, job_id):
        raise HTTPException(status.HTTP_409_CONFLICT, "The job is already running")
    await session.refresh(job)
    return await _to_out(session, job)


@router.post("/{job_id}/stop", response_model=DownloadJobOut)
async def stop_download(job_id: int, session: SessionDep, _: ActiveUserDep) -> DownloadJobOut:
    job = await session.get(DownloadJob, job_id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Download job not found")
    if not await scheduler.stop(DOWNLOAD, job_id):
        raise HTTPException(status.HTTP_409_CONFLICT, "The job is not running")
    return await _to_out(session, job)


@router.get("/{job_id}/runs", response_model=list[DownloadRunOut])
async def download_runs(
    job_id: int, session: SessionDep, _: ActiveUserDep, limit: int = 20
) -> list[DownloadRun]:
    result = await session.execute(
        select(DownloadRun)
        .where(DownloadRun.job_id == job_id)
        .order_by(DownloadRun.started_at.desc())
        .limit(min(limit, 100))
    )
    return list(result.scalars())
