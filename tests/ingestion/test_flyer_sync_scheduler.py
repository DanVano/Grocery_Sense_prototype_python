"""
M4 — FlyerSyncScheduler (thin thread wrapper)

Scope is deliberately narrow — the scheduler itself is orchestration. Coverage:
  - request_sync fires the callback on successful runs
  - request_sync captures run_sync exceptions and still fires the callback
  - start runs an initial sync when needs_sync() is true
  - start skips the initial sync when needs_sync() is false
  - stop cancels an armed timer

Long sleeps are avoided by stubbing run_sync / needs_sync / threading.Timer.
"""

from __future__ import annotations

import threading
import time

import pytest

from Grocery_Sense.services import flyer_sync_scheduler as sched_mod
from Grocery_Sense.services.flyer_sync_scheduler import FlyerSyncScheduler
from Grocery_Sense.services.flyer_sync_service import FlyerSyncResult


class _NoopTimer:
    """Drop-in for threading.Timer that never fires."""

    def __init__(self, interval, function, args=None, kwargs=None):
        self.interval = interval
        self.function = function
        self.daemon = False
        self._cancelled = False
        self._started = False

    def start(self):
        self._started = True

    def cancel(self):
        self._cancelled = True


@pytest.fixture
def silence_timer(monkeypatch):
    """Prevent real 3.5-day timers from being armed."""
    monkeypatch.setattr(sched_mod.threading, "Timer", _NoopTimer)


@pytest.fixture
def stub_run_sync(monkeypatch):
    """Default stub: run_sync returns a successful FlyerSyncResult."""
    calls: list = []

    def fake(*, force: bool) -> FlyerSyncResult:
        calls.append(force)
        return FlyerSyncResult(stores_synced=2, deals_inserted=3)

    monkeypatch.setattr(sched_mod, "run_sync", fake)
    return calls


def _wait_for(predicate, timeout: float = 2.0, interval: float = 0.02) -> bool:
    """Poll predicate until True or timeout. Returns whether it succeeded."""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(interval)
    return False


# ---------------------------------------------------------------------------
# request_sync
# ---------------------------------------------------------------------------


class TestRequestSync:
    def test_fires_callback_with_result(self, silence_timer, stub_run_sync):
        received: list = []
        scheduler = FlyerSyncScheduler(on_sync_complete=received.append)
        scheduler.request_sync()

        assert _wait_for(lambda: len(received) == 1)
        result = received[0]
        assert isinstance(result, FlyerSyncResult)
        assert result.ran is True
        assert result.stores_synced == 2
        assert stub_run_sync == [True]  # forced

    def test_callback_still_fires_when_run_sync_raises(
        self, silence_timer, monkeypatch
    ):
        def boom(*, force: bool) -> FlyerSyncResult:
            raise RuntimeError("network down")

        monkeypatch.setattr(sched_mod, "run_sync", boom)

        received: list = []
        scheduler = FlyerSyncScheduler(on_sync_complete=received.append)
        scheduler.request_sync()

        assert _wait_for(lambda: len(received) == 1)
        assert received[0].errors == ["network down"]

    def test_no_callback_does_not_crash(self, silence_timer, stub_run_sync):
        scheduler = FlyerSyncScheduler(on_sync_complete=None)
        scheduler.request_sync()
        assert _wait_for(lambda: stub_run_sync == [True])


# ---------------------------------------------------------------------------
# start — conditional initial sync + timer arming
# ---------------------------------------------------------------------------


class TestStart:
    def test_runs_initial_sync_when_needed(
        self, silence_timer, stub_run_sync, monkeypatch
    ):
        monkeypatch.setattr(sched_mod, "needs_sync", lambda: True)
        received: list = []
        scheduler = FlyerSyncScheduler(on_sync_complete=received.append)
        scheduler.start()

        assert _wait_for(lambda: len(received) == 1)
        assert stub_run_sync == [False]  # initial sync is not forced

    def test_skips_initial_sync_when_not_needed(
        self, silence_timer, stub_run_sync, monkeypatch
    ):
        monkeypatch.setattr(sched_mod, "needs_sync", lambda: False)
        received: list = []
        scheduler = FlyerSyncScheduler(on_sync_complete=received.append)
        scheduler.start()

        # Give the startup thread a beat to settle; nothing should happen.
        assert not _wait_for(lambda: bool(received), timeout=0.3)
        assert stub_run_sync == []

    def test_arms_a_timer(self, silence_timer, stub_run_sync, monkeypatch):
        monkeypatch.setattr(sched_mod, "needs_sync", lambda: False)
        scheduler = FlyerSyncScheduler()
        scheduler.start()

        assert _wait_for(lambda: scheduler._timer is not None)
        assert isinstance(scheduler._timer, _NoopTimer)
        assert scheduler._timer._started is True


# ---------------------------------------------------------------------------
# stop — cancels the armed timer
# ---------------------------------------------------------------------------


class TestStop:
    def test_stop_cancels_timer(self, silence_timer, stub_run_sync, monkeypatch):
        monkeypatch.setattr(sched_mod, "needs_sync", lambda: False)
        scheduler = FlyerSyncScheduler()
        scheduler.start()
        assert _wait_for(lambda: scheduler._timer is not None)

        timer = scheduler._timer
        scheduler.stop()

        assert timer._cancelled is True
        assert scheduler._timer is None

    def test_stop_is_safe_when_nothing_armed(self, silence_timer):
        scheduler = FlyerSyncScheduler()
        # No start() → no timer → stop should not raise.
        scheduler.stop()
