"""Astrazione della sorgente di un sync job.

Una cartella montata nel container e un remote rclone si comportano allo stesso modo
dal punto di vista del job: entrambi sanno elencare i propri file con percorso,
dimensione e mtime, e sanno restituire un lettore per un intervallo di byte.

Il resto del runner non deve sapere quale delle due sta usando.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from ..rclone import client as rclone
from ..telegram.fast_transfer import LocalSliceReader
from .scanner import ScannedFile, scan


@dataclass(slots=True)
class SourceFile:
    rel_path: str
    name: str
    size: int
    mtime_ns: int


class LocalSource:
    kind = "local"

    def __init__(self, root: str, files_per_sec: int = 0) -> None:
        self.root = root
        self.files_per_sec = files_per_sec

    @property
    def label(self) -> str:
        return self.root

    async def list_files(self, on_progress=None) -> list[SourceFile]:
        def report(files: int, dirs: int, total_bytes: int, where: str) -> None:
            if on_progress is not None:
                on_progress(files, dirs, total_bytes, where)

        found: list[ScannedFile] = await scan(self.root, self.files_per_sec, on_progress=report)
        return [
            SourceFile(item.rel_path, item.name, item.size, item.mtime_ns) for item in found
        ]

    def reader(self, rel_path: str, offset: int, length: int):
        return LocalSliceReader(os.path.join(self.root, rel_path), offset, length)


class RcloneSource:
    kind = "rclone"

    def __init__(self, remote: str) -> None:
        # Normalizzato una volta sola: "nome:" oppure "nome:sottocartella".
        self.remote = remote.strip()

    @property
    def label(self) -> str:
        return self.remote

    async def list_files(self, on_progress=None) -> list[SourceFile]:
        def report(files: int, total_bytes: int, where: str) -> None:
            if on_progress is not None:
                # Un remote non riporta un conteggio di cartelle: resta a zero.
                on_progress(files, 0, total_bytes, where)

        found = await rclone.list_files(self.remote, on_progress=report)
        return [SourceFile(item.path, item.name, item.size, item.mtime_ns) for item in found]

    def _full_path(self, rel_path: str) -> str:
        base = self.remote
        if base.endswith(":") or base.endswith("/"):
            return f"{base}{rel_path}"
        return f"{base}/{rel_path}"

    def reader(self, rel_path: str, offset: int, length: int):
        return rclone.RemoteSliceReader(self._full_path(rel_path), offset, length)


def build_source(job) -> LocalSource | RcloneSource:
    if job.source_type == "rclone":
        if not job.remote:
            raise ValueError("Il job e di tipo rclone ma non ha un remote configurato")
        return RcloneSource(job.remote)
    return LocalSource(job.local_path, job.scan_files_per_sec)
