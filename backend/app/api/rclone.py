from dataclasses import asdict

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from ..deps import ActiveUserDep, SessionDep
from ..models import Setting, utcnow
from ..rclone import client as rclone
from ..schemas import (
    RcloneConfigIn,
    RcloneContentOut,
    RcloneStatusOut,
    RemoteCheckOut,
    RemoteEntryOut,
    RemotePreviewOut,
)
from ..security import decrypt, encrypt

router = APIRouter(prefix="/api/rclone", tags=["rclone"])

SETTING_KEY = "rclone_config"


async def load_config(session) -> str | None:
    row = await session.scalar(select(Setting).where(Setting.key == SETTING_KEY))
    if row is None:
        return None
    return decrypt(row.value) if row.encrypted else row.value


async def sync_config_to_disk(session) -> None:
    """Riporta su disco la configurazione salvata nel database.

    Il database e la fonte di verita: il file su disco esiste solo perche rclone
    vuole un file, e viene riscritto a ogni avvio.
    """
    content = await load_config(session)
    if content:
        rclone.write_config(content)
    else:
        rclone.remove_config()


@router.get("", response_model=RcloneStatusOut)
async def status_(session: SessionDep, _: ActiveUserDep) -> RcloneStatusOut:
    content = await load_config(session)
    row = await session.scalar(select(Setting).where(Setting.key == SETTING_KEY))

    try:
        version = await rclone.version()
    except Exception as exc:
        version = f"non disponibile: {exc}"

    remotes: list[str] = []
    error: str | None = None
    if content:
        try:
            remotes = await rclone.list_remotes()
        except rclone.RcloneError as exc:
            error = str(exc)

    return RcloneStatusOut(
        configured=bool(content),
        version=version,
        remotes=remotes,
        # La configurazione contiene le credenziali dei cloud: non torna mai al
        # browser, si mostrano solo i nomi delle sezioni e le dimensioni.
        config_lines=len(content.splitlines()) if content else 0,
        updated_at=row.updated_at if row else None,
        error=error,
    )


@router.get("/content", response_model=RcloneContentOut)
async def read_config(session: SessionDep, _: ActiveUserDep) -> RcloneContentOut:
    """Restituisce la configurazione in chiaro, per poterla modificare.

    Deliberatamente separato da GET /api/rclone: lo stato viene letto a ogni
    caricamento della pagina, mentre le credenziali escono solo quando l'utente
    chiede esplicitamente di modificarle.
    """
    content = await load_config(session)
    if content is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Nessuna configurazione salvata")
    return RcloneContentOut(content=content)


@router.put("", response_model=RcloneStatusOut)
async def save_config(
    payload: RcloneConfigIn, session: SessionDep, user: ActiveUserDep
) -> RcloneStatusOut:
    # Si valida sul testo ripulito ma si salva quello originale: cosi rileggere per
    # modificare restituisce esattamente il file che l'utente aveva scritto.
    content = payload.content
    stripped = content.strip()
    if not stripped:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "La configurazione e vuota")
    if "[" not in stripped:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Non sembra un file rclone.conf: manca una sezione fra parentesi quadre",
        )

    row = await session.scalar(select(Setting).where(Setting.key == SETTING_KEY))
    if row is None:
        row = Setting(key=SETTING_KEY, encrypted=True, value=encrypt(content))
        session.add(row)
    else:
        row.value = encrypt(content)
        row.encrypted = True
        row.updated_at = utcnow()
    await session.commit()

    rclone.write_config(content)

    try:
        remotes = await rclone.list_remotes()
    except rclone.RcloneError as exc:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, f"rclone rifiuta questa configurazione: {exc}"
        ) from exc

    return await status_(session, user)


@router.delete("", status_code=status.HTTP_204_NO_CONTENT)
async def delete_config(session: SessionDep, _: ActiveUserDep) -> None:
    row = await session.scalar(select(Setting).where(Setting.key == SETTING_KEY))
    if row is not None:
        await session.delete(row)
        await session.commit()
    rclone.remove_config()


@router.get("/check", response_model=RemoteCheckOut)
async def check_remote(remote: str, _: ActiveUserDep) -> RemoteCheckOut:
    try:
        await rclone.check_remote(remote)
    except rclone.RcloneError as exc:
        return RemoteCheckOut(ok=False, error=str(exc))
    return RemoteCheckOut(ok=True, error=None)


@router.get("/preview", response_model=RemotePreviewOut)
async def preview_remote(
    remote: str, _: ActiveUserDep, limit: int = 20
) -> RemotePreviewOut:
    limit = max(1, min(limit, 200))
    try:
        # Si chiede una voce in piu del necessario per sapere se ce n'erano altre.
        entries = await rclone.preview(remote, limit + 1)
    except rclone.RcloneError as exc:
        return RemotePreviewOut(remote=remote, entries=[], truncated=False, error=str(exc))

    return RemotePreviewOut(
        remote=remote,
        # Le dataclass con slots non hanno __dict__, quindi niente vars().
        entries=[RemoteEntryOut(**asdict(e)) for e in entries[:limit]],
        truncated=len(entries) > limit,
    )
