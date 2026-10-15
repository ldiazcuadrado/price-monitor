"""Interval loop behind the ``watch`` command."""

from __future__ import annotations

import logging
import random
import re
import signal
import threading
from collections.abc import Callable
from types import FrameType

log = logging.getLogger(__name__)

_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400}
_INTERVAL_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([smhd])\s*$")


def parse_interval(text: str) -> float:
    """'90s', '30m', '6h' or '1d' -> seconds."""
    match = _INTERVAL_RE.match(text)
    if not match or float(match.group(1)) <= 0:
        raise ValueError(f"invalid interval {text!r}; use e.g. 30m, 6h or 1d")
    return float(match.group(1)) * _UNITS[match.group(2)]


def jittered(interval: float, jitter: float, rng: random.Random) -> float:
    """``interval`` +/- ``jitter`` (a fraction, e.g. 0.1 for 10%)."""
    return interval * (1 + rng.uniform(-jitter, jitter))


def install_signal_handlers(stop: threading.Event) -> Callable[[], None]:
    """Make SIGINT/SIGTERM set ``stop``; returns a function restoring the old handlers."""
    previous = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}

    def handler(signum: int, _frame: FrameType | None) -> None:
        log.info("shutdown requested", extra={"signal": signal.Signals(signum).name})
        stop.set()

    for sig in previous:
        signal.signal(sig, handler)

    def restore() -> None:
        for sig, old in previous.items():
            signal.signal(sig, old)

    return restore


def watch_loop(
    job: Callable[[], None],
    *,
    interval: float,
    jitter: float,
    stop: threading.Event,
    max_runs: int | None = None,
    rng: random.Random | None = None,
) -> int:
    """Run ``job`` repeatedly until ``stop`` is set. Job failures are logged, not fatal.

    Returns the number of completed runs. The wait between runs is interruptible, so a
    signal ends the loop promptly; a job already in progress finishes first.
    """
    rng = rng or random.Random()
    runs = 0
    while not stop.is_set():
        try:
            job()
        except Exception:
            log.exception("watch run failed")
        runs += 1
        if max_runs is not None and runs >= max_runs:
            break
        delay = jittered(interval, jitter, rng)
        log.info("next run scheduled", extra={"in_seconds": round(delay)})
        stop.wait(delay)
    return runs
