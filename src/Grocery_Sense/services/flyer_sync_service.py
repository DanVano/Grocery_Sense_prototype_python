"""
Grocery_Sense.services.flyer_sync_service

Downloads flyer deals for the user's stores and persists them into
flyer_batches + flyer_deals via FlyersRepo.

Sync is throttled to at most twice a week (every SYNC_INTERVAL_DAYS = 3.5 days).
Passing force=True bypasses the throttle (used for the manual Sync button).

The FlippClient is currently stubbed, so no real HTTP calls are made.
Wire up the real client when API credentials are available.
"""

from __future__ import annotations

import datetime
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from Grocery_Sense.data.repositories.flyers_repo import FlyersRepo
from Grocery_Sense.data.repositories.stores_repo import list_stores
from Grocery_Sense.integrations.flipp_client import FlippClient


SYNC_INTERVAL_DAYS = 3.5

_META_FILE = Path(__file__).resolve().parent.parent / "config" / "flyer_sync_meta.json"


# ---------------------------------------------------------------------------
# Sync metadata (last-run timestamp)
# ---------------------------------------------------------------------------

def _read_last_sync_utc() -> Optional[datetime.datetime]:
    if not _META_FILE.exists():
        return None
    try:
        data = json.loads(_META_FILE.read_text(encoding="utf-8"))
        ts = data.get("last_sync_utc")
        if not ts:
            return None
        return datetime.datetime.fromisoformat(ts)
    except Exception:
        return None


def _write_last_sync_utc(dt: datetime.datetime) -> None:
    _META_FILE.parent.mkdir(parents=True, exist_ok=True)
    _META_FILE.write_text(
        json.dumps({"last_sync_utc": dt.isoformat(timespec="seconds")}),
        encoding="utf-8",
    )


def needs_sync() -> bool:
    """True if no sync has ever run, or the last sync was more than SYNC_INTERVAL_DAYS ago."""
    last = _read_last_sync_utc()
    if last is None:
        return True
    elapsed = datetime.datetime.utcnow() - last
    return elapsed.total_seconds() >= SYNC_INTERVAL_DAYS * 86400


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass
class FlyerSyncResult:
    stores_synced: int = 0
    deals_inserted: int = 0
    skipped_reason: Optional[str] = None  # set when sync did not run
    errors: List[str] = field(default_factory=list)

    @property
    def ran(self) -> bool:
        return self.skipped_reason is None


# ---------------------------------------------------------------------------
# Postal code helper
# ---------------------------------------------------------------------------

def _get_postal_code() -> str:
    try:
        from Grocery_Sense.config.config_store import load_config
        return load_config().postal_code or ""
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# Public sync entry point
# ---------------------------------------------------------------------------

def run_sync(*, force: bool = False) -> FlyerSyncResult:
    """
    Sync flyer deals for every store in the user's store list.

    - force=True bypasses the twice-weekly throttle.
    - Each successful store sync creates one flyer_batches row and N flyer_deals rows.
    - Validity window defaults to today + 7 days when the API does not return dates
      (the stub never returns dates, so this applies to all stub runs).
    - Updates last-sync timestamp on completion even if some stores had errors.
    """
    if not force and not needs_sync():
        return FlyerSyncResult(skipped_reason="too_soon")

    stores = list_stores(include_disabled=False)
    if not stores:
        return FlyerSyncResult(skipped_reason="no_stores")

    result = FlyerSyncResult()
    postal_code = _get_postal_code()
    client = FlippClient()
    repo = FlyersRepo()

    today = datetime.date.today()
    default_valid_from = today.isoformat()
    default_valid_to = (today + datetime.timedelta(days=7)).isoformat()

    for store in stores:
        store_id = int(store.id)
        store_name = str(store.name or "")

        try:
            raw_deals = client.fetch_flyers_for_store(store_name, postal_code)
        except Exception as exc:
            result.errors.append(f"{store_name}: fetch failed — {exc}")
            continue

        # Stub returns []; a real client returns deal dicts.
        # We always record the store as "synced" (attempted), even if no deals returned.
        result.stores_synced += 1

        if not raw_deals:
            continue

        try:
            # Derive validity window from the first deal if available; else use default.
            valid_from = raw_deals[0].get("valid_from") or default_valid_from
            valid_to = raw_deals[0].get("valid_to") or default_valid_to

            flyer_id = repo.create_flyer_batch(
                store_id=store_id,
                valid_from=valid_from,
                valid_to=valid_to,
                source_type="flipp_api",
                source_ref=f"auto_sync_{today.isoformat()}",
                note=f"Auto-sync {today.isoformat()}",
                status="active",
            )
            count = repo.insert_deals(flyer_id, store_id, raw_deals)
            result.deals_inserted += count
        except Exception as exc:
            result.errors.append(f"{store_name}: DB insert failed — {exc}")

    _write_last_sync_utc(datetime.datetime.utcnow())
    return result
