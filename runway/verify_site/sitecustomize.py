"""Loaded by `make verify`'s demo servers and seed (runway/verify.py puts this folder on their PYTHONPATH), which run on
libfaketime's stopped clock. libfaketime 0.9.10 stops more than the wall clock: under it Python's timed waits on a lock
(threading.Event.wait, Condition.wait, a Semaphore's acquire with a timeout) return at once, so a loop around one spins a
CPU (the server's `_hang_up_late` thread kept one busy per server, which on a shared machine starved the browser and
made pages time out), and time.sleep fails with EINVAL (clock_nanosleep on the monotonic clock). select() and poll()
are untouched, so both are rebuilt on them here: the same waits, on the real monotonic clock. Only these demo processes
load it, and only while FROZEN_ENV is set; never Runway itself."""
from __future__ import annotations

import operator
import os
import select
import threading
import time
from typing import SupportsIndex

FROZEN_ENV = "RUNWAY_VERIFY_FROZEN_CLOCK"
POLL = 0.01   # how often a timed lock wait looks again: a notify is seen within 10 ms


def _sleep(secs: float | SupportsIndex) -> None:
    seconds = float(secs) if isinstance(secs, (int, float)) else float(operator.index(secs))
    if seconds < 0:
        raise ValueError("sleep length must be non-negative")
    end = time.monotonic() + seconds
    while (left := end - time.monotonic()) > 0:
        select.select([], [], [], left)


class _PollingLock:
    """A lock whose timed acquire polls (on select's clock, which libfaketime leaves alone) instead of a timed wait."""
    __slots__ = ("_lock",)

    def __init__(self) -> None:
        self._lock = _real_allocate_lock()

    def acquire(self, blocking: bool = True, timeout: float = -1) -> bool:
        if not blocking or timeout is None or timeout < 0:
            return self._lock.acquire(blocking)
        end = time.monotonic() + timeout
        while not self._lock.acquire(False):
            left = end - time.monotonic()
            if left <= 0:
                return False
            select.select([], [], [], min(left, POLL))
        return True

    def release(self) -> None:
        self._lock.release()

    def locked(self) -> bool:
        return self._lock.locked()

    __enter__ = acquire

    def __exit__(self, *_exc: object) -> None:
        self.release()


_real_allocate_lock = threading._allocate_lock   # type: ignore[attr-defined]


def install() -> None:
    """time.sleep, and the lock each Condition.wait (so Event.wait, Semaphore.acquire, queue.Queue.get) waits on."""
    time.sleep = _sleep
    threading._allocate_lock = _PollingLock   # type: ignore[attr-defined]


if os.environ.get(FROZEN_ENV) == "1":
    install()
