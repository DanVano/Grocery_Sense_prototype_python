"""
M3 — PriceHistoryService

Covers:
  - get_or_create_item idempotency (same canonical_name reused, not duplicated)
  - record_price_from_receipt/flyer/manual writing through to prices_repo
  - get_item_stats window filtering
  - classify_deal thresholds (great / good / typical / expensive / weak_data / no_data)
  - stats_for_item_by_store aggregation
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from Grocery_Sense.data.repositories.stores_repo import create_store
from Grocery_Sense.services.price_history_service import PriceHistoryService


@pytest.fixture
def svc() -> PriceHistoryService:
    return PriceHistoryService()


@pytest.fixture
def store(isolated_db):
    return create_store(name="Test Mart", is_favorite=True, priority=1)


def _iso_days_ago(n: int) -> str:
    return (date.today() - timedelta(days=n)).isoformat()


# ---------------------------------------------------------------------------
# Item creation
# ---------------------------------------------------------------------------


class TestItemLifecycle:
    def test_get_or_create_item_creates(self, svc, isolated_db):
        item = svc.get_or_create_item("chicken thighs", category="meat", default_unit="kg")
        assert item.id is not None
        assert item.canonical_name == "chicken thighs"
        assert item.category == "meat"

    def test_get_or_create_item_is_idempotent(self, svc, isolated_db):
        a = svc.get_or_create_item("pork loin")
        b = svc.get_or_create_item("pork loin")
        assert a.id == b.id

    def test_case_insensitive_lookup(self, svc, isolated_db):
        a = svc.get_or_create_item("Ground Beef")
        b = svc.get_or_create_item("ground beef")
        assert a.id == b.id

    def test_trims_whitespace(self, svc, isolated_db):
        a = svc.get_or_create_item("  eggs  ")
        b = svc.get_or_create_item("eggs")
        assert a.id == b.id


# ---------------------------------------------------------------------------
# Recording prices (source fan-out)
# ---------------------------------------------------------------------------


class TestRecordPrices:
    def test_record_from_receipt_inserts_row(self, svc, store):
        pid = svc.record_price_from_receipt(
            item_name="milk 2L",
            store_id=store.id,
            unit_price=4.99,
            unit="each",
        )
        assert pid > 0

        stats = svc.get_item_stats("milk 2L", window_days=30)
        assert stats is not None
        assert stats["sample_count"] == 1
        assert stats["avg_unit_price"] == 4.99

    def test_record_from_flyer_inserts_row(self, svc, store):
        svc.record_price_from_flyer(
            item_name="apples", store_id=store.id, unit_price=2.99, unit="lb"
        )
        stats = svc.get_item_stats("apples")
        assert stats["sample_count"] == 1

    def test_record_manual_inserts_row(self, svc, store):
        svc.record_manual_price(
            item_name="bread", store_id=store.id, unit_price=3.50, unit="each"
        )
        stats = svc.get_item_stats("bread")
        assert stats["sample_count"] == 1


# ---------------------------------------------------------------------------
# Stats & window filtering
# ---------------------------------------------------------------------------


class TestItemStats:
    def test_returns_none_when_no_item(self, svc, isolated_db):
        assert svc.get_item_stats("does not exist") is None

    def test_returns_none_when_item_has_no_prices_in_window(self, svc, store):
        svc.record_price_from_receipt(
            item_name="rare item",
            store_id=store.id,
            unit_price=5.00,
            unit="each",
            date_str=_iso_days_ago(400),
        )
        assert svc.get_item_stats("rare item", window_days=180) is None

    def test_aggregates_min_max_avg(self, svc, store):
        prices = [2.00, 3.00, 4.00, 5.00]
        for p in prices:
            svc.record_price_from_receipt(
                item_name="bananas",
                store_id=store.id,
                unit_price=p,
                unit="lb",
                date_str=_iso_days_ago(10),
            )
        stats = svc.get_item_stats("bananas", window_days=30)
        assert stats["sample_count"] == 4
        assert stats["min_unit_price"] == 2.00
        assert stats["max_unit_price"] == 5.00
        assert stats["avg_unit_price"] == pytest.approx(3.50)

    def test_window_excludes_old_prices(self, svc, store):
        # One recent, one ancient → window_days=30 sees only the recent one.
        svc.record_price_from_receipt(
            item_name="yogurt",
            store_id=store.id,
            unit_price=5.00,
            unit="each",
            date_str=_iso_days_ago(5),
        )
        svc.record_price_from_receipt(
            item_name="yogurt",
            store_id=store.id,
            unit_price=99.00,
            unit="each",
            date_str=_iso_days_ago(400),
        )
        stats = svc.get_item_stats("yogurt", window_days=30)
        assert stats["sample_count"] == 1
        assert stats["avg_unit_price"] == 5.00


# ---------------------------------------------------------------------------
# classify_deal thresholds
# ---------------------------------------------------------------------------


class TestClassifyDeal:
    def test_no_data_when_item_missing(self, svc, isolated_db):
        result = svc.classify_deal("ghost item", candidate_unit_price=1.99)
        assert result["classification"] == "no_data"
        assert result["has_history"] is False

    def test_no_data_when_no_prices_in_window(self, svc, store):
        # Item exists but all prices are outside the window.
        svc.record_price_from_receipt(
            item_name="tea",
            store_id=store.id,
            unit_price=3.00,
            unit="each",
            date_str=_iso_days_ago(400),
        )
        result = svc.classify_deal("tea", candidate_unit_price=2.50, window_days=180)
        assert result["classification"] == "no_data"

    def _seed_avg(self, svc, store, name: str, avg: float, n: int) -> None:
        for _ in range(n):
            svc.record_price_from_receipt(
                item_name=name,
                store_id=store.id,
                unit_price=avg,
                unit="each",
                date_str=_iso_days_ago(5),
            )

    def test_weak_data_when_samples_under_three(self, svc, store):
        self._seed_avg(svc, store, "oats", avg=5.00, n=2)
        result = svc.classify_deal("oats", candidate_unit_price=2.00)
        assert result["classification"] == "weak_data"

    def test_great_at_twenty_percent_below_avg(self, svc, store):
        self._seed_avg(svc, store, "coffee", avg=10.00, n=5)
        result = svc.classify_deal("coffee", candidate_unit_price=8.00)  # 20% off avg
        assert result["classification"] == "great"

    def test_good_between_ten_and_twenty_percent_below(self, svc, store):
        self._seed_avg(svc, store, "pasta", avg=10.00, n=5)
        result = svc.classify_deal("pasta", candidate_unit_price=8.90)  # 11% below
        assert result["classification"] == "good"

    def test_typical_within_ten_percent_band(self, svc, store):
        self._seed_avg(svc, store, "bread", avg=10.00, n=5)
        result = svc.classify_deal("bread", candidate_unit_price=9.50)  # 5% below
        assert result["classification"] == "typical"

    def test_expensive_when_ten_plus_above_avg(self, svc, store):
        self._seed_avg(svc, store, "olives", avg=10.00, n=5)
        result = svc.classify_deal("olives", candidate_unit_price=12.00)  # 20% over
        assert result["classification"] == "expensive"

    def test_percent_vs_avg_sign_convention(self, svc, store):
        """Positive percent_vs_avg = cheaper than average."""
        self._seed_avg(svc, store, "cereal", avg=10.00, n=5)
        cheap = svc.classify_deal("cereal", candidate_unit_price=8.00)
        assert cheap["percent_vs_avg"] > 0
        expensive = svc.classify_deal("cereal", candidate_unit_price=12.00)
        assert expensive["percent_vs_avg"] < 0


# ---------------------------------------------------------------------------
# Per-store stats
# ---------------------------------------------------------------------------


class TestStatsByStore:
    def test_per_store_scoping(self, svc, isolated_db):
        a = create_store(name="Store A")
        b = create_store(name="Store B")

        svc.record_price_from_receipt(
            item_name="eggs", store_id=a.id, unit_price=4.00, unit="dozen"
        )
        svc.record_price_from_receipt(
            item_name="eggs", store_id=b.id, unit_price=6.00, unit="dozen"
        )

        item = svc.get_or_create_item("eggs")
        stats_a = svc.stats_for_item_by_store(item.id, a.id, window_days=30)
        stats_b = svc.stats_for_item_by_store(item.id, b.id, window_days=30)

        assert stats_a["sample_count"] == 1
        assert stats_a["avg_price"] == 4.00
        assert stats_b["avg_price"] == 6.00

    def test_empty_returns_zero_count(self, svc, store):
        item = svc.get_or_create_item("never seen")
        stats = svc.stats_for_item_by_store(item.id, store.id, window_days=30)
        assert stats["sample_count"] == 0
        assert stats["avg_price"] is None
