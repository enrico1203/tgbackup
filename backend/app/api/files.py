from fastapi import APIRouter, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from ..deps import ActiveUserDep, SessionDep
from ..models import FileEntry, SyncJob
from ..schemas import FileOut, FilePage, RestoreIn, RestoreOut
from ..sync.restore import restore_file

router = APIRouter(prefix="/api/files", tags=["files"])


@router.get("", response_model=FilePage)
async def list_files(
    session: SessionDep,
    _: ActiveUserDep,
    job_id: int | None = None,
    channel_id: int | None = None,
    state: str | None = None,
    search: str | None = None,
    offset: int = 0,
    limit: int = 100,
) -> FilePage:
    filters = []
    if job_id is not None:
        filters.append(FileEntry.job_id == job_id)
    if channel_id is not None:
        # Un canale puo essere la destinazione di piu job: si filtra passando per loro.
        filters.append(
            FileEntry.job_id.in_(
                select(SyncJob.id).where(SyncJob.channel_id == channel_id)
            )
        )
    if state:
        filters.append(FileEntry.state == state)
    if search:
        filters.append(FileEntry.rel_path.ilike(f"%{search}%"))

    total = await session.scalar(select(func.count(FileEntry.id)).where(*filters))
    result = await session.execute(
        select(FileEntry)
        .where(*filters)
        .options(selectinload(FileEntry.parts))
        .order_by(FileEntry.rel_path)
        .offset(offset)
        .limit(min(limit, 500))
    )
    return FilePage(items=[FileOut.model_validate(e) for e in result.scalars()], total=total or 0)


@router.get("/{file_id}", response_model=FileOut)
async def get_file(file_id: int, session: SessionDep, _: ActiveUserDep) -> FileEntry:
    result = await session.execute(
        select(FileEntry).where(FileEntry.id == file_id).options(selectinload(FileEntry.parts))
    )
    entry = result.scalar_one_or_none()
    if entry is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "File non trovato")
    return entry


@router.post("/restore", response_model=RestoreOut)
async def start_restore(payload: RestoreIn, _: ActiveUserDep) -> RestoreOut:
    try:
        restore_id = await restore_file(payload.file_id)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    from ..sync.progress import hub

    progress = hub.restores[restore_id]
    return RestoreOut(restore_id=restore_id, target_path=progress.target_path)
