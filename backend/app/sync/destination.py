"""Abstraction over the destination of a download job.

The mirror image of `source.py`. A folder mounted in the container and an rclone remote
behave the same way from the job's point of view: both can list what they already hold and
both can take a new file. The runner does not need to know which of the two it is writing
to.

The two differ in one thing that cannot be hidden, and the runner does have to ask about
it: a local file is written with pwrite at the offset of every part, so the parts can be
downloaded in parallel, while a remote is written through `rclone rcat`, that is a pipe,
and a pipe only accepts bytes in order. `random_access` says which of the two it is.
"""

from __future__ import annotations

import asyncio
import logging
import os

from ..rclone import client as rclone
from .scanner import scan

log = logging.getLogger(__name__)

# Suffix of a local file being written. A download interrupted halfway leaves a file of
# the right size, because it is allocated upfront, and the next run would take it for
# complete: it only takes its final name once every part has arrived.
PARTIAL_SUFFIX = ".tgpart"


class DestinationError(Exception):
    """Error meant to be shown directly to the user."""


def _check_rel_path(rel_path: str) -> str:
    """Refuses a relative path that would escape the destination.

    The paths come from the index of a channel, which may have been produced by another
    machine or imported from a file: nothing guarantees they stay inside the folder they
    are given, and a download job is the only part of this application that writes.
    """
    if rel_path.startswith("/") or ".." in rel_path.split("/"):
        raise DestinationError(f"Path {rel_path} points outside the destination")
    return rel_path


class LocalSink:
    """A local file being written, with the parts landing wherever they belong."""

    random_access = True

    def __init__(self, path: str, size: int, mtime_ns: int) -> None:
        self.path = path
        self.temp_path = path + PARTIAL_SUFFIX
        self._size = size
        self._mtime_ns = mtime_ns
        self._committed = False
        self.fd: int = -1

    async def __aenter__(self) -> LocalSink:
        await asyncio.to_thread(os.makedirs, os.path.dirname(self.path), 0o755, True)
        self.fd = await asyncio.to_thread(
            os.open, self.temp_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644
        )
        # Sized upfront: the positional writes of the later parts do not have to extend
        # the file one at a time.
        await asyncio.to_thread(os.ftruncate, self.fd, self._size)
        return self

    async def write(self, data: bytes) -> None:
        raise NotImplementedError("A local destination is written by part, not as a stream")

    async def commit(self) -> None:
        await asyncio.to_thread(os.close, self.fd)
        self.fd = -1
        if self._mtime_ns > 0:
            # The original mtime is restored: with size it is what identifies a file here,
            # so a folder brought back this way can be uploaded again without every file
            # looking modified.
            await asyncio.to_thread(os.utime, self.temp_path, ns=(self._mtime_ns, self._mtime_ns))
        await asyncio.to_thread(os.replace, self.temp_path, self.path)
        self._committed = True

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if self._committed:
            return
        if self.fd >= 0:
            await asyncio.to_thread(os.close, self.fd)
            self.fd = -1
        # Nothing half written is left behind: what is not complete is not kept.
        await asyncio.to_thread(_unlink_quietly, self.temp_path)


def _unlink_quietly(path: str) -> None:
    try:
        os.unlink(path)
    except OSError as exc:
        log.warning("Removing the partial file %s failed: %s", path, exc)


class LocalDestination:
    kind = "local"

    def __init__(self, root: str) -> None:
        self.root = root

    @property
    def label(self) -> str:
        return self.root

    async def prepare(self) -> None:
        if not await asyncio.to_thread(os.path.isdir, self.root):
            raise DestinationError(
                f"Folder {self.root} does not exist inside the container. "
                "Add it as a writable volume in docker-compose.yml and restart the backend."
            )
        if not await asyncio.to_thread(os.access, self.root, os.W_OK):
            raise DestinationError(
                f"Folder {self.root} is not writable. The folders to back up are mounted "
                "read-only on purpose: a download destination needs its own volume "
                "without the :ro suffix."
            )

    async def list_files(self, on_progress=None) -> dict[str, int]:
        def report(files: int, dirs: int, total_bytes: int, where: str) -> None:
            if on_progress is not None:
                on_progress(files, dirs, total_bytes, where)

        found = await scan(self.root, on_progress=report)
        # The files still being written belong to an interrupted run: they are not there
        # as far as the comparison is concerned, and they are overwritten.
        return {
            item.rel_path: item.size
            for item in found
            if not item.rel_path.endswith(PARTIAL_SUFFIX)
        }

    def sink(self, rel_path: str, size: int, mtime_ns: int) -> LocalSink:
        return LocalSink(os.path.join(self.root, _check_rel_path(rel_path)), size, mtime_ns)


class RcloneDestination:
    kind = "rclone"

    def __init__(self, remote: str) -> None:
        self.remote = remote.strip()

    @property
    def label(self) -> str:
        return self.remote

    async def prepare(self) -> None:
        try:
            await rclone.check_remote(self.remote)
        except rclone.RcloneError as exc:
            raise DestinationError(f"Remote {self.remote} does not answer: {exc}") from exc

    async def list_files(self, on_progress=None) -> dict[str, int]:
        def report(files: int, total_bytes: int, where: str) -> None:
            if on_progress is not None:
                # A remote reports no folder count: it stays at zero.
                on_progress(files, 0, total_bytes, where)

        found = await rclone.list_files(self.remote, on_progress=report)
        return {item.path: item.size for item in found}

    def _full_path(self, rel_path: str) -> str:
        base = self.remote
        if base.endswith(":") or base.endswith("/"):
            return f"{base}{rel_path}"
        return f"{base}/{rel_path}"

    def sink(self, rel_path: str, size: int, mtime_ns: int) -> rclone.RemoteWriter:
        return rclone.RemoteWriter(self._full_path(_check_rel_path(rel_path)), size, mtime_ns)


def build_destination(job) -> LocalDestination | RcloneDestination:
    if job.dest_type == "rclone":
        if not job.remote:
            raise DestinationError("The job writes to rclone but has no remote configured")
        return RcloneDestination(job.remote)
    if not job.local_path:
        raise DestinationError("The job writes to a local folder but has no path configured")
    return LocalDestination(job.local_path)
