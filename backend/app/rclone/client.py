"""Access to rclone remotes without mounting them.

Four commands cover everything:

  rclone lsjson -R --files-only   lists a whole remote through the API
  rclone cat --offset N --count M reads an exact byte range
  rclone rcat --size N            writes a file from standard input
  rclone touch --timestamp        restores the date of a file just written

Listing through the API is far faster than walking a FUSE mount, and ranged reads avoid
copying files to disk before uploading them: slices go straight from the stream to the
uploader. Writing works the same way in the other direction, the bytes coming out of
Telegram go into the pipe without ever touching the disk.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime

from ..config import settings

log = logging.getLogger(__name__)

RCLONE = shutil.which("rclone") or "/usr/local/bin/rclone"

# Common arguments: no banner, no update check, minimal logging.
BASE_ARGS = ["--config", str(settings.rclone_config_path), "--log-level", "ERROR"]


class RcloneError(Exception):
    """Error meant to be shown directly to the user."""


def _clean_error(raw: bytes | str) -> str:
    """Cleans up rclone stderr so it can be shown in the interface.

    Lines arrive as "2026/07/25 22:10:43 ERROR : message": date, time and level tell the
    user nothing and hide the actual message.
    """
    text = raw.decode(errors="replace") if isinstance(raw, bytes) else raw
    lines = []
    for line in text.strip().splitlines():
        cleaned = re.sub(r"^\d{4}/\d{2}/\d{2} \d{2}:\d{2}:\d{2}\s+", "", line.strip())
        cleaned = re.sub(r"^(CRITICAL|ERROR|NOTICE|WARNING|INFO|DEBUG)\s*:\s*", "", cleaned)
        cleaned = re.sub(r"^Failed to \w+ .*?: ", "", cleaned)
        if cleaned and cleaned not in lines:
            lines.append(cleaned)
    return " | ".join(lines)[:400] or "unknown error"


@dataclass(slots=True)
class RemoteFile:
    path: str
    name: str
    size: int
    mtime_ns: int


def config_exists() -> bool:
    return settings.rclone_config_path.exists()


def write_config(content: str) -> None:
    """Writes the configuration to disk with restricted permissions.

    The file holds the cloud credentials: it is encrypted in the database, and on disk it
    stays readable only by root inside the container.
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
        raise RcloneError(f"rclone did not answer within {timeout:.0f} seconds") from None

    if process.returncode != 0:
        raise RcloneError(_clean_error(stderr))
    return stdout.decode(errors="replace")


async def version() -> str:
    output = await _run(["version"], timeout=20)
    return output.splitlines()[0] if output else "unknown"


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
    """Reads lsjson output as it arrives, stopping once there is enough.

    lsjson prints one object per line, so there is no need to wait for the end to get the
    first results. With `max_items` the process is closed as soon as the requested count
    is reached: listing the first entries of a folder holding tens of thousands of files
    then costs the same as listing a small one, instead of waiting for every name to be
    decrypted.
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
    # A caller collecting through on_item does not need a second copy kept here: on a
    # remote with hundreds of thousands of files that would be double memory for nothing.
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
            f"rclone did not answer within {timeout:.0f} seconds for {target}"
        ) from None

    if stopped_early:
        # Deliberate close: the non-zero exit that follows is not an error.
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
            _clean_error(stderr) if stderr else f"lsjson of {target} failed"
        )
    return items


async def check_remote(remote: str) -> None:
    """Checks that the remote answers.

    It stops at the first entry received, so the cost does not depend on how many the
    folder holds. An empty folder makes rclone exit successfully, which is fine too.
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
    """First level of a remote, to get a sense of what it holds.

    Depth one plus streaming that stops at the first `limit` entries: on a folder with
    tens of thousands of files a full listing would take minutes, while here the cost does
    not depend on how full it is.
    """
    items = await _stream_lsjson(
        _normalize(remote),
        ["--max-depth", "1", "--no-mimetype"],
        timeout=settings.rclone_preview_timeout,
        max_items=limit,
    )

    # Sorting over the entries received, folders first and then files, the way a file
    # manager would show them. This is not a global sort of the folder, because reading
    # stops before it has all been read.
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
    """Accepts both "name:" and "name:subfolder"."""
    remote = remote.strip()
    if ":" not in remote:
        raise RcloneError(
            f"'{remote}' is not a valid rclone path: the colon is missing, "
            "for example mycloud-crypt: or mycloud-crypt:Movies"
        )
    return remote


def _parse_mtime(value: str) -> int:
    """ISO 8601 ModTime in nanoseconds. Zero when absent."""
    if not value:
        return 0
    try:
        return int(datetime.fromisoformat(value).timestamp() * 1_000_000_000)
    except ValueError:
        return 0


async def list_files(
    remote: str, on_progress=None, timeout: float | None = None
) -> list[RemoteFile]:
    """Recursively lists the files of a remote, reporting progress.

    On remotes with hundreds of thousands of files the listing takes minutes: reading it
    as a stream makes it possible to show how many files have been found so far instead of
    staying silent until the end.
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
    """Sequential reader over a byte range of a remote.

    It uses no mount and writes nothing to disk: rclone opens a ranged request and the
    bytes go straight to the uploader.
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
        """Reads `size` bytes, or fewer only at the end of the stream.

        StreamReader.read returns whatever it has at that moment, which on a network pipe
        is almost always less than requested: without the loop, parts shorter than they
        should be would be sent to Telegram, and every part but the last must be full.
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

        # A non-zero exit only counts when it was not caused by closing early after
        # reading everything that was needed.
        if exc_type is None and self._read < self._length and process.returncode not in (0, None):
            raise RcloneError(
                _clean_error(stderr)
                if stderr
                else f"Reading {self._remote_file} stopped at {self._read}/{self._length} bytes"
            )


async def touch(remote_file: str, when: datetime) -> None:
    """Sets the modification time of a file already on the remote.

    The timestamp is given in UTC, which is how rclone reads it: verified by writing a
    file through a remote of type local from a container set to Europe/Rome, where a
    timestamp read as local time would have landed two hours out. Backends that cannot
    store a date make this fail, so the caller decides what a failure is worth: for a
    file that has arrived complete it is not worth much.
    """
    await _run(
        [
            "touch",
            "--no-create",
            "--timestamp",
            when.strftime("%Y-%m-%dT%H:%M:%S"),
            remote_file,
        ],
        timeout=120,
    )


class RemoteWriter:
    """A file being written on a remote, fed through the standard input of `rclone rcat`.

    Nothing is staged on disk: the bytes arriving from Telegram go straight into the pipe.
    A pipe accepts them in order only, which is why the caller has to hand them over in
    order, and there is no temporary name: rclone finalises an object at the end of the
    stream, and a leftover name on a remote is far harder to clean up than a local one. An
    interrupted transfer leaves either nothing or an object shorter than it should be, and
    a run that comes later downloads it again because the size does not match.
    """

    # Told apart from a local file, which is written part by part at the right offset.
    random_access = False

    def __init__(self, remote_file: str, size: int, mtime_ns: int = 0) -> None:
        self.path = remote_file
        self._size = size
        self._mtime_ns = mtime_ns
        self._process: asyncio.subprocess.Process | None = None
        self._committed = False

    async def __aenter__(self) -> RemoteWriter:
        self._process = await asyncio.create_subprocess_exec(
            RCLONE,
            *BASE_ARGS,
            "rcat",
            # Without the size rclone buffers the stream to decide how to upload it.
            "--size",
            str(self._size),
            self.path,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
            limit=8 * 1024 * 1024,
        )
        return self

    async def write(self, data: bytes) -> None:
        process = self._process
        assert process is not None and process.stdin is not None
        try:
            process.stdin.write(data)
            await process.stdin.drain()
        except (BrokenPipeError, ConnectionResetError) as exc:
            # rclone died: its stderr says why, the broken pipe says nothing.
            raise RcloneError(await self._failure()) from exc

    async def _failure(self) -> str:
        process = self._process
        if process is None:
            return f"rclone is not writing {self.path}"
        stderr = b""
        if process.stderr is not None:
            try:
                stderr = await asyncio.wait_for(process.stderr.read(), 10)
            except Exception:
                stderr = b""
        await process.wait()
        return _clean_error(stderr) if stderr else f"Writing {self.path} failed"

    async def commit(self) -> None:
        """Closes the stream and waits for rclone to finish writing the file."""
        process = self._process
        assert process is not None and process.stdin is not None
        try:
            process.stdin.close()
            await process.stdin.wait_closed()
        except (BrokenPipeError, ConnectionResetError):
            pass

        stderr = b""
        if process.stderr is not None:
            stderr = await process.stderr.read()
        await process.wait()
        if process.returncode != 0:
            raise RcloneError(
                _clean_error(stderr) if stderr else f"Writing {self.path} failed"
            )
        self._committed = True

        if self._mtime_ns > 0:
            # Best effort: a backend that refuses to store a date is no reason to lose a
            # file that has arrived complete.
            stamp = datetime.fromtimestamp(self._mtime_ns / 1_000_000_000, UTC)
            try:
                await touch(self.path, stamp)
            except RcloneError as exc:
                log.warning("Setting the date of %s failed: %s", self.path, exc)

    async def __aexit__(self, exc_type, exc, tb) -> None:
        process = self._process
        if process is None or self._committed:
            return
        if process.returncode is None:
            try:
                process.kill()
            except ProcessLookupError:
                pass
        await process.wait()
