from __future__ import annotations

import os
import random
import signal
import threading

import pytest

from price_monitor.scheduler import (
    install_signal_handlers,
    jittered,
    parse_interval,
    watch_loop,
)


@pytest.mark.parametrize(
    ("text", "seconds"),
    [("90s", 90), ("30m", 1800), ("6h", 21600), ("1d", 86400), (" 1.5h ", 5400)],
)
def test_parse_interval(text: str, seconds: float) -> None:
    assert parse_interval(text) == seconds


@pytest.mark.parametrize("text", ["", "10", "m", "0m", "-5m", "5x", "1h30m"])
def test_parse_interval_rejects(text: str) -> None:
    with pytest.raises(ValueError, match="invalid interval"):
        parse_interval(text)


def test_jitter_bounds() -> None:
    rng = random.Random(3)
    values = [jittered(100.0, 0.1, rng) for _ in range(200)]
    assert min(values) >= 90.0
    assert max(values) <= 110.0
    assert len(set(values)) > 100
    assert jittered(100.0, 0.0, rng) == 100.0


def test_loop_runs_until_max_runs() -> None:
    stop = threading.Event()
    calls: list[int] = []
    runs = watch_loop(lambda: calls.append(1), interval=0.0, jitter=0.0, stop=stop, max_runs=3)
    assert runs == 3
    assert len(calls) == 3


def test_loop_survives_job_failures() -> None:
    stop = threading.Event()
    attempts: list[int] = []

    def job() -> None:
        attempts.append(1)
        raise RuntimeError("scrape exploded")

    assert watch_loop(job, interval=0.0, jitter=0.0, stop=stop, max_runs=2) == 2
    assert len(attempts) == 2


def test_stop_interrupts_the_wait() -> None:
    stop = threading.Event()
    timer = threading.Timer(0.05, stop.set)
    timer.start()
    runs = watch_loop(lambda: None, interval=3600.0, jitter=0.0, stop=stop)
    timer.join()
    assert runs == 1


def test_stop_set_before_start_runs_nothing() -> None:
    stop = threading.Event()
    stop.set()
    assert watch_loop(lambda: pytest.fail("must not run"), interval=1.0, jitter=0.0, stop=stop) == 0


@pytest.mark.parametrize("sig", [signal.SIGINT, signal.SIGTERM])
def test_signals_trigger_graceful_stop(sig: signal.Signals) -> None:
    stop = threading.Event()
    restore = install_signal_handlers(stop)
    try:
        os.kill(os.getpid(), sig)
        assert stop.wait(2.0)
    finally:
        restore()
    assert signal.getsignal(signal.SIGINT) is not None
