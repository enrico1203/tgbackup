"""In memory progress state, broadcast over WebSocket.

The counters live in the process and never touch the database: they are updated on every
uploaded part and pushed to the clients once per second.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from dataclasses import dataclass, field

from ..config import settings

log = logging.getLogger(__name__)

# Damping constant of the exponential moving average of the speed, in seconds.
SPEED_SMOOTHING = 3.0

# How long a finished restore stays on the panel before the hub forgets it.
RESTORE_LINGER = 300.0


class SpeedMeter:
    """Exponential moving average of the transfer speed.

    Not a dataclass on purpose: a dataclass base whose fields all have defaults would stop
    the classes below from declaring fields without one. It only brings the methods, and
    the three fields they work on are declared by whoever inherits it, which every class
    here does.
    """

    bytes_done: int
    speed_bps: float
    _window_bytes: int
    _window_start: float

    def add_bytes(self, count: int) -> None:
        self.bytes_done += count
        self._window_bytes += count

    def tick(self) -> None:
        now = time.monotonic()
        elapsed = now - self._window_start
        if elapsed <= 0:
            return
        instant = self._window_bytes / elapsed
        weight = min(1.0, elapsed / SPEED_SMOOTHING)
        self.speed_bps = self.speed_bps + (instant - self.speed_bps) * weight
        self._window_bytes = 0
        self._window_start = now


@dataclass
class JobProgress(SpeedMeter):
    job_id: int
    name: str
    phase: str = "idle"
    current_file: str | None = None
    current_part: int = 0
    current_parts: int = 0

    files_total: int = 0
    files_done: int = 0
    bytes_total: int = 0
    bytes_done: int = 0

    # Scan progress. On a network mount the walk takes a long time and without these
    # counters the dashboard would sit still on "preparing".
    scanned_files: int = 0
    scanned_dirs: int = 0
    scanned_bytes: int = 0
    scanned_where: str | None = None

    started_at: float = field(default_factory=time.monotonic)
    speed_bps: float = 0.0

    _window_bytes: int = 0
    _window_start: float = field(default_factory=time.monotonic)

    @property
    def bytes_remaining(self) -> int:
        return max(0, self.bytes_total - self.bytes_done)

    @property
    def files_remaining(self) -> int:
        return max(0, self.files_total - self.files_done)

    @property
    def eta_seconds(self) -> float | None:
        if self.speed_bps < 1024 or self.bytes_remaining == 0:
            return None
        return self.bytes_remaining / self.speed_bps

    def snapshot(self) -> dict:
        return {
            "job_id": self.job_id,
            "name": self.name,
            "phase": self.phase,
            "current_file": self.current_file,
            "current_part": self.current_part,
            "current_parts": self.current_parts,
            "scanned_files": self.scanned_files,
            "scanned_dirs": self.scanned_dirs,
            "scanned_bytes": self.scanned_bytes,
            "scanned_where": self.scanned_where,
            "files_total": self.files_total,
            "files_done": self.files_done,
            "files_remaining": self.files_remaining,
            "bytes_total": self.bytes_total,
            "bytes_done": self.bytes_done,
            "bytes_remaining": self.bytes_remaining,
            "speed_bps": round(self.speed_bps, 1),
            "eta_seconds": self.eta_seconds,
            "elapsed_seconds": round(time.monotonic() - self.started_at, 1),
        }


@dataclass
class DownloadProgress(SpeedMeter):
    """Progress of a download job.

    It does not reuse `JobProgress` because the counters mean different things: there the
    scan looks at the source and everything found has to go up, here the channel index is
    known upfront and the scan looks at the destination to find out how little of it is
    actually missing.
    """

    job_id: int
    name: str
    phase: str = "idle"
    current_file: str | None = None
    current_part: int = 0
    current_parts: int = 0

    # What the channel holds, according to the index.
    indexed_files: int = 0
    indexed_bytes: int = 0
    # What the destination already holds. `dest_where` follows the scan folder by folder.
    dest_files: int = 0
    dest_where: str | None = None
    present_files: int = 0

    # The missing files, the ones this run downloads.
    files_total: int = 0
    files_done: int = 0
    bytes_total: int = 0
    bytes_done: int = 0

    started_at: float = field(default_factory=time.monotonic)
    speed_bps: float = 0.0

    _window_bytes: int = 0
    _window_start: float = field(default_factory=time.monotonic)

    @property
    def bytes_remaining(self) -> int:
        return max(0, self.bytes_total - self.bytes_done)

    @property
    def files_remaining(self) -> int:
        return max(0, self.files_total - self.files_done)

    @property
    def eta_seconds(self) -> float | None:
        if self.speed_bps < 1024 or self.bytes_remaining == 0:
            return None
        return self.bytes_remaining / self.speed_bps

    def snapshot(self) -> dict:
        return {
            "job_id": self.job_id,
            "name": self.name,
            "phase": self.phase,
            "current_file": self.current_file,
            "current_part": self.current_part,
            "current_parts": self.current_parts,
            "indexed_files": self.indexed_files,
            "indexed_bytes": self.indexed_bytes,
            "dest_files": self.dest_files,
            "dest_where": self.dest_where,
            "present_files": self.present_files,
            "files_total": self.files_total,
            "files_done": self.files_done,
            "files_remaining": self.files_remaining,
            "bytes_total": self.bytes_total,
            "bytes_done": self.bytes_done,
            "bytes_remaining": self.bytes_remaining,
            "speed_bps": round(self.speed_bps, 1),
            "eta_seconds": self.eta_seconds,
            "elapsed_seconds": round(time.monotonic() - self.started_at, 1),
        }


@dataclass
class RestoreProgress(SpeedMeter):
    """One file being rebuilt, or a whole folder.

    The counters are the same either way: a single file is a batch of one, which is why
    `files_total` defaults to 1 and the panel can show both without asking which it is.
    """

    restore_id: str
    file_name: str
    target_path: str
    bytes_total: int
    bytes_done: int = 0
    # running, done, error, cancelled
    phase: str = "running"
    error: str | None = None
    speed_bps: float = 0.0

    files_total: int = 1
    files_done: int = 0
    # The path being written right now, relative to the folder that was asked for.
    current_file: str | None = None
    # Files that failed without stopping the rest, and the first few reasons.
    failed: int = 0
    errors: list[str] = field(default_factory=list)
    # Monotonic time at which it stopped running, filled in by the hub. Only used to
    # decide when to forget it.
    ended_at: float | None = None

    _window_bytes: int = 0
    _window_start: float = field(default_factory=time.monotonic)

    def snapshot(self) -> dict:
        remaining = max(0, self.bytes_total - self.bytes_done)
        return {
            "restore_id": self.restore_id,
            "file_name": self.file_name,
            "target_path": self.target_path,
            "phase": self.phase,
            "error": self.error,
            "bytes_total": self.bytes_total,
            "bytes_done": self.bytes_done,
            "files_total": self.files_total,
            "files_done": self.files_done,
            "current_file": self.current_file,
            "failed": self.failed,
            "errors": self.errors,
            "speed_bps": round(self.speed_bps, 1),
            "eta_seconds": (
                remaining / self.speed_bps if self.speed_bps >= 1024 and remaining else None
            ),
        }


class ProgressHub:
    def __init__(self) -> None:
        self.jobs: dict[int, JobProgress] = {}
        # Kept apart from the sync jobs: the two numbering schemes are independent and a
        # single dictionary would have job 3 overwrite download job 3.
        self.downloads: dict[int, DownloadProgress] = {}
        self.restores: dict[str, RestoreProgress] = {}
        self._subscribers: set[asyncio.Queue] = set()
        self._task: asyncio.Task | None = None

    def start_job(self, job_id: int, name: str) -> JobProgress:
        progress = JobProgress(job_id=job_id, name=name)
        self.jobs[job_id] = progress
        return progress

    def end_job(self, job_id: int) -> None:
        self.jobs.pop(job_id, None)

    def start_download(self, job_id: int, name: str) -> DownloadProgress:
        progress = DownloadProgress(job_id=job_id, name=name)
        self.downloads[job_id] = progress
        return progress

    def end_download(self, job_id: int) -> None:
        self.downloads.pop(job_id, None)

    def start_restore(self, restore: RestoreProgress) -> RestoreProgress:
        self.restores[restore.restore_id] = restore
        return restore

    def prune_restores(self) -> None:
        """Forgets restores that ended a while ago.

        They are kept for a few minutes on purpose, so somebody who started one and
        changed page still finds out how it went. Without a limit the panel would grow
        for as long as the process lives, and every frame would carry the whole history
        to every connected browser.
        """
        now = time.monotonic()
        for restore_id, restore in list(self.restores.items()):
            if restore.phase == "running":
                continue
            if restore.ended_at is None:
                restore.ended_at = now
            elif now - restore.ended_at > RESTORE_LINGER:
                self.restores.pop(restore_id, None)

    def snapshot(self) -> dict:
        return {
            "type": "progress",
            "jobs": [job.snapshot() for job in self.jobs.values()],
            "downloads": [job.snapshot() for job in self.downloads.values()],
            "restores": [restore.snapshot() for restore in self.restores.values()],
        }

    def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=4)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        self._subscribers.discard(queue)

    async def _broadcast_loop(self) -> None:
        while True:
            await asyncio.sleep(settings.progress_interval_seconds)
            for job in self.jobs.values():
                job.tick()
            for job in self.downloads.values():
                job.tick()
            for restore in self.restores.values():
                restore.tick()
            self.prune_restores()

            if not self._subscribers:
                continue
            payload = json.dumps(self.snapshot())
            for queue in list(self._subscribers):
                # A slow client must not slow the others down: the stale frame is dropped,
                # the next one arrives in a second anyway.
                if queue.full():
                    with contextlib.suppress(asyncio.QueueEmpty):
                        queue.get_nowait()
                with contextlib.suppress(asyncio.QueueFull):
                    queue.put_nowait(payload)

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._broadcast_loop())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None


hub = ProgressHub()
