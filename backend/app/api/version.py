"""What this installation runs, and what Docker Hub publishes.

A self-hosted installation that is never updated is the normal outcome, not the
exception: nothing on the machine knows a new image exists, and the release notes are
on a page nobody has a reason to open. So the application asks. The tags of the two
repositories are public, one anonymous GET each, and the highest one that reads as
X.Y.Z is the published version; anything else on those repositories, `latest` and the
`X.Y` alias, says nothing about which release is newest.

The answer is cached in memory for CHECK_INTERVAL. The dashboard polls, and turning
every poll into two calls to Docker Hub would be a good way to get rate limited for
information that changes a few times a month. Nothing is persisted: a restart costing
one extra pair of requests is cheaper than a column and a migration.

A check that fails is never an error the user has to deal with. The previous answer, if
there is one, stays in place, the reason travels on the response for whoever wants to
know why the banner is not there, and the next attempt is RETRY_INTERVAL away rather
than the full six hours.
"""

import asyncio
import json
import logging
import re
import time
import urllib.request
from datetime import UTC, datetime

from fastapi import APIRouter

from ..config import settings
from ..deps import ActiveUserDep
from ..schemas import VersionOut

router = APIRouter(prefix="/api/version", tags=["version"])
log = logging.getLogger("tgbackup.version")

REPOSITORIES = {
    "backend": "enrico1203/tgbackup-backend",
    "frontend": "enrico1203/tgbackup-frontend",
}
# Ordered by last update by default, so on a repository holding more tags than one page
# the newest are the ones that come back.
HUB_TAGS = "https://hub.docker.com/v2/repositories/{repository}/tags?page_size=100"
HUB_TIMEOUT = 15.0
CHECK_INTERVAL = 6 * 3600.0
RETRY_INTERVAL = 900.0

SEMVER = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")

_lock = asyncio.Lock()
_due_at = 0.0
_checked_at: datetime | None = None
_latest: dict[str, str | None] = {"backend": None, "frontend": None}
_error: str | None = None


def _highest_tag(payload: bytes) -> str | None:
    results = json.loads(payload).get("results") or []
    versions = [
        tuple(int(part) for part in match.groups())
        for entry in results
        if (match := SEMVER.match(str(entry.get("name", "")))) is not None
    ]
    if not versions:
        return None
    return ".".join(str(part) for part in max(versions))


def _fetch(repository: str) -> str | None:
    """Blocking, and called in a thread: urllib is what the image already has, and one
    request every six hours does not justify a dependency."""
    request = urllib.request.Request(
        HUB_TAGS.format(repository=repository),
        headers={"Accept": "application/json", "User-Agent": "tgbackup"},
    )
    with urllib.request.urlopen(request, timeout=HUB_TIMEOUT) as response:
        return _highest_tag(response.read())


async def _refresh() -> None:
    global _due_at, _checked_at, _error

    async with _lock:
        # A second request that waited on the lock while the first one was fetching
        # finds the answer already there.
        if time.monotonic() < _due_at:
            return
        try:
            for image, repository in REPOSITORIES.items():
                _latest[image] = await asyncio.to_thread(_fetch, repository)
            _checked_at = datetime.now(UTC)
            _error = None
            _due_at = time.monotonic() + CHECK_INTERVAL
        except Exception as exc:
            _error = f"{type(exc).__name__}: {exc}"
            _due_at = time.monotonic() + RETRY_INTERVAL
            log.warning("Update check failed: %s", _error)


@router.get("", response_model=VersionOut)
async def version(_: ActiveUserDep) -> VersionOut:
    # Asked for even by an image built from a working copy, which has no version of its
    # own to compare: the banner stays quiet there, but the Settings page still has to
    # be able to say which release exists.
    if time.monotonic() >= _due_at:
        await _refresh()

    return VersionOut(
        backend=settings.app_version,
        latest_backend=_latest["backend"],
        latest_frontend=_latest["frontend"],
        checked_at=_checked_at,
        error=_error,
    )
