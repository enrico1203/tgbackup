import asyncio
import os
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import case, delete, func, select, update

from ..channels import job_channel_ids
from ..deps import ActiveUserDep, SessionDep
from ..models import (
    READABLE_STATES,
    Bot,
    BotSet,
    Channel,
    FileEntry,
    JobRun,
    SyncJob,
    SyncJobChannel,
    TelegramAccount,
)
from ..rclone import client as rclone
from ..schemas import JobChannelOut, JobIn, JobOut, JobRunOut, JobStats, JobUpdate
from ..sync import window
from ..sync.scheduler import SYNC, scheduler
from ..telegram.bots import bots
from .accounts import carry_channel_check, channel_for_account

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


async def _stats(session, job_id: int) -> JobStats:
    uploaded_flag = case((FileEntry.state == "uploaded", 1), else_=0)
    pending_flag = case((FileEntry.state.in_(("pending", "uploading", "stale")), 1), else_=0)
    error_flag = case((FileEntry.state == "error", 1), else_=0)
    trashed_flag = case((FileEntry.state == "trashed", 1), else_=0)
    uploaded_size = case((FileEntry.state == "uploaded", FileEntry.size), else_=0)
    trashed_size = case((FileEntry.state == "trashed", FileEntry.size), else_=0)

    row = await session.execute(
        select(
            func.count(FileEntry.id),
            func.coalesce(func.sum(FileEntry.size), 0),
            func.coalesce(func.sum(uploaded_flag), 0),
            func.coalesce(func.sum(uploaded_size), 0),
            func.coalesce(func.sum(pending_flag), 0),
            func.coalesce(func.sum(error_flag), 0),
            func.coalesce(func.sum(trashed_flag), 0),
            func.coalesce(func.sum(trashed_size), 0),
        ).where(FileEntry.job_id == job_id)
    )
    (
        total,
        bytes_total,
        uploaded,
        bytes_uploaded,
        pending,
        errors,
        trashed,
        bytes_trashed,
    ) = row.one()
    return JobStats(
        files_total=total or 0,
        files_uploaded=uploaded or 0,
        files_pending=pending or 0,
        files_error=errors or 0,
        # Still in the channel, no longer at the source: they are what the trash is
        # holding and what a restore to an earlier day would find.
        files_trashed=trashed or 0,
        bytes_total=bytes_total or 0,
        bytes_uploaded=bytes_uploaded or 0,
        bytes_trashed=bytes_trashed or 0,
    )


async def _to_out(session, job: SyncJob, zone: ZoneInfo | None = None) -> JobOut:
    out = JobOut.model_validate(job)
    channel = await session.get(Channel, job.channel_id)
    if job.bot_set_id is not None:
        bot_set = await session.get(BotSet, job.bot_set_id)
        out.transport = "botset"
        out.account_label = bot_set.name if bot_set else ""
    else:
        account = (
            await session.get(TelegramAccount, job.account_id)
            if job.account_id is not None
            else None
        )
        out.transport = "account"
        out.account_label = account.label if account else ""
    out.channel_title = channel.title if channel else ""
    out.channel_tg_id = channel.tg_id if channel else 0
    out.stats = await _stats(session, job.id)
    out.channels = await _channels_out(session, job, out.stats)

    # The zone is read once per request when a whole list is being built, since it is one
    # value for the installation and the same for every row.
    if zone is None:
        zone = window.load_zone(await window.load_timezone(session))
    now = datetime.now(UTC)
    out.window_open = window.is_open(job.schedule_hours, zone, now)
    out.next_window_at = window.next_opening(job.schedule_hours, zone, now)
    return out


async def _channels_out(session, job: SyncJob, stats: JobStats) -> list[JobChannelOut]:
    """The channels of the job with how many of its files each one holds.

    The count per channel costs a grouped query over the files of the job, so it is only
    asked of a job that actually has more than one: on the others the totals already
    computed say the same thing.
    """
    ids = await job_channel_ids(session, job)
    rows = {
        row.id: row
        for row in (await session.execute(select(Channel).where(Channel.id.in_(ids)))).scalars()
    }
    if len(ids) > 1:
        counts = dict(
            (
                await session.execute(
                    select(FileEntry.channel_id, func.count(FileEntry.id))
                    .where(FileEntry.job_id == job.id, FileEntry.state.in_(READABLE_STATES))
                    .group_by(FileEntry.channel_id)
                )
            ).all()
        )
    else:
        counts = {job.channel_id: stats.files_uploaded + stats.files_trashed}
    return [
        JobChannelOut(
            id=channel_id,
            title=rows[channel_id].title,
            tg_id=rows[channel_id].tg_id,
            files=counts.get(channel_id, 0),
        )
        for channel_id in ids
        if channel_id in rows
    ]


@router.get("", response_model=list[JobOut])
async def list_jobs(session: SessionDep, _: ActiveUserDep) -> list[JobOut]:
    zone = window.load_zone(await window.load_timezone(session))
    result = await session.execute(select(SyncJob).order_by(SyncJob.id))
    return [await _to_out(session, job, zone) for job in result.scalars()]


@router.get("/{job_id}", response_model=JobOut)
async def get_job(job_id: int, session: SessionDep, _: ActiveUserDep) -> JobOut:
    job = await session.get(SyncJob, job_id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Job not found")
    return await _to_out(session, job)


async def _validate_transport(session, account_id: int | None, bot_set_id: int | None) -> None:
    """Exactly one carrier, and it has to exist.

    Enforced here and not in the schema, since a PATCH carries whichever of the two is
    changing and the other has to be read from the row before the pair can be judged.
    """
    if (account_id is None) == (bot_set_id is None):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "A job runs either on a Telegram account or on a bot set, not on both and "
            "not on neither",
        )


async def _validate(
    session,
    account_id: int | None,
    bot_set_id: int | None,
    channel_id: int,
    extra_channel_ids: list[int],
    source_type: str,
    local_path: str,
    remote: str | None,
) -> Channel:
    await _validate_transport(session, account_id, bot_set_id)

    channel = await session.get(Channel, channel_id)
    if channel is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Channel not found")

    # Every channel the job uploads into gets the same checks its own channel always had:
    # a file placed in a channel the carrier cannot reach is a file that fails, one run
    # after the other, with the other channels working fine and hiding it.
    channels = [channel]
    for extra_id in extra_channel_ids:
        extra = await session.get(Channel, extra_id)
        if extra is None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "One of the extra channels is gone")
        channels.append(extra)
    if len({item.tg_id for item in channels}) != len(channels):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "The same channel is listed more than once"
        )

    if bot_set_id is not None:
        bot_set = await session.get(BotSet, bot_set_id)
        if bot_set is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Bot set not found")
        result = await session.execute(
            select(Bot).where(Bot.bot_set_id == bot_set_id, Bot.enabled.is_(True))
        )
        members = list(result.scalars())
        if not members:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"The set {bot_set.name} has no enabled bot",
            )
        # Every bot has to be in the channel, and it is checked now rather than at the
        # first run: a bot that was never added is a job that uploads with one fewer
        # carrier and says nothing about why.
        missing: list[str] = []
        for item in channels:
            for bot in members:
                try:
                    await bots.peer(bot.id, item.tg_id)
                except Exception as exc:
                    label = bot.username or bot.id
                    prefix = f"{label} in {item.title}" if len(channels) > 1 else f"{label}"
                    missing.append(f"{prefix} ({exc})")
        if missing:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "These bots cannot reach the channel, add them as administrators with "
                "permission to post and to delete messages: " + ", ".join(missing),
            )
    else:
        account = await session.get(TelegramAccount, account_id)
        if account is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Telegram account not found")
        for item in channels:
            if item.account_id != account_id:
                raise HTTPException(
                    status.HTTP_400_BAD_REQUEST,
                    f"The channel {item.title} does not belong to this account",
                )

    if source_type == "rclone":
        if not remote:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, "Give the rclone remote to synchronise"
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
                status.HTTP_400_BAD_REQUEST, "Give the local folder to synchronise"
            )
        # In a thread: the folder can sit on a network mount, where a stat on a server
        # that has stopped answering would block the event loop for the whole timeout.
        if not await asyncio.to_thread(os.path.isdir, local_path):
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"Folder {local_path} does not exist inside the container. "
                "Add it as a volume in docker-compose.yml and restart the backend.",
            )
    return channel


async def _default_part_size(
    session, account_id: int | None, bot_set_id: int | None
) -> int:
    """The split threshold the carrier imposes.

    A bot is never Premium, so a job on a set is capped where a standard account is: 2 GB
    per file, which the 1.9 GB default stays under with room for the message around it.
    """
    if bot_set_id is not None:
        bot_set = await session.get(BotSet, bot_set_id)
        return bot_set.default_part_size if bot_set else 1_900_000_000
    account = await session.get(TelegramAccount, account_id)
    return account.default_part_size if account else 1_900_000_000


async def _relocate_files(session, job_id: int, channel_ids: list[int]) -> None:
    """Keeps every file of the job pointing at a channel the job still has.

    The channels change in two ways that look alike and are not. The job moves onto
    another account: the Telegram channels stay, only the rows do not, since rows are per
    account, and the files follow onto the new row with the same Telegram id. Or the user
    takes a channel away: the messages of the files in it are still there and nothing else
    would ever delete them, so that is refused while any file is left in it.

    A pending file has no message yet. Its channel is a placeholder the runner replaces,
    so it simply goes to the job's own channel.
    """
    rows = {
        row.id: row
        for row in (
            await session.execute(select(Channel).where(Channel.id.in_(channel_ids)))
        ).scalars()
    }
    by_tg = {rows[cid].tg_id: cid for cid in channel_ids if cid in rows}

    holding = await session.execute(
        select(FileEntry.channel_id, func.count(FileEntry.id))
        .where(FileEntry.job_id == job_id, FileEntry.state != "pending")
        .group_by(FileEntry.channel_id)
    )
    for current_id, count in holding.all():
        if current_id in rows:
            continue
        current = await session.get(Channel, current_id)
        target = by_tg.get(current.tg_id) if current is not None else None
        if target is None:
            title = current.title if current is not None else f"channel {current_id}"
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"{count} files of this job are in {title}, so it cannot be taken away "
                "from the job: that is where their messages are. Keep it among the "
                "channels of the job.",
            )
        await session.execute(
            update(FileEntry)
            .where(FileEntry.job_id == job_id, FileEntry.channel_id == current_id)
            .values(channel_id=target)
        )

    await session.execute(
        update(FileEntry)
        .where(
            FileEntry.job_id == job_id,
            FileEntry.state == "pending",
            FileEntry.channel_id.not_in(channel_ids),
        )
        .values(channel_id=channel_ids[0])
    )


@router.post("", response_model=JobOut, status_code=status.HTTP_201_CREATED)
async def create_job(payload: JobIn, session: SessionDep, _: ActiveUserDep) -> JobOut:
    extras = [cid for cid in dict.fromkeys(payload.extra_channel_ids) if cid != payload.channel_id]
    await _validate(
        session,
        payload.account_id,
        payload.bot_set_id,
        payload.channel_id,
        extras,
        payload.source_type,
        payload.local_path,
        payload.remote,
    )
    default_part_size = await _default_part_size(
        session, payload.account_id, payload.bot_set_id
    )

    local_path = ""
    if payload.source_type == "local":
        # The trailing slash is dropped so the same folder written two ways gives one
        # path, except for the root, which is only a slash.
        local_path = payload.local_path.rstrip("/") or "/"

    job = SyncJob(
        name=payload.name,
        account_id=payload.account_id,
        bot_set_id=payload.bot_set_id,
        parallel_files=payload.parallel_files,
        channel_id=payload.channel_id,
        source_type=payload.source_type,
        local_path=local_path,
        remote=payload.remote.strip() if payload.remote else None,
        interval_hours=payload.interval_hours,
        scan_files_per_sec=payload.scan_files_per_sec,
        part_size_bytes=min(payload.part_size_bytes or default_part_size, default_part_size)
        if payload.bot_set_id is not None
        else (payload.part_size_bytes or default_part_size),
        include_globs=payload.include_globs,
        exclude_globs=payload.exclude_globs,
        max_file_size=payload.max_file_size,
        schedule_hours=payload.schedule_hours,
        stop_outside_window=payload.stop_outside_window,
        throttle_bps=payload.throttle_bps,
        silence_alerts=payload.silence_alerts,
        delete_guard_percent=payload.delete_guard_percent,
        delete_guard_files=payload.delete_guard_files,
        trash_days=payload.trash_days,
        enabled=payload.enabled,
    )
    session.add(job)
    await session.flush()
    for extra_id in extras:
        session.add(SyncJobChannel(job_id=job.id, channel_id=extra_id))
    await session.commit()
    await session.refresh(job)
    return await _to_out(session, job)


@router.patch("/{job_id}", response_model=JobOut)
async def update_job(
    job_id: int, payload: JobUpdate, session: SessionDep, _: ActiveUserDep
) -> JobOut:
    job = await session.get(SyncJob, job_id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Job not found")
    if job.status == "running":
        raise HTTPException(
            status.HTTP_409_CONFLICT, "The job is running, stop it before editing it"
        )

    data = payload.model_dump(exclude_unset=True)
    extras: list[int] | None = data.pop("extra_channel_ids", None)
    current_extras = (await job_channel_ids(session, job))[1:]
    # A null on either of the two is how the browser says "not this one", and it is
    # dropped: what names the new carrier is the field that carries a value. Naming one
    # clears the other, since a job runs on exactly one.
    if data.get("account_id") is None:
        data.pop("account_id", None)
    if data.get("bot_set_id") is None:
        data.pop("bot_set_id", None)
    if "bot_set_id" in data:
        data["account_id"] = None
    elif "account_id" in data:
        data["bot_set_id"] = None

    account_id = data.get("account_id", job.account_id)
    bot_set_id = data.get("bot_set_id", job.bot_set_id)

    if account_id is not None and account_id != job.account_id:
        # The account changes, the channel does not: the job is moved onto the row the new
        # account holds for the same Telegram channel, since the access_hash it carries is
        # issued per user. The index is left exactly as it is, because the message ids in it
        # belong to the channel and any account that is in it can read and delete them.
        current = await session.get(Channel, job.channel_id)
        if current is None:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, "The job points at a channel that is gone"
            )
        target = await channel_for_account(session, account_id, current.tg_id)
        # Unless the request names one itself, which is the user changing both at once.
        data.setdefault("channel_id", target.id)
        if extras is None:
            # The extra channels travel the same way, each onto the new account's row.
            extras = []
            for extra_id in current_extras:
                extra = await session.get(Channel, extra_id)
                if extra is not None:
                    extras.append(
                        (await channel_for_account(session, account_id, extra.tg_id)).id
                    )

    primary = data.get("channel_id", job.channel_id)
    if extras is None:
        extras = current_extras
    extras = [cid for cid in dict.fromkeys(extras) if cid != primary]
    channels_changed = primary != job.channel_id or extras != current_extras

    keys = {"account_id", "bot_set_id", "channel_id", "local_path", "remote", "source_type"}
    if keys & data.keys() or channels_changed:
        await _validate(
            session,
            account_id,
            bot_set_id,
            primary,
            extras,
            data.get("source_type", job.source_type),
            data.get("local_path", job.local_path),
            data.get("remote", job.remote),
        )
        if bot_set_id is not None and job.bot_set_id is None:
            # Onto a bot set from an account that may have been Premium: a part above what
            # a bot can send would fail on every file large enough to be split.
            ceiling = await _default_part_size(session, None, bot_set_id)
            if job.part_size_bytes > ceiling and "part_size_bytes" not in data:
                data["part_size_bytes"] = ceiling

    if channels_changed:
        await _relocate_files(session, job.id, [primary, *extras])
        await session.execute(delete(SyncJobChannel).where(SyncJobChannel.job_id == job.id))
        for extra_id in extras:
            session.add(SyncJobChannel(job_id=job.id, channel_id=extra_id))

    left_behind = job.channel_id

    for field, value in data.items():
        setattr(job, field, value)

    if job.channel_id != left_behind:
        # The scheduled check lives on the channel row and works through the jobs writing
        # to it, so it follows the job when the row it was set on is left with none.
        await session.flush()
        await carry_channel_check(session, left_behind, job.channel_id)

    await session.commit()
    await session.refresh(job)
    return await _to_out(session, job)


@router.delete("/{job_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_job(job_id: int, session: SessionDep, _: ActiveUserDep) -> None:
    job = await session.get(SyncJob, job_id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Job not found")
    if scheduler.is_running(SYNC, job_id):
        raise HTTPException(
            status.HTTP_409_CONFLICT, "The job is running, stop it before deleting it"
        )
    await session.delete(job)
    await session.commit()


@router.post("/{job_id}/run", response_model=JobOut)
async def run_job(job_id: int, session: SessionDep, _: ActiveUserDep) -> JobOut:
    job = await session.get(SyncJob, job_id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Job not found")
    if not await scheduler.trigger(SYNC, job_id):
        raise HTTPException(status.HTTP_409_CONFLICT, "The job is already running")
    await session.refresh(job)
    return await _to_out(session, job)


@router.post("/{job_id}/allow-deletions", response_model=JobOut)
async def allow_deletions(job_id: int, session: SessionDep, _: ActiveUserDep) -> JobOut:
    """Lets the next run of this job go through with the deletions the guard stopped.

    Deliberately not part of the ordinary update: this is an acknowledgement of something
    the user has read, it lasts one run, and the run itself clears it. Editing the limits
    is the other answer, and that one goes through PATCH like everything else.
    """
    job = await session.get(SyncJob, job_id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Job not found")
    job.delete_guard_bypass = True
    await session.commit()
    await session.refresh(job)
    return await _to_out(session, job)


@router.post("/{job_id}/stop", response_model=JobOut)
async def stop_job(job_id: int, session: SessionDep, _: ActiveUserDep) -> JobOut:
    job = await session.get(SyncJob, job_id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Job not found")
    if not await scheduler.stop(SYNC, job_id):
        raise HTTPException(status.HTTP_409_CONFLICT, "The job is not running")
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
