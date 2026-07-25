from fastapi import APIRouter
from sqlalchemy import case, func, select

from ..deps import ActiveUserDep, SessionDep
from ..models import FileEntry, JobRun, SyncJob, TelegramAccount
from ..schemas import DashboardOut, JobRunOut
from ..telegram.manager import manager

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


@router.get("", response_model=DashboardOut)
async def dashboard(session: SessionDep, _: ActiveUserDep) -> DashboardOut:
    accounts = list((await session.execute(select(TelegramAccount))).scalars())
    jobs = list((await session.execute(select(SyncJob))).scalars())

    uploaded_flag = case((FileEntry.state == "uploaded", 1), else_=0)
    pending_flag = case((FileEntry.state.in_(("pending", "uploading", "stale")), 1), else_=0)
    error_flag = case((FileEntry.state == "error", 1), else_=0)
    uploaded_size = case((FileEntry.state == "uploaded", FileEntry.size), else_=0)

    row = (
        await session.execute(
            select(
                func.count(FileEntry.id),
                func.coalesce(func.sum(uploaded_flag), 0),
                func.coalesce(func.sum(pending_flag), 0),
                func.coalesce(func.sum(error_flag), 0),
                func.coalesce(func.sum(FileEntry.size), 0),
                func.coalesce(func.sum(uploaded_size), 0),
            )
        )
    ).one()

    runs = list(
        (
            await session.execute(
                select(JobRun).order_by(JobRun.started_at.desc()).limit(10)
            )
        ).scalars()
    )

    return DashboardOut(
        accounts=len(accounts),
        accounts_connected=sum(1 for a in accounts if manager.is_connected(a.id)),
        jobs=len(jobs),
        jobs_running=sum(1 for j in jobs if j.status == "running"),
        files_total=row[0],
        files_uploaded=row[1],
        files_pending=row[2],
        files_error=row[3],
        bytes_total=row[4],
        bytes_uploaded=row[5],
        recent_runs=[JobRunOut.model_validate(r) for r in runs],
    )
