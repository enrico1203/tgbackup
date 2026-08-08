"""Backing off when Telegram says wait, and saying so out loud.

A flood wait is not an error, it is an instruction: the account has asked for more than
its share and the server states how long to hold off. Telethon has its own answer, which
is wrong for a transfer. `TelegramClient._call` sleeps a flood wait shorter than
`flood_sleep_threshold` and **counts the sleep as one of `request_retries`**, so five
consecutive waits of sixteen seconds exhaust the attempts and the call dies. That failure
is what the caller sees, and it says nothing about a flood wait: the part is thrown away,
the slice restarts, the file ends in `error`. Measured on this installation over two days,
an account limited at a steady sixteen seconds uploaded nothing at all while the process
issued a hundred and thirty thousand rejected requests, each of which is another request
against an account that was already being told to stop.

So the transfer owns the policy instead. One gate per run is shared by every connection:
the first one told to wait closes it for everybody, because twenty connections discovering
the same limit one after another is the storm, not the cure. The delay escalates,
`BACKOFF` below, and never shrinks while the waits keep coming: a limit that lasts hours
is answered in hours rather than by asking again every sixteen seconds. It steps back down
only after `RESET_SECONDS` of transfers that worked, so a single flood wait in an otherwise
healthy run costs half a minute and nothing more.

The gate is also the only thing that knows the transfer is alive but held, which is a state
the interface had no way to show: a job stuck at zero bytes looked exactly like a job that
was slow. `snapshot` travels inside the progress frame and the browser paints it red.
"""

from __future__ import annotations

import asyncio
import logging
import time

log = logging.getLogger(__name__)

# The ladder, in seconds. The first step is already far above the sixteen seconds Telegram
# asks for, on purpose: obeying the letter of a flood wait and retrying the instant it
# expires is what keeps an account pinned against its limit.
BACKOFF = (30, 60, 120, 300, 600, 900, 1800, 3600)

# How long the transfer has to run clean before the ladder is forgotten. Shorter than the
# smallest step, and a run alternating one good part with one wait would keep resetting to
# thirty seconds instead of climbing.
RESET_SECONDS = 300.0

# Longest single sleep while held. The countdown shown in the interface is recomputed at
# every wake-up, and a cancelled job must not wait out an hour before noticing.
MAX_SLEEP = 1.0


class FloodGate:
    """The wait every connection of one run observes together."""

    def __init__(self, label: str = "", cancel=None) -> None:
        self._label = label
        self._cancel = cancel
        self._until = 0.0
        self._total = 0.0
        self._level = 0
        self.waits = 0

    def remaining(self) -> float:
        return max(0.0, self._until - time.monotonic())

    async def hold(self) -> None:
        """Waits for the gate to open. Called before every request, cheap when open."""
        while True:
            left = self.remaining()
            if left <= 0:
                return
            if self._cancel is not None and self._cancel.is_set():
                raise asyncio.CancelledError("Interrupted while waiting out a flood wait")
            await asyncio.sleep(min(left, MAX_SLEEP))

    async def flooded(self, seconds: float) -> None:
        """Records a flood wait, then holds until it has passed.

        Only the connection that finds the gate open decides the next delay: the other
        nineteen are about to report the same limit and would otherwise climb the ladder
        by twenty steps at once. A wait longer than the one already running still extends
        it, since the server has just been more specific than the ladder was.
        """
        now = time.monotonic()
        asked = float(seconds) + 1.0
        if now >= self._until:
            step = float(BACKOFF[min(self._level, len(BACKOFF) - 1)])
            self._level += 1
            self.waits += 1
            self._until = now + max(asked, step)
            self._total = self._until - now
            log.warning(
                "Telegram asked %s to wait %.0fs%s: holding every connection for %.0fs",
                self._label or "the transfer",
                seconds,
                f" (wait {self.waits} of this run)" if self.waits > 1 else "",
                self._total,
            )
        elif now + asked > self._until:
            self._until = now + asked
            self._total = self._until - now
        await self.hold()

    def cleared(self) -> None:
        """A request went through. Steps the ladder down once the coast has been clear."""
        if self._level and time.monotonic() - self._until > RESET_SECONDS:
            log.info("%s is transferring again, flood backoff reset", self._label or "Transfer")
            self._level = 0

    def snapshot(self) -> dict:
        left = self.remaining()
        return {
            "flood_wait_seconds": round(left, 1) if left > 0 else None,
            "flood_wait_total": round(self._total, 1) if left > 0 else None,
            "flood_waits": self.waits,
        }


def snapshot_of(gate: FloodGate | None) -> dict:
    """The three fields a progress frame carries, for a transfer with or without a gate."""
    if gate is None:
        return {"flood_wait_seconds": None, "flood_wait_total": None, "flood_waits": 0}
    return gate.snapshot()
