"""Low impact filesystem scanning.

A file's identity is the triple (relative path, size, mtime in nanoseconds), read with a
single stat per entry. The content is never opened nor read: no hashing, no checksums, no
pressure on the disk cache.
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Callable
from dataclasses import dataclass

log = logging.getLogger(__name__)

# How many entries between updates of the counter shown on the dashboard.
REPORT_EVERY = 25

# Callback invoked by the scanning thread with (files found, folders visited,
# total bytes, current folder).
ScanProgress = Callable[[int, int, int, str], None]


@dataclass(slots=True)
class ScannedFile:
    rel_path: str
    name: str
    size: int
    mtime_ns: int


def _walk(root: str, on_progress: ScanProgress | None = None) -> list[ScannedFile]:
    """Recursive walk with os.scandir, without following symbolic links.

    scandir reuses the directory entry data, so is_dir and is_file often cost no extra
    stat. The real stat is only needed for size and mtime, and never touches the file
    content.

    On a network mount the walk can take a long time, so it reports progress as it goes
    instead of staying silent until the end.
    """
    found: list[ScannedFile] = []
    stack = [root]
    dirs = 0
    total_bytes = 0
    since_report = 0

    while stack:
        current = stack.pop()
        dirs += 1
        try:
            with os.scandir(current) as entries:
                for entry in entries:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            stack.append(entry.path)
                        elif entry.is_file(follow_symlinks=False):
                            info = entry.stat(follow_symlinks=False)
                            rel = os.path.relpath(entry.path, root)
                            found.append(
                                ScannedFile(
                                    rel_path=rel,
                                    name=entry.name,
                                    size=info.st_size,
                                    mtime_ns=info.st_mtime_ns,
                                )
                            )
                            total_bytes += info.st_size
                    except OSError as exc:
                        log.warning("Entry skipped %s: %s", entry.path, exc)
        except OSError as exc:
            log.warning("Folder not readable %s: %s", current, exc)

        since_report += 1
        if on_progress is not None and since_report >= REPORT_EVERY:
            since_report = 0
            relative = os.path.relpath(current, root)
            on_progress(len(found), dirs, total_bytes, "." if relative == "." else relative)

    if on_progress is not None:
        on_progress(len(found), dirs, total_bytes, "")
    return found


async def scan(
    root: str, files_per_sec: int = 0, on_progress: ScanProgress | None = None
) -> list[ScannedFile]:
    """Scans `root` in a thread, with optional throttling.

    `files_per_sec` at zero means no limit. Above zero the total time is spread out so the
    disk is not saturated when the folder sits on slow storage or is shared with other
    workloads.
    """
    # In a thread like the walk that follows: on a network mount a stat can hang, and
    # the scheduler must stay responsive for the other jobs.
    if not await asyncio.to_thread(os.path.isdir, root):
        raise FileNotFoundError(f"Folder {root} does not exist inside the container")

    files = await asyncio.to_thread(_walk, root, on_progress)

    if files_per_sec > 0:
        # The walk is already over: the pause owed is spread across chunks, so the rest
        # of the application stays responsive and the requested pace is respected over the
        # complete scan cycle.
        total_delay = len(files) / files_per_sec
        step = 0.2
        waited = 0.0
        while waited < total_delay:
            await asyncio.sleep(min(step, total_delay - waited))
            waited += step
    else:
        await asyncio.sleep(0)

    return files
