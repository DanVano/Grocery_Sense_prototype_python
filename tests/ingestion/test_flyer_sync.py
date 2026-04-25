"""
M4 — flyer_sync_service

Covers:
  - needs_sync throttle (fresh meta / stale meta / missing meta)
  - run_sync skip paths (too_soon, no_stores)
  - FINDING: run_sync calls list_stores() with an unsupported kwarg,
    so any invocation that actually reaches the DB query TypeErrors today.
  - Happy path with FlippClient + list_stores monkeypatched — verifies
    deals are persisted and the meta file is updated.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pytest

from Grocery_Sense.data.repositories.flyers_repo import FlyersRepo
from Grocery_Sense.data.repositories.stores_repo import create_store
from Grocery_Sense.services import flyer_sync_service as sync_mod
from Grocery_Sense.services.flyer_sync_service import (
    FlyerSyncResult,
    needs_sync,
    run_sync,
)


@pytest.fixture
def tmp_meta(tmp_path, monkeypatch):
    """Redirect the sync meta file into a tmp path per test."""
    meta = tmp_path / "flyer_sync_meta.json"
    monkeypatch.setattr(sync_mod, "_META_FILE", meta)
    return meta


def _iso(dt_obj: dt.datetime) -> str:
    return dt_obj.isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# needs_sync — throttle logic
# ---------------------------------------------------------------------------


class TestNeedsSync:
    def test_true_when_no_meta_file(self, tmp_meta):
        assert needs_sync() is True

    def test_false_when_meta_recent(self, tmp_meta):
        now = dt.datetime.now(dt.timezone.utc)
        tmp_meta.write_text(
            json.dumps({"last_sync_utc": _iso(now)}), encoding="utf-8"
        )
        assert needs_sync() is False

    def test_true_when_meta_older_than_interval(self, tmp_meta):
        # Interval is 3.5 days — write a 5-day-old timestamp.
        stale = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=5)
        tmp_meta.write_text(
            json.dumps({"last_sync_utc": _iso(stale)}), encoding="utf-8"
        )
        assert needs_sync() is True

    def test_malformed_meta_counts_as_never_synced(self, tmp_meta):
        tmp_meta.write_text("{not json", encoding="utf-8")
        assert needs_sync() is True


# ---------------------------------------------------------------------------
# Skip paths that do not reach list_stores
# ---------------------------------------------------------------------------


class TestSkipPaths:
    def test_skipped_too_soon(self, tmp_meta, isolated_db):
        now = dt.datetime.now(dt.timezone.utc)
        tmp_meta.write_text(
            json.dumps({"last_sync_utc": _iso(now)}), encoding="utf-8"
        )
        result = run_sync(force=False)
        assert isinstance(result, FlyerSyncResult)
        assert result.skipped_reason == "too_soon"
        assert result.ran is False
        assert result.stores_synced == 0
        assert result.deals_inserted == 0


# ---------------------------------------------------------------------------
# Reachable skip paths (post-fix: list_stores is called without kwargs)
# ---------------------------------------------------------------------------


class TestNoStoresSkip:
    """
    Post-fix: run_sync no longer raises TypeError when it reaches the store
    query. With no stores registered, it short-circuits cleanly.
    """

    def test_force_sync_with_no_stores_skips(self, tmp_meta, isolated_db):
        result = run_sync(force=True)
        assert result.skipped_reason == "no_stores"
        assert result.ran is False
        assert result.stores_synced == 0

    def test_fresh_install_with_no_stores_skips(self, tmp_meta, isolated_db):
        # No meta file → needs_sync()=True → reaches list_stores → no_stores.
        result = run_sync(force=False)
        assert result.skipped_reason == "no_stores"

    def test_force_sync_with_real_stores_uses_flipp_stub(self, tmp_meta, isolated_db):
        """
        With the default FlippClient stub (returns []), real stores are still
        recorded as 'synced' attempts.
        """
        create_store(name="Real Mart")
        result = run_sync(force=True)
        assert result.ran is True
        assert result.stores_synced == 1
        assert result.deals_inserted == 0
        assert result.errors == []


# ---------------------------------------------------------------------------
# Happy path — list_stores + FlippClient monkeypatched
# ---------------------------------------------------------------------------


class TestHappyPath:
    """
    Exercises the pipeline under the assumption that list_stores accepts the
    kwarg (i.e. post-fix). We patch both the store lookup and the Flipp client
    so no real HTTP is involved.
    """

    @pytest.fixture
    def stub_stores(self, monkeypatch):
        from Grocery_Sense.domain.models import Store

        stores = [
            Store(id=1, name="Store A"),
            Store(id=2, name="Store B"),
        ]
        monkeypatch.setattr(
            sync_mod, "list_stores", lambda **kwargs: list(stores)
        )
        return stores

    @pytest.fixture
    def stub_flipp(self, monkeypatch):
        """
        Replace FlippClient so fetch_flyers_for_store returns different payloads
        per store, letting us prove per-store wiring.
        """
        calls: list = []

        class StubFlipp:
            def fetch_flyers_for_store(self, store_name, postal_code):
                calls.append((store_name, postal_code))
                if store_name == "Store A":
                    return [
                        {
                            "title": "Chicken Thighs",
                            "description": "Family Pack",
                            "price_text": "$5.99/kg",
                            "unit_price": 5.99,
                            "unit": "kg",
                            "valid_from": "2026-04-20",
                            "valid_to": "2026-04-27",
                        }
                    ]
                if store_name == "Store B":
                    return [
                        {
                            "title": "Apples",
                            "price_text": "2/$5",
                            "unit_price": 2.50,
                            "unit": "each",
                        },
                        {
                            "title": "Milk 2L",
                            "price_text": "$4.99",
                            "unit_price": 4.99,
                            "unit": "each",
                        },
                    ]
                return []

        monkeypatch.setattr(sync_mod, "FlippClient", StubFlipp)
        return calls

    def test_inserts_deals_and_updates_meta(
        self, tmp_meta, isolated_db, stub_stores, stub_flipp
    ):
        # Ensure FlyersRepo schema and stores table align before run_sync
        # attempts to store rows. isolated_db has already initialised the core
        # schema; FlyersRepo.ensure_schema is called inside insert_deals.
        FlyersRepo().ensure_schema()

        result = run_sync(force=True)

        assert result.skipped_reason is None
        assert result.ran is True
        assert result.stores_synced == 2
        assert result.deals_inserted == 3  # 1 from Store A + 2 from Store B
        assert result.errors == []
        assert tmp_meta.exists(), "meta file must be written on successful sync"

        # stub_flipp captured one call per store.
        assert len(stub_flipp) == 2
        assert {c[0] for c in stub_flipp} == {"Store A", "Store B"}

    def test_empty_flipp_results_still_count_as_synced(
        self, tmp_meta, isolated_db, stub_stores, monkeypatch
    ):
        class EmptyFlipp:
            def fetch_flyers_for_store(self, store_name, postal_code):
                return []

        monkeypatch.setattr(sync_mod, "FlippClient", EmptyFlipp)
        FlyersRepo().ensure_schema()

        result = run_sync(force=True)
        assert result.ran is True
        assert result.stores_synced == 2
        assert result.deals_inserted == 0

    def test_flipp_exception_is_captured_per_store(
        self, tmp_meta, isolated_db, stub_stores, monkeypatch
    ):
        class ExplodingFlipp:
            def fetch_flyers_for_store(self, store_name, postal_code):
                if store_name == "Store A":
                    raise RuntimeError("network down")
                return [
                    {
                        "title": "x",
                        "price_text": "$1.00",
                        "unit_price": 1.0,
                        "unit": "each",
                    }
                ]

        monkeypatch.setattr(sync_mod, "FlippClient", ExplodingFlipp)
        FlyersRepo().ensure_schema()

        result = run_sync(force=True)
        assert result.ran is True
        # Store A errored, Store B succeeded.
        assert result.stores_synced == 1
        assert len(result.errors) == 1
        assert "Store A" in result.errors[0]
        assert result.deals_inserted == 1


# ---------------------------------------------------------------------------
# Null / zero-price handling (cross-cutting finding)
# ---------------------------------------------------------------------------


class TestNullPriceDeals:
    """
    FlyersRepo.insert_deals stores whatever it is handed: deal_total and
    unit_price are both nullable in the schema, so a $0.00 or missing price
    flyer row survives as a deal with NULL / 0 price. Test documents this
    for anyone ranking deals by price — they must filter None explicitly.
    """

    def test_zero_and_missing_prices_persist(self, isolated_db):
        store = create_store(name="Null Mart")
        repo = FlyersRepo()
        batch_id = repo.create_flyer_batch(
            store_id=store.id, valid_from="2026-04-20", valid_to="2026-04-27"
        )
        count = repo.insert_deals(
            batch_id,
            store.id,
            [
                {"title": "Freebie", "price_text": "FREE", "deal_total": 0.0},
                {"title": "Missing", "price_text": ""},
            ],
        )
        assert count == 2
        deals = repo.list_deals_for_flyer(batch_id, apply_preferences=False)
        titles = {d["title"]: d for d in deals}
        assert titles["Freebie"]["deal_total"] == 0.0
        assert titles["Missing"]["deal_total"] is None
