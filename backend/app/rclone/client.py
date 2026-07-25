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
import shutil
from dataclasses import dataclass

from ..config import settings

log = logging.getLogger(__name__)

RCLONE = shutil.which("rclone") or "/usr/local/bin/rclone"

# Argomenti comuni: niente banner, niente controllo di aggiornamenti, log minimo.
BASE_ARGS = ["--config", str(settings.rclone_config_path), "--log-level", "ERROR"]


class RcloneError(Exception):
    """Errore da mostrare direttamente all'utente."""


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
        message = stderr.decode(errors="replace").strip() or "errore sconosciuto"
        raise RcloneError(message[:500])
    return stdout.decode(errors="replace")


async def version() -> str:
    output = await _run(["version"], timeout=20)
    return output.splitlines()[0] if output else "sconosciuta"


async def list_remotes() -> list[str]:
    if not config_exists():
        return []
    output = await _run(["listremotes"], timeout=30)
    return [line.strip() for line in output.splitlines() if line.strip()]


async def check_remote(remote: str) -> None:
    """Verifica che il remote risponda, senza elencarlo tutto."""
    await _run(["lsjson", "--max-depth", "1", _normalize(remote)], timeout=60)


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
    remote: str, on_progress=None, timeout: float = 3600.0
) -> list[RemoteFile]:
    """Elenca ricorsivamente i file di un remote.

    L'uscita di lsjson e un unico array JSON: si legge in streaming riga per riga
    per poter riportare l'avanzamento su remote con centinaia di migliaia di file,
    invece di restare muti fino alla fine.
    """
    target = _normalize(remote)
    process = await asyncio.create_subprocess_exec(
        RCLONE,
        *BASE_ARGS,
        "lsjson",
        "-R",
        "--files-only",
        "--no-mimetype",
        target,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        limit=8 * 1024 * 1024,
    )

    files: list[RemoteFile] = []
    total_bytes = 0
    buffer: list[str] = []

    async def read_stdout() -> None:
        nonlocal total_bytes
        assert process.stdout is not None
        while True:
            raw = await process.stdout.readline()
            if not raw:
                break
            line = raw.decode(errors="replace").strip().rstrip(",")
            if line in ("[", "]", ""):
                continue
            buffer.append(line)
            # lsjson stampa un oggetto per riga: quando la riga chiude un oggetto
            # completo lo si converte subito.
            if not line.endswith("}"):
                continue
            candidate = "".join(buffer)
            buffer.clear()
            try:
                item = json.loads(candidate)
            except json.JSONDecodeError:
                continue
            if item.get("IsDir"):
                continue
            size = item.get("Size", 0)
            if size < 0:
                continue
            files.append(
                RemoteFile(
                    path=item["Path"],
                    name=item.get("Name") or os.path.basename(item["Path"]),
                    size=size,
                    mtime_ns=_parse_mtime(item.get("ModTime", "")),
                )
            )
            total_bytes += size
            if on_progress is not None and len(files) % 250 == 0:
                on_progress(len(files), total_bytes, os.path.dirname(item["Path"]))

    try:
        await asyncio.wait_for(read_stdout(), timeout)
    except TimeoutError:
        process.kill()
        await process.wait()
        raise RcloneError(f"L'elenco di {remote} non e finito entro {timeout:.0f}s") from None

    stderr = b""
    if process.stderr is not None:
        stderr = await process.stderr.read()
    await process.wait()

    if process.returncode != 0:
        raise RcloneError(
            stderr.decode(errors="replace").strip()[:500] or f"lsjson di {remote} fallito"
        )

    if on_progress is not None:
        on_progress(len(files), total_bytes, "")
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
                stderr.decode(errors="replace").strip()[:500]
                or f"Lettura di {self._remote_file} interrotta a {self._read}/{self._length} byte"
            )
