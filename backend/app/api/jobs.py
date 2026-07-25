import os

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import case, func, select

from ..deps import ActiveUserDep, SessionDep
from ..models import Channel, FileEntry, JobRun, SyncJob, TelegramAccount
from ..rclone import client as rclone
from ..schemas import JobIn, JobOut, JobRunOut, JobStats, JobUpdate
from ..sync.scheduler import scheduler

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


async def _stats(session, job_id: int) -> JobStats:
    uploaded_flag = case((FileEntry.state == "uploaded", 1), else_=0)
    pending_flag = case((FileEntry.state.in_(("pending", "uploading", "stale")), 1), else_=0)
    error_flag = case((FileEntry.state == "error", 1), else_=0)
    uploaded_size = case((FileEntry.state == "uploaded", FileEntry.size), else_=0)

    row = await session.execute(
        select(
            func.count(FileEntry.id),
            func.coalesce(func.sum(FileEntry.size), 0),
            func.coalesce(func.sum(uploaded_flag), 0),
            func.coalesce(func.sum(uploaded_size), 0),
            func.coalesce(func.sum(pending_flag), 0),
            func.coalesce(func.sum(error_flag), 0),
        ).where(FileEntry.job_id == job_id)
    )
    total, bytes_total, uploaded, bytes_uploaded, pending, errors = row.one()
    return JobStats(
        files_total=total or 0,
        files_uploaded=uploaded or 0,
        files_pending=pending or 0,
        files_error=errors or 0,
        bytes_total=bytes_total or 0,
        bytes_uploaded=bytes_uploaded or 0,
    )


async def _to_out(session, job: SyncJob) -> JobOut:
    out = JobOut.model_validate(job)
    account = await session.get(TelegramAccount, job.account_id)
    channel = await session.get(Channel, job.channel_id)
    out.account_label = account.label if account else ""
    out.channel_title = channel.title if channel else ""
    out.channel_tg_id = channel.tg_id if channel else 0
    out.stats = await _stats(session, job.id)
    return out


@router.get("", response_model=list[JobOut])
async def list_jobs(session: SessionDep, _: ActiveUserDep) -> list[JobOut]:
    result = await session.execute(select(SyncJob).order_by(SyncJob.id))
    return [await _to_out(session, job) for job in result.scalars()]


@router.get("/{job_id}", response_model=JobOut)
async def get_job(job_id: int, session: SessionDep, _: ActiveUserDep) -> JobOut:
    job = await session.get(SyncJob, job_id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Job non trovato")
    return await _to_out(session, job)


async def _validate(
    session,
    account_id: int,
    channel_id: int,
    source_type: str,
    local_path: str,
    remote: str | None,
) -> Channel:
    account = await session.get(TelegramAccount, account_id)
    if account is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Account Telegram non trovato")

    channel = await session.get(Channel, channel_id)
    if channel is None or channel.account_id != account_id:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "Il canale non appartiene a questo account"
        )

    if source_type == "rclone":
        if not remote:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, "Indica il remote rclone da sincronizzare"
            )
        try:
            await rclone.check_remote(remote)
        except rclone.RcloneError as exc:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, f"Il remote {remote} non risponde: {exc}"
            ) from exc
    else:
        if not local_path:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, "Indica la cartella locale da sincronizzare"
            )
        if not os.path.isdir(local_path):
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"La cartella {local_path} non esiste dentro il container. "
                "Aggiungila come volume nel docker-compose.yml e riavvia il backend.",
            )
    return channel


@router.post("", response_model=JobOut, status_code=status.HTTP_201_CREATED)
async def create_job(payload: JobIn, session: SessionDep, _: ActiveUserDep) -> JobOut:
    await _validate(
        session,
        payload.account_id,
        payload.channel_id,
        payload.source_type,
        payload.local_path,
        payload.remote,
    )
    account = await session.get(TelegramAccount, payload.account_id)

    job = SyncJob(
        name=payload.name,
        account_id=payload.account_id,
        channel_id=payload.channel_id,
        source_type=payload.source_type,
        local_path=(payload.local_path.rstrip("/") or "/") if payload.source_type == "local" else "",
        remote=payload.remote.strip() if payload.remote else None,
        interval_hours=payload.interval_hours,
        scan_files_per_sec=payload.scan_files_per_sec,
        part_size_bytes=payload.part_size_bytes or account.default_part_size,
        enabled=payload.enabled,
    )
    session.add(job)
    await session.commit()
    await session.refresh(job)
    return await _to_out(session, job)


@router.patch("/{job_id}", response_model=JobOut)
async def update_job(
    job_id: int, payload: JobUpdate, session: SessionDep, _: ActiveUserDep
) -> JobOut:
    job = await session.get(SyncJob, job_id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Job non trovato")
    if job.status == "running":
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Il job e in esecuzione, fermalo prima di modificarlo"
        )

    data = payload.model_dump(exclude_unset=True)
    if {"channel_id", "local_path", "remote", "source_type"} & data.keys():
        await _validate(
            session,
            job.account_id,
            data.get("channel_id", job.channel_id),
            data.get("source_type", job.source_type),
            data.get("local_path", job.local_path),
            data.get("remote", job.remote),
        )

    for field, value in data.items():
        setattr(job, field, value)
    await session.commit()
    await session.refresh(job)
    return await _to_out(session, job)


@router.delete("/{job_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_job(job_id: int, session: SessionDep, _: ActiveUserDep) -> None:
    job = await session.get(SyncJob, job_id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Job non trovato")
    if scheduler.is_running(job_id):
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Il job e in esecuzione, fermalo prima di eliminarlo"
        )
    await session.delete(job)
    await session.commit()


@router.post("/{job_id}/run", response_model=JobOut)
async def run_job(job_id: int, session: SessionDep, _: ActiveUserDep) -> JobOut:
    job = await session.get(SyncJob, job_id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Job non trovato")
    if not await scheduler.trigger(job_id):
        raise HTTPException(status.HTTP_409_CONFLICT, "Il job e gia in esecuzione")
    await session.refresh(job)
    return await _to_out(session, job)


@router.post("/{job_id}/stop", response_model=JobOut)
async def stop_job(job_id: int, session: SessionDep, _: ActiveUserDep) -> JobOut:
    job = await session.get(SyncJob, job_id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Job non trovato")
    if not await scheduler.stop(job_id):
        raise HTTPException(status.HTTP_409_CONFLICT, "Il job non e in esecuzione")
    return await _to_out(session, job)


@router.get("/{job_id}/runs", response_model=list[JobRunOut])
async def job_runs(
    job_id: int, session: SessionDep, _: ActiveUserDep, limit: int = 20
) -> list[JobRun]:
    result = await session.execute(
        select(JobRun)
        .where(JobRun.job_id == job_id)
        .order_by(JobRun.started_at.desc())
        .limit(min(limit, 100))
    )
    return list(result.scalars())
