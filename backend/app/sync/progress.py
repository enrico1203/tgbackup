"""Stato di avanzamento in memoria e diffusione via WebSocket.

I contatori vivono nel processo e non toccano il database: vengono aggiornati a ogni
parte caricata e spinti ai client una volta al secondo.
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

# Costante di smorzamento della media esponenziale della velocita, in secondi.
SPEED_SMOOTHING = 3.0


@dataclass
class JobProgress:
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

    started_at: float = field(default_factory=time.monotonic)
    speed_bps: float = 0.0

    _window_bytes: int = 0
    _window_start: float = field(default_factory=time.monotonic)

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
class RestoreProgress:
    restore_id: str
    file_name: str
    target_path: str
    bytes_total: int
    bytes_done: int = 0
    phase: str = "running"
    error: str | None = None
    speed_bps: float = 0.0

    _window_bytes: int = 0
    _window_start: float = field(default_factory=time.monotonic)

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
            "speed_bps": round(self.speed_bps, 1),
            "eta_seconds": (
                remaining / self.speed_bps if self.speed_bps >= 1024 and remaining else None
            ),
        }


class ProgressHub:
    def __init__(self) -> None:
        self.jobs: dict[int, JobProgress] = {}
        self.restores: dict[str, RestoreProgress] = {}
        self._subscribers: set[asyncio.Queue] = set()
        self._task: asyncio.Task | None = None

    def start_job(self, job_id: int, name: str) -> JobProgress:
        progress = JobProgress(job_id=job_id, name=name)
        self.jobs[job_id] = progress
        return progress

    def end_job(self, job_id: int) -> None:
        self.jobs.pop(job_id, None)

    def start_restore(self, restore: RestoreProgress) -> RestoreProgress:
        self.restores[restore.restore_id] = restore
        return restore

    def snapshot(self) -> dict:
        return {
            "type": "progress",
            "jobs": [job.snapshot() for job in self.jobs.values()],
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
            for restore in self.restores.values():
                restore.tick()

            if not self._subscribers:
                continue
            payload = json.dumps(self.snapshot())
            for queue in list(self._subscribers):
                # Un client lento non deve rallentare gli altri: si scarta il frame
                # vecchio, tanto il successivo arriva fra un secondo.
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
