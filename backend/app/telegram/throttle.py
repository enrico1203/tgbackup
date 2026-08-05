"""Bandwidth limits, as a token bucket the transfer takes from before moving bytes.

There is one place where the speed of a transfer can be decided, and it is the loop that
hands chunks over: the reader in `upload_slice`, and the part a `_DownloadSender` has just
brought back. Everything else, the number of connections, the size of a part, the
concurrency of the jobs, decides how fast it *can* go. So the limit lives here and is
taken there, and nothing else in the application has to know it exists.

The rate is not a number but a function returning one, consulted as the bucket refills.
That is what lets a job be limited between eight and eighteen and free at night without
restarting anything: the hour changes under a run that is already going, the next refill
reads the new ceiling, and the transfer changes speed. A rate of zero means no limit at
all, which is the common case and costs one comparison.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable

# How much unused allowance can pile up, in seconds of transfer. Without a ceiling a job
# that spent an hour scanning would arrive at the upload with an hour of credit and empty
# it at full speed. One second is enough to absorb the jitter between parts.
BURST_SECONDS = 1.0

# Longest single sleep. A limit changed while a transfer waits takes effect within this,
# and a rate of a few KB/s does not produce a sleep measured in minutes.
MAX_SLEEP = 1.0

RateProvider = Callable[[], float]


class RateLimiter:
    """A token bucket over bytes per second, shared by whoever passes the same instance."""

    def __init__(self, rate: RateProvider) -> None:
        self._rate = rate
        self._allowance = 0.0
        self._last = time.monotonic()
        self._lock = asyncio.Lock()

    async def take(self, amount: int) -> None:
        """Waits until `amount` bytes may be moved."""
        if amount <= 0:
            return
        while True:
            async with self._lock:
                rate = self._rate()
                if rate <= 0:
                    # No limit right now. The credit is dropped rather than kept: coming
                    # out of an unlimited hour with a full bucket would let the first
                    # second of the limited one run at any speed.
                    self._allowance = 0.0
                    self._last = time.monotonic()
                    return

                now = time.monotonic()
                # The ceiling has to clear one whole chunk, or a limit slower than a chunk
                # per second would never accumulate enough to let one through.
                ceiling = max(rate * BURST_SECONDS, float(amount))
                self._allowance = min(self._allowance + (now - self._last) * rate, ceiling)
                self._last = now

                if self._allowance >= amount:
                    self._allowance -= amount
                    return
                wait = (amount - self._allowance) / rate

            await asyncio.sleep(min(wait, MAX_SLEEP))


class ChainedLimiter:
    """Several limits at once, the tightest of which is what actually applies.

    A job with its own ceiling still has to fit inside the one set for the whole
    installation, and taking from both in turn is exactly that.
    """

    def __init__(self, *limiters: RateLimiter | ChainedLimiter | None) -> None:
        self._limiters = [limiter for limiter in limiters if limiter is not None]

    def __bool__(self) -> bool:
        return bool(self._limiters)

    async def take(self, amount: int) -> None:
        for limiter in self._limiters:
            await limiter.take(amount)


# The limit for the whole installation, in bytes per second, 0 for none. It is a module
# level value refreshed when the preference is saved and at startup, rather than a row
# read per chunk: this is consulted tens of times a second.
_global_rate = 0.0


def set_global_rate(rate: float) -> None:
    global _global_rate
    _global_rate = max(0.0, rate)


def global_rate() -> float:
    return _global_rate


# One bucket for the whole process, so two jobs sharing the installation limit divide it
# between them instead of each taking it in full.
global_limiter = RateLimiter(global_rate)


def limiter_for(rate: RateProvider) -> ChainedLimiter:
    """The limiter a transfer should use: its own ceiling under the global one."""
    return ChainedLimiter(RateLimiter(rate), global_limiter)


def installation_limiter() -> ChainedLimiter:
    """The global limit alone, for a transfer that belongs to no job.

    A restore and a browser download are asked for by hand, so no job ceiling applies to
    them, but they travel on the same line as everything else and the limit set for the
    installation is exactly the statement that the line has to be left usable.
    """
    return ChainedLimiter(global_limiter)
