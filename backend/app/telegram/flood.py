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

Not every wait is that, though, and treating them alike was its own failure (measured
2026-08-12, five bots uploading parts). Telegram answers a part with "wait one second" and
goes on taking the bytes: that is the pace of a transfer that is working, not a punishment,
and the ladder answered it with thirty seconds of silence, then sixty, then a hundred and
twenty, per bot. The run then spent its time inside a backoff of its own making. So a wait
under `SHORT_WAIT_SECONDS` is obeyed for exactly as long as it says and climbs nothing;
above it, the ladder. Pacing that never lets up is answered by opening fewer connections
rather than by sleeping longer, which is asking for less instead of waiting more.

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

# Twenty connections meet the same cut within milliseconds of each other, so twenty
# reports are one event. Counted otherwise, "held back 3 times" would read as 60 and mean
# nothing to anybody.
CUT_COALESCE_SECONDS = 10.0

# What it takes to say an account is being held back, and how long that stays true after
# the last sign of it. One lost part in an hour is a lost part; a second one is a pattern,
# and the pattern is what the interface has to name. Fifteen minutes of quiet and the job
# stops being marked, because the state it describes is the present one.
LIMIT_MIN_EVENTS = 2
LIMIT_MEMORY_SECONDS = 900.0

# The connection budget under a limit. Twenty is the protocol ceiling and the right number
# when nothing is in the way; against an account being held back it is also twenty ways of
# asking for the same refusal at once. Each cut takes a quarter of them away, five clean
# minutes give one back, and the floor is two, because one connection is not a parallel
# transfer at all. Whether fewer connections make Telegram friendlier is not something a
# client can know: what the run does is try, and keep what works. Nothing is lost if the
# answer is no, since the bytes keep moving at every count.
CONNECTION_FLOOR = 2
GROW_AFTER_SECONDS = 300.0

# A wait this short is not a limit, it is the server giving the transfer its pace, and it
# is answered by waiting exactly that long (measured 2026-08-12, bots uploading parts:
# Telegram asks for one to three seconds and keeps taking the bytes). The ladder exists for
# the other thing, an account told to wait sixteen seconds over and over, where obeying to
# the letter and asking again the instant it expires is what keeps it pinned. Answering a
# one second pace with thirty seconds of silence, then sixty, then a hundred and twenty, is
# the same mistake in the other direction: the run then spends its time inside a backoff of
# its own making rather than inside anything Telegram asked for.
SHORT_WAIT_SECONDS = 5.0

# Pacing that never lets up is a limit after all, and the answer to it is not to sleep
# longer but to ask for less: this many short waits inside the window count as one cut, so
# the next slice opens fewer connections. Counted per window rather than per wait, or a
# transfer being paced steadily would walk straight down to the floor.
PACE_WINDOW_SECONDS = 60.0
PACE_STORM = 20


class FloodGate:
    """The wait every connection of one run observes together.

    It is also the only thing that sees the whole shape of what Telegram is doing to a
    run: the waits it announces and the parts it silently stops answering both pass
    through here, so this is where the count belongs that lets a job say it is being held
    back rather than being slow.
    """

    def __init__(self, label: str = "", cancel=None) -> None:
        self._label = label
        self._cancel = cancel
        self._until = 0.0
        self._total = 0.0
        self._level = 0
        self._last_event = 0.0
        self._last_cut = 0.0
        self._allowed: int | None = None
        self._last_grow = 0.0
        # The pace is held apart from the ladder: it delays the parts exactly as a wait
        # does, and it is deliberately invisible to `remaining`, so a second of pacing is
        # not painted as a job being held. See flooded.
        self._paced_until = 0.0
        self._pace_window = 0.0
        self._pace_in_window = 0
        self.paced = 0
        self.waits = 0
        self.cuts = 0

    @property
    def events(self) -> int:
        """Everything Telegram did to this run to slow it down, said out loud or not."""
        return self.waits + self.cuts

    def limited(self) -> bool:
        """Whether the account is being held back right now, as far as this run can tell."""
        if self.events < LIMIT_MIN_EVENTS:
            return False
        return (time.monotonic() - self._last_event) < LIMIT_MEMORY_SECONDS

    def cut(self) -> None:
        """One part taken and never answered, or a connection that broke carrying it."""
        now = time.monotonic()
        self._last_event = now
        if now - self._last_cut <= CUT_COALESCE_SECONDS:
            return
        self.cuts += 1
        self._last_cut = now
        if self._allowed is not None and self._allowed > CONNECTION_FLOOR:
            self._allowed = max(CONNECTION_FLOOR, self._allowed - max(1, self._allowed // 4))
            self._last_grow = now
            log.warning(
                "%s cut again: the next slice opens %d connections instead of the ceiling",
                self._label or "The transfer", self._allowed,
            )

    def allowance(self, ceiling: int) -> int:
        """How many connections a slice starting now may open, out of `ceiling`.

        Read when a slice starts and not while one runs: the senders of a slice already
        going are built and dividing its parts between them, and taking one away in the
        middle would mean rewriting the round robin under it for no gain. Cuts come every
        few minutes and slices last minutes, so the next one is soon enough.

        Growing back is one connection per clean stretch, shrinking is a quarter at a
        time: it takes an hour of quiet to walk back up from the floor, which is the right
        asymmetry when the cost of being wrong upwards is another cut.
        """
        now = time.monotonic()
        if self._allowed is None:
            self._allowed = ceiling
            self._last_grow = now
        elif self._allowed < ceiling:
            quiet = now - max(self._last_event, self._last_grow)
            steps = int(quiet // GROW_AFTER_SECONDS)
            if steps > 0:
                self._allowed = min(ceiling, self._allowed + steps)
                self._last_grow = now
                log.info(
                    "%s has been clean for %.0f minutes: back to %d connections",
                    self._label or "The transfer", quiet / 60, self._allowed,
                )
        return max(1, min(ceiling, self._allowed))

    def remaining(self) -> float:
        """How long the gate is held for, which is the ladder and not the pace.

        The pace is left out on purpose: it is a second or two, it is what a healthy
        transfer looks like on this server, and the interface would paint the job red for
        it once a second.
        """
        return max(0.0, self._until - time.monotonic())

    async def hold(self) -> None:
        """Waits for the gate to open. Called before every request, cheap when open."""
        while True:
            left = max(self.remaining(), self._paced_until - time.monotonic())
            if left <= 0:
                return
            if self._cancel is not None and self._cancel.is_set():
                raise asyncio.CancelledError("Interrupted while waiting out a flood wait")
            await asyncio.sleep(min(left, MAX_SLEEP))

    async def flooded(self, seconds: float) -> None:
        """Records a flood wait, then holds until it has passed.

        Two different things arrive here. A short one is the server pacing the transfer and
        is answered by waiting exactly as long as it said; anything longer is a limit, and
        that is what the ladder is for.
        """
        asked = float(seconds)
        if 0 < asked <= SHORT_WAIT_SECONDS:
            await self._pace(asked)
            return
        label = self._label or "the transfer"
        await self._close(asked + 1.0, f"asked {label} to wait {asked:.0f}s")

    async def _pace(self, seconds: float) -> None:
        """Obeys a short wait without climbing anything.

        It is not counted as an event either: a transfer that is being given its pace is a
        transfer that is working, and calling it a limit would put a job that is moving
        bytes at full speed in the same state as one that has stopped. What it does count
        is the storm: pacing that will not let up is answered by opening fewer connections,
        which is asking for less rather than sleeping longer.
        """
        now = time.monotonic()
        self.paced += 1
        if now - self._pace_window > PACE_WINDOW_SECONDS:
            self._pace_window = now
            self._pace_in_window = 0
        self._pace_in_window += 1
        if self._pace_in_window == PACE_STORM:
            log.info(
                "%s has been paced %d times in a minute: asking for fewer connections",
                self._label or "The transfer", PACE_STORM,
            )
            self.cut()
        self._paced_until = max(self._paced_until, now + seconds + 0.2)
        await self.hold()

    async def stalled(self) -> None:
        """Records silence where an answer was due, and holds exactly as a wait does.

        A part taken and never answered, twice in a row on connections built for it, is
        the same instruction as a flood wait given without the courtesy of saying so. It
        carries no duration, so the ladder alone decides: thirty seconds, then a minute,
        and so on for as long as the silence lasts.
        """
        await self._close(1.0, f"stopped answering {self._label or 'the transfer'}")

    async def _close(self, asked: float, what: str) -> None:
        """Holds every connection of the run for the longer of `asked` and the ladder.

        Only the connection that finds the gate open decides the next delay: the other
        nineteen are about to report the same limit and would otherwise climb the ladder
        by twenty steps at once. A wait longer than the one already running still extends
        it, since the server has just been more specific than the ladder was.
        """
        now = time.monotonic()
        self._last_event = now
        if now >= self._until:
            step = float(BACKOFF[min(self._level, len(BACKOFF) - 1)])
            self._level += 1
            self.waits += 1
            self._until = now + max(asked, step)
            self._total = self._until - now
            log.warning(
                "Telegram %s%s: holding every connection for %.0fs",
                what,
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
        limited = self.limited()
        return {
            "flood_wait_seconds": round(left, 1) if left > 0 else None,
            "flood_wait_total": round(self._total, 1) if left > 0 else None,
            "flood_waits": self.waits,
            # Short waits obeyed as asked. Not a limit and not painted as one, but worth
            # carrying: it is the difference between a transfer that is being given its
            # pace and one that is simply slow.
            "paced_waits": self.paced,
            # Held right now is one thing and being held back is another: a transfer that
            # is cut every few minutes moves bytes in between, so it never looks stopped
            # and the interface had nothing to say about it. These three are what lets it
            # say the account is limited while the bar is still moving.
            "limited": limited,
            "limited_events": self.events,
            "limited_ago": (
                round(time.monotonic() - self._last_event) if limited else None
            ),
            # What the limit is costing in connections, so the interface can say what was
            # done about it and not only that something happened.
            "connections_allowed": self._allowed,
        }


class FloodGroup:
    """Several gates read as one, for a run that uploads through more than one account.

    A bot set is N accounts, and a limit is something Telegram applies to one of them: a
    single gate shared by five bots would stop four that were doing fine because the fifth
    was told to wait. So every bot has its own, and this is what the run shows and records:
    held for as long as the longest wait still running, limited if any of them is, and the
    events summed, because "Telegram held this run back 7 times" is the same sentence
    whichever of its accounts was being held.

    It answers `snapshot` and `events` exactly as a gate does, which is all the progress
    frame and the run row ever ask for.
    """

    def __init__(self, gates: list[FloodGate]) -> None:
        self._gates = gates

    @property
    def events(self) -> int:
        return sum(gate.events for gate in self._gates)

    def limited(self) -> bool:
        return any(gate.limited() for gate in self._gates)

    def remaining(self) -> float:
        return max((gate.remaining() for gate in self._gates), default=0.0)

    def snapshot(self) -> dict:
        if not self._gates:
            return dict(EMPTY_SNAPSHOT)
        parts = [gate.snapshot() for gate in self._gates]
        waits = [part["flood_wait_seconds"] for part in parts if part["flood_wait_seconds"]]
        totals = [part["flood_wait_total"] for part in parts if part["flood_wait_total"]]
        allowed = [
            part["connections_allowed"] for part in parts if part["connections_allowed"]
        ]
        ago = [part["limited_ago"] for part in parts if part["limited_ago"] is not None]
        return {
            "flood_wait_seconds": max(waits) if waits else None,
            "flood_wait_total": max(totals) if totals else None,
            "flood_waits": sum(part["flood_waits"] for part in parts),
            "paced_waits": sum(part["paced_waits"] for part in parts),
            "limited": any(part["limited"] for part in parts),
            "limited_events": sum(part["limited_events"] for part in parts),
            "limited_ago": min(ago) if ago else None,
            # The width the bots are working at, added up: what the user set is per bot,
            # and what is happening is the total.
            "connections_allowed": sum(allowed) if allowed else None,
        }


EMPTY_SNAPSHOT = {
    "flood_wait_seconds": None,
    "flood_wait_total": None,
    "flood_waits": 0,
    "paced_waits": 0,
    "limited": False,
    "limited_events": 0,
    "limited_ago": None,
    "connections_allowed": None,
}


def snapshot_of(gate: FloodGate | None) -> dict:
    """The fields a progress frame carries, for a transfer with or without a gate."""
    return dict(EMPTY_SNAPSHOT) if gate is None else gate.snapshot()
