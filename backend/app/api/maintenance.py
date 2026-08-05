from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from .. import maintenance
from ..deps import ActiveUserDep, SessionDep
from ..models import Channel, SyncJob
from ..schemas import ChannelOut, CheckIn, CheckScheduleIn, MaintenanceTaskOut, RebuildIn

router = APIRouter(prefix="/api/maintenance", tags=["maintenance"])


async def _channel_or_404(session, channel_id: int) -> Channel:
    channel = await session.get(Channel, channel_id)
    if channel is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Channel not found")
    return channel


async def _guard(session, channel_id: int, writes: bool) -> Channel:
    channel = await _channel_or_404(session, channel_id)

    if maintenance.registry.active_on(channel_id) is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Another maintenance task is already running on this channel",
        )

    if writes:
        # A job uploading to this channel is writing the very index that is about to be
        # rewritten. Reading is fine, changing it under the job is not.
        running = await maintenance.running_job_on(session, channel_id)
        if running:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                f"Job {running} is running on this channel, stop it first",
            )
    return channel


@router.get("/tasks", response_model=list[MaintenanceTaskOut])
async def list_tasks(_: ActiveUserDep, channel_id: int | None = None) -> list[dict]:
    tasks = [
        task.snapshot()
        for task in maintenance.registry.tasks.values()
        if channel_id is None or task.channel_id == channel_id
    ]
    return sorted(tasks, key=lambda item: item["started_at"], reverse=True)


@router.post("/check", response_model=MaintenanceTaskOut, status_code=status.HTTP_202_ACCEPTED)
async def check(payload: CheckIn, session: SessionDep, _: ActiveUserDep) -> dict:
    channel = await _guard(session, payload.channel_id, writes=payload.repair)

    def run(task):
        return maintenance.check_channel(task, channel.id, payload.repair)

    return maintenance.registry.start("check", channel, run).snapshot()


@router.post("/rebuild", response_model=MaintenanceTaskOut, status_code=status.HTTP_202_ACCEPTED)
async def rebuild(payload: RebuildIn, session: SessionDep, _: ActiveUserDep) -> dict:
    channel = await _guard(session, payload.channel_id, writes=True)

    if payload.mode == "merge":
        job = await session.get(SyncJob, payload.job_id) if payload.job_id else None
        if job is None or job.channel_id != channel.id:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, "Pick a job that writes to this channel"
            )

    def run(task):
        return maintenance.rebuild_index(
            task, channel.id, payload.mode, payload.job_id, payload.job_name.strip()
        )

    return maintenance.registry.start("rebuild", channel, run).snapshot()


@router.put("/channels/{channel_id}/schedule", response_model=ChannelOut)
async def set_check_schedule(
    channel_id: int, payload: CheckScheduleIn, session: SessionDep, _: ActiveUserDep
) -> Channel:
    """How often this channel checks itself, and whether it repairs what it finds.

    Repair marks the damaged files for re-upload, which means the next run of the job
    sends them again: it is the one setting here that spends bandwidth without being
    asked, and the form says so.
    """
    channel = await _channel_or_404(session, channel_id)
    channel.check_interval_days = payload.check_interval_days
    channel.check_hour = payload.check_hour
    channel.check_repair = payload.check_repair
    await session.commit()
    await session.refresh(channel)
    return channel


@router.get("/channels/{channel_id}/jobs", response_model=list[dict])
async def channel_jobs(channel_id: int, session: SessionDep, _: ActiveUserDep) -> list[dict]:
    """The sync jobs writing to a channel, to pick the one a rebuild merges into."""
    await _channel_or_404(session, channel_id)
    result = await session.execute(
        select(SyncJob.id, SyncJob.name).where(SyncJob.channel_id == channel_id)
    )
    return [{"id": job_id, "name": name} for job_id, name in result]
