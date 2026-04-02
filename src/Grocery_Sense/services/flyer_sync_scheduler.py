"""
Grocery_Sense.services.flyer_sync_scheduler

Background scheduler that runs flyer sync automatically twice a week and
fires a callback after each sync so the UI can run the price-drop alert check.

Usage (in tk_main.py __init__):
    scheduler = FlyerSyncScheduler(on_sync_complete=self._on_flyer_sync_done)
    scheduler.start()

Manual trigger (from "Sync Flyers" button):
    scheduler.request_sync()
"""

from __future__ import annotations

import threading
from typing import Callable, Optional

from Grocery_Sense.services.flyer_sync_service import (
    FlyerSyncResult,
    SYNC_INTERVAL_DAYS,
    needs_sync,
    run_sync,
)

_INTERVAL_SECONDS = SYNC_INTERVAL_DAYS * 86400


class FlyerSyncScheduler:
    def __init__(
        self,
        on_sync_complete: Optional[Callable[[FlyerSyncResult], None]] = None,
    ) -> None:
        """
        on_sync_complete: called on the sync worker thread after every sync that
        actually ran (skipped syncs do not trigger it).
        The callback receives the FlyerSyncResult so the UI can log details.
        """
        self._on_sync_complete = on_sync_complete
        self._timer: Optional[threading.Timer] = None
        self._lock = threading.Lock()

    def start(self) -> None:
        """
        Call once on app start.
        Immediately checks if a sync is due; runs it if so.
        Then arms the recurring background timer.
        """
        threading.Thread(target=self._startup_check, daemon=True).start()

    def request_sync(self) -> None:
        """
        Force a sync now, bypassing the throttle.
        Safe to call from any thread (e.g. a button handler).
        Fires on_sync_complete when done.
        """
        threading.Thread(target=self._run_and_notify, kwargs={"force": True}, daemon=True).start()

    def stop(self) -> None:
        """Cancel the background timer (called on app exit if needed)."""
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _startup_check(self) -> None:
        if needs_sync():
            self._run_and_notify(force=False)
        self._schedule_next()

    def _periodic_check(self) -> None:
        if needs_sync():
            self._run_and_notify(force=False)
        self._schedule_next()

    def _schedule_next(self) -> None:
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
            self._timer = threading.Timer(_INTERVAL_SECONDS, self._periodic_check)
            self._timer.daemon = True
            self._timer.start()

    def _run_and_notify(self, *, force: bool) -> None:
        try:
            result = run_sync(force=force)
        except Exception as exc:
            result = FlyerSyncResult(errors=[str(exc)])

        if result.ran and self._on_sync_complete is not None:
            try:
                self._on_sync_complete(result)
            except Exception:
                pass
