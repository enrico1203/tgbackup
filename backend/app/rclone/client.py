"""Accesso ai remote rclone senza mount.

Due comandi coprono tutto:

  rclone lsjson -R --files-only   elenca un remote intero via API
  rclone cat --offset N --count M legge un intervallo esatto di byte

Elencare via API e molto piu veloce che camminare un mount FUSE, e leggere a
intervalli evita di copiare i file su disco prima di caricarli: le fette vanno
direttamente dallo stream all'uploader.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
from dataclasses import dataclass

from ..config import settings

log = logging.getLogger(__name__)

RCLONE = shutil.which("rclone") or "/usr/local/bin/rclone"

# Argomenti comuni: niente banner, niente controllo di aggiornamenti, log minimo.
BASE_ARGS = ["--config", str(settings.rclone_config_path), "--log-level", "ERROR"]


class RcloneError(Exception):
    """Errore da mostrare direttamente all'utente."""


def _clean_error(raw: bytes | str) -> str:
    """Ripulisce lo stderr di rclone per mostrarlo in interfaccia.

    Le righe arrivano come "2026/07/25 22:10:43 ERROR : messaggio": data, ora e
    livello non dicono nulla all'utente e nascondono il messaggio vero.
    """
    text = raw.decode(errors="replace") if isinstance(raw, bytes) else raw
    lines = []
    for line in text.strip().splitlines():
        cleaned = re.sub(r"^\d{4}/\d{2}/\d{2} \d{2}:\d{2}:\d{2}\s+", "", line.strip())
        cleaned = re.sub(r"^(CRITICAL|ERROR|NOTICE|WARNING|INFO|DEBUG)\s*:\s*", "", cleaned)
        cleaned = re.sub(r"^Failed to \w+ .*?: ", "", cleaned)
        if cleaned and cleaned not in lines:
            lines.append(cleaned)
    return " | ".join(lines)[:400] or "errore sconosciuto"


@dataclass(slots=True)
class RemoteFile:
    path: str
    name: str
    size: int
    mtime_ns: int


def config_exists() -> bool:
    return settings.rclone_config_path.exists()


def write_config(content: str) -> None:
    """Scrive la configurazione su disco con permessi ristretti.

    Il file contiene le credenziali dei cloud: nel database sta cifrato, qui su
    disco resta leggibile solo da root dentro il container.
    """
    path = settings.rclone_config_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    os.chmod(path, 0o600)


def remove_config() -> None:
    settings.rclone_config_path.unlink(missing_ok=True)


async def _run(args: list[str], timeout: float = 120.0) -> str:
    process = await asyncio.create_subprocess_exec(
        RCLONE,
        *BASE_ARGS,
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout)
    except TimeoutError:
        process.kill()
        await process.wait()
        raise RcloneError(f"rclone non ha risposto entro {timeout:.0f} secondi") from None

    if process.returncode != 0:
        raise RcloneError(_clean_error(stderr))
    return stdout.decode(errors="replace")


async def version() -> str:
    output = await _run(["version"], timeout=20)
    return output.splitlines()[0] if output else "sconosciuta"


async def list_remotes() -> list[str]:
    if not config_exists():
        return []
    output = await _run(["listremotes"], timeout=30)
    return [line.strip() for line in output.splitlines() if line.strip()]


async def _stream_lsjson(
    target: str,
    extra_args: list[str],
    timeout: float,
    max_items: int | None = None,
    on_item=None,
) -> list[dict]:
    """Legge l'uscita di lsjson man mano che arriva, fermandosi quando basta.

    lsjson stampa un oggetto per riga, quindi non serve attendere la fine per avere
    i primi risultati. Con `max_items` il processo viene chiuso appena raggiunto il
    numero richiesto: cosi elencare le prime voci di una cartella con decine di
    migliaia di file costa quanto elencarne una piccola, invece di dover attendere
    la decifratura di tutti i nomi.
    """
    process = await asyncio.create_subprocess_exec(
        RCLONE,
        *BASE_ARGS,
        "lsjson",
        *extra_args,
        target,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        limit=8 * 1024 * 1024,
    )

    items: list[dict] = []
    buffer: list[str] = []
    stopped_early = False
    seen = 0
    # Chi raccoglie da se con on_item non ha bisogno che si tenga anche una copia:
    # su un remote da centinaia di migliaia di file sarebbe memoria doppia per nulla.
    keep = on_item is None or max_items is not None

    async def pump() -> None:
        nonlocal stopped_early, seen
        assert process.stdout is not None
        while True:
            raw = await process.stdout.readline()
            if not raw:
                return
            line = raw.decode(errors="replace").strip().rstrip(",")
            if line in ("[", "]", ""):
                continue
            buffer.append(line)
            if not line.endswith("}"):
                continue
            candidate = "".join(buffer)
            buffer.clear()
            try:
                item = json.loads(candidate)
            except json.JSONDecodeError:
                continue
            seen += 1
            if keep:
                items.append(item)
            if on_item is not None:
                on_item(item, seen)
            if max_items is not None and seen >= max_items:
                stopped_early = True
                return

    try:
        await asyncio.wait_for(pump(), timeout)
    except TimeoutError:
        process.kill()
        await process.wait()
        raise RcloneError(
            f"rclone non ha risposto entro {timeout:.0f} secondi per {target}"
        ) from None

    if stopped_early:
        # Chiusura voluta: l'uscita non nulla che ne deriva non e un errore.
        if process.returncode is None:
            process.kill()
        await process.wait()
        return items

    stderr = b""
    if process.stderr is not None:
        stderr = await process.stderr.read()
    await process.wait()
    if process.returncode != 0:
        raise RcloneError(
            _clean_error(stderr) if stderr else f"lsjson di {target} fallito"
        )
    return items


async def check_remote(remote: str) -> None:
    """Verifica che il remote risponda.

    Si ferma alla prima voce ricevuta: il costo non dipende da quante ne contiene la
    cartella. Una cartella vuota fa uscire rclone con successo e va bene lo stesso.
    """
    await _stream_lsjson(
        _normalize(remote),
        ["--max-depth", "1", "--no-mimetype"],
        timeout=settings.rclone_check_timeout,
        max_items=1,
    )


@dataclass(slots=True)
class RemoteEntry:
    name: str
    path: str
    size: int
    is_dir: bool
    mtime: str


async def preview(remote: str, limit: int = 20) -> list[RemoteEntry]:
    """Primo livello di un remote, per farsi un'idea del contenuto.

    Profondita uno e lettura in streaming che si ferma alle prime `limit` voci: su
    una cartella con decine di migliaia di file un elenco completo richiederebbe
    minuti, qui il costo non dipende da quanto e piena.
    """
    items = await _stream_lsjson(
        _normalize(remote),
        ["--max-depth", "1", "--no-mimetype"],
        timeout=settings.rclone_preview_timeout,
        max_items=limit,
    )

    # Ordinamento sulle voci ricevute, cartelle prima e poi file: e come le
    # mostrerebbe un gestore di file. Non e un ordinamento globale della cartella,
    # perche ci si ferma prima di averla letta tutta.
    items.sort(key=lambda i: (not i.get("IsDir"), i.get("Name", "").lower()))

    return [
        RemoteEntry(
            name=item.get("Name", ""),
            path=item.get("Path", ""),
            size=max(0, item.get("Size", 0)),
            is_dir=bool(item.get("IsDir")),
            mtime=item.get("ModTime", "") or "",
        )
        for item in items[:limit]
    ]


def _normalize(remote: str) -> str:
    """Accetta sia 'nome:' che 'nome:sottocartella'."""
    remote = remote.strip()
    if ":" not in remote:
        raise RcloneError(
            f"'{remote}' non e un percorso rclone valido: manca i due punti, "
            "ad esempio jottamio-crypt: oppure jottamio-crypt:Film"
        )
    return remote


def _parse_mtime(value: str) -> int:
    """ModTime ISO 8601 in nanosecondi. Se manca vale zero."""
    if not value:
        return 0
    try:
        from datetime import datetime

        return int(datetime.fromisoformat(value).timestamp() * 1_000_000_000)
    except ValueError:
        return 0


async def list_files(
    remote: str, on_progress=None, timeout: float | None = None
) -> list[RemoteFile]:
    """Elenca ricorsivamente i file di un remote, riportando l'avanzamento.

    Su remote con centinaia di migliaia di file l'elenco dura minuti: leggendo in
    streaming si puo mostrare quanti file sono gia stati trovati invece di restare
    muti fino alla fine.
    """
    files: list[RemoteFile] = []
    total = {"bytes": 0}

    def collect(item: dict, _count: int) -> None:
        if item.get("IsDir"):
            return
        size = item.get("Size", 0)
        if size < 0:
            return
        files.append(
            RemoteFile(
                path=item["Path"],
                name=item.get("Name") or os.path.basename(item["Path"]),
                size=size,
                mtime_ns=_parse_mtime(item.get("ModTime", "")),
            )
        )
        total["bytes"] += size
        if on_progress is not None and len(files) % 250 == 0:
            on_progress(len(files), total["bytes"], os.path.dirname(item["Path"]))

    await _stream_lsjson(
        _normalize(remote),
        ["-R", "--files-only", "--no-mimetype"],
        timeout=timeout if timeout is not None else settings.rclone_list_timeout,
        on_item=collect,
    )

    if on_progress is not None:
        on_progress(len(files), total["bytes"], "")
    return files


class RemoteSliceReader:
    """Lettore sequenziale di un intervallo di byte da un remote.

    Non usa mount e non scrive nulla su disco: rclone apre una richiesta con
    intervallo e i byte passano direttamente all'uploader.
    """

    def __init__(self, remote_file: str, offset: int, length: int) -> None:
        self._remote_file = remote_file
        self._offset = offset
        self._length = length
        self._process: asyncio.subprocess.Process | None = None
        self._read = 0

    async def __aenter__(self) -> RemoteSliceReader:
        self._process = await asyncio.create_subprocess_exec(
            RCLONE,
            *BASE_ARGS,
            "cat",
            "--offset",
            str(self._offset),
            "--count",
            str(self._length),
            "--buffer-size",
            "64M",
            self._remote_file,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            limit=8 * 1024 * 1024,
        )
        return self

    async def read(self, size: int) -> bytes:
        """Legge `size` byte, o meno solo alla fine del flusso.

        StreamReader.read restituisce quello che ha in quel momento, che su una
        pipe di rete e quasi sempre meno del richiesto: senza il ciclo si
        manderebbero a Telegram parti piu corte del dovuto, e tutte le parti
        tranne l'ultima devono essere piene.
        """
        assert self._process is not None and self._process.stdout is not None
        chunks: list[bytes] = []
        remaining = size
        while remaining > 0:
            chunk = await self._process.stdout.read(remaining)
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        data = b"".join(chunks)
        self._read += len(data)
        return data

    async def __aexit__(self, exc_type, exc, tb) -> None:
        process = self._process
        if process is None:
            return
        if process.returncode is None:
            try:
                process.kill()
            except ProcessLookupError:
                pass
        stderr = b""
        if process.stderr is not None:
            try:
                stderr = await process.stderr.read()
            except Exception:
                pass
        await process.wait()

        # Un'uscita non nulla conta solo se non e stata causata dalla chiusura
        # anticipata dopo aver letto tutto quello che serviva.
        if exc_type is None and self._read < self._length and process.returncode not in (0, None):
            raise RcloneError(
                _clean_error(stderr)
                if stderr
                else f"Lettura di {self._remote_file} interrotta a {self._read}/{self._length} byte"
            )
