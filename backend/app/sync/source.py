"""Abstraction over the source of a sync job.

A folder mounted in the container and an rclone remote behave the same way from the job's
point of view: both can list their files with path, size and mtime, and both can return a
reader over a byte range.

The rest of the runner does not need to know which of the two it is using.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

from ..rclone import client as rclone
from ..telegram.fast_transfer import LocalSliceReader
from .filters import FileFilter, build_filter
from .scanner import ScannedFile, scan

log = logging.getLogger(__name__)


@dataclass(slots=True)
class SourceFile:
    rel_path: str
    name: str
    size: int
    mtime_ns: int


class LocalSource:
    kind = "local"

    def __init__(
        self, root: str, files_per_sec: int = 0, file_filter: FileFilter | None = None
    ) -> None:
        self.root = root
        self.files_per_sec = files_per_sec
        self.filter = file_filter or FileFilter()

    @property
    def label(self) -> str:
        return self.root

    async def list_files(self, on_progress=None) -> list[SourceFile]:
        def report(files: int, dirs: int, total_bytes: int, where: str) -> None:
            if on_progress is not None:
                on_progress(files, dirs, total_bytes, where)

        found: list[ScannedFile] = await scan(self.root, self.files_per_sec, on_progress=report)
        kept = [
            SourceFile(item.rel_path, item.name, item.size, item.mtime_ns)
            for item in found
            if self.filter.allows(item.rel_path, item.size)
        ]
        _log_filtered(self.filter, len(found), self.root)
        return kept

    def reader(self, rel_path: str, offset: int, length: int):
        return LocalSliceReader(os.path.join(self.root, rel_path), offset, length)


class RcloneSource:
    kind = "rclone"

    def __init__(self, remote: str, file_filter: FileFilter | None = None) -> None:
        # Normalized once: "name:" or "name:subfolder".
        self.remote = remote.strip()
        self.filter = file_filter or FileFilter()

    @property
    def label(self) -> str:
        return self.remote

    async def list_files(self, on_progress=None) -> list[SourceFile]:
        def report(files: int, total_bytes: int, where: str) -> None:
            if on_progress is not None:
                # A remote reports no folder count: it stays at zero.
                on_progress(files, 0, total_bytes, where)

        found = await rclone.list_files(self.remote, on_progress=report)
        kept = [
            SourceFile(item.path, item.name, item.size, item.mtime_ns)
            for item in found
            if self.filter.allows(item.path, item.size)
        ]
        _log_filtered(self.filter, len(found), self.remote)
        return kept

    def _full_path(self, rel_path: str) -> str:
        base = self.remote
        if base.endswith(":") or base.endswith("/"):
            return f"{base}{rel_path}"
        return f"{base}/{rel_path}"

    def reader(self, rel_path: str, offset: int, length: int):
        return rclone.RemoteSliceReader(self._full_path(rel_path), offset, length)


def _log_filtered(file_filter: FileFilter, seen: int, label: str) -> None:
    """What a filter left out belongs in the log.

    A file that stops being backed up without a word is the kind of thing nobody notices
    until it is needed, and a pattern with one character too many is easy to write.
    """
    if file_filter.skipped:
        log.info(
            "Filter on %s left out %d files of %d (%s)",
            label,
            file_filter.skipped,
            seen,
            file_filter.describe(),
        )


def build_source(job) -> LocalSource | RcloneSource:
    file_filter = build_filter(job)
    if job.source_type == "rclone":
        if not job.remote:
            raise ValueError("The job is of type rclone but has no remote configured")
        return RcloneSource(job.remote, file_filter)
    return LocalSource(job.local_path, job.scan_files_per_sec, file_filter)
