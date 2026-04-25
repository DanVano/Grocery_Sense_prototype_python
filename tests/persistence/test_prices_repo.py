"""
M1 — prices_repo

Direct coverage of the lower-level repo helpers (higher-level services were
covered in M3). Focus on the advanced query helpers that PriceDropAlertService
depends on, plus edge cases for null / zero inputs.

Covers:
  - add_price_point round-trip (all nullable fields)
  - get_prices_for_item window + store filter
  - get_most_recent_price
  - get_price_stats_for_item
  - _median (internal) via behavior
  - list_unit_prices filters (receipt_only, sources, limit)
  - get_usual_unit_price + fallback basis
  - get_six_month_low_unit_price
  - get_last_seen_at_or_below
  - list_staple_item_ids thresholds
  - get_active_flyer_unit_price path
  - Batch helpers: prices_by_store, global, active_flyer, stats, usual, low, last_seen
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from Grocery_Sense.data.connection import get_connection
from Grocery_Sense.data.repositories.items_repo import create_item
from Grocery_Sense.data.repositories.prices_repo import (
    add_price_point,
    get_active_flyer_prices_batch,
    get_active_flyer_unit_price,
    get_best_current_quote_for_item_store,
    get_last_seen_at_or_below,
    get_last_seen_at_or_below_batch,
    get_most_recent_price,
    get_most_recent_prices_by_store_batch,
    get_most_recent_prices_global_batch,
    get_price_stats_batch,
    get_price_stats_for_item,
    get_prices_for_item,
    get_six_month_low_batch,
    get_six_month_low_unit_price,
    get_usual_unit_price,
    get_usual_unit_price_batch,
    list_staple_item_ids,
    list_unit_prices,
)
from Grocery_Sense.data.repositories.stores_repo import create_store


def _days_ago(n: int) -> str:
    return (date.today() - timedelta(days=n)).isoformat()


def _seed_active_flyer_price(store_id: int, item_id: int, *, unit_price: float) -> None:
    """Create a flyer_sources row + prices row tied to it (both with today's window)."""
    today = date.today().isoformat()
    vt = (date.today() + timedelta(days=7)).isoformat()
    with get_connection() as c:
        cur = c.execute(
            "INSERT INTO flyer_sources (provider, external_id, store_id, valid_from, valid_to) "
            "VALUES (?, ?, ?, ?, ?)",
            ("test", "fl-1", store_id, today, vt),
        )
        fs_id = int(cur.lastrowid)
        c.commit()
    add_price_point(
        item_id=item_id, store_id=store_id, unit_price=unit_price,
        unit="each", source="flyer", flyer_source_id=fs_id, date=today,
    )


# ---------------------------------------------------------------------------
# add_price_point + round-trip
# ---------------------------------------------------------------------------


class TestAddPricePoint:
    def test_round_trip_full_fields(self, isolated_db):
        item = create_item(canonical_name="eggs")
        store = create_store(name="A")
        pid = add_price_point(
            item_id=item.id,
            store_id=store.id,
            unit_price=4.99,
            unit="each",
            quantity=2.0,
            total_price=9.98,
            raw_name="EGGS DOZEN",
            confidence=5,
            source="receipt",
            date="2026-04-22",
        )
        assert pid > 0
        pts = get_prices_for_item(item.id)
        assert len(pts) == 1
        p = pts[0]
        assert p.unit_price == 4.99
        assert p.quantity == 2.0
        assert p.total_price == 9.98
        assert p.raw_name == "EGGS DOZEN"
        assert p.confidence == 5
        assert p.source == "receipt"

    def test_defaults_date_to_today(self, isolated_db):
        item = create_item(canonical_name="x")
        store = create_store(name="A")
        add_price_point(item_id=item.id, store_id=store.id, unit_price=1.0, unit="each")
        pts = get_prices_for_item(item.id, since_days=1)
        assert len(pts) == 1
        assert pts[0].date == date.today().isoformat()


# ---------------------------------------------------------------------------
# get_prices_for_item — window + store filter
# ---------------------------------------------------------------------------


class TestGetPricesForItem:
    def test_window_excludes_old(self, isolated_db):
        item = create_item(canonical_name="x")
        store = create_store(name="A")
        add_price_point(item_id=item.id, store_id=store.id, unit_price=1.0,
                        unit="each", source="receipt", date=_days_ago(5))
        add_price_point(item_id=item.id, store_id=store.id, unit_price=2.0,
                        unit="each", source="receipt", date=_days_ago(400))
        assert len(get_prices_for_item(item.id, since_days=180)) == 1

    def test_store_filter(self, isolated_db):
        item = create_item(canonical_name="x")
        a = create_store(name="A")
        b = create_store(name="B")
        add_price_point(item_id=item.id, store_id=a.id, unit_price=1.0,
                        unit="each", source="receipt", date=_days_ago(5))
        add_price_point(item_id=item.id, store_id=b.id, unit_price=2.0,
                        unit="each", source="receipt", date=_days_ago(5))
        assert len(get_prices_for_item(item.id, store_id=a.id)) == 1
        assert len(get_prices_for_item(item.id, store_id=b.id)) == 1

    def test_order_ascending_by_date(self, isolated_db):
        item = create_item(canonical_name="x")
        store = create_store(name="A")
        add_price_point(item_id=item.id, store_id=store.id, unit_price=1.0,
                        unit="each", source="receipt", date=_days_ago(1))
        add_price_point(item_id=item.id, store_id=store.id, unit_price=2.0,
                        unit="each", source="receipt", date=_days_ago(30))
        pts = get_prices_for_item(item.id)
        assert pts[0].unit_price == 2.0
        assert pts[-1].unit_price == 1.0


# ---------------------------------------------------------------------------
# get_most_recent_price
# ---------------------------------------------------------------------------


class TestGetMostRecentPrice:
    def test_returns_latest(self, isolated_db):
        item = create_item(canonical_name="x")
        store = create_store(name="A")
        add_price_point(item_id=item.id, store_id=store.id, unit_price=1.0,
                        unit="each", source="receipt", date=_days_ago(10))
        add_price_point(item_id=item.id, store_id=store.id, unit_price=2.0,
                        unit="each", source="receipt", date=_days_ago(1))
        assert get_most_recent_price(item.id).unit_price == 2.0

    def test_store_filter(self, isolated_db):
        item = create_item(canonical_name="x")
        a = create_store(name="A")
        b = create_store(name="B")
        add_price_point(item_id=item.id, store_id=a.id, unit_price=1.0,
                        unit="each", source="receipt", date=_days_ago(5))
        add_price_point(item_id=item.id, store_id=b.id, unit_price=99.0,
                        unit="each", source="receipt", date=_days_ago(1))
        assert get_most_recent_price(item.id, store_id=a.id).unit_price == 1.0

    def test_no_data_returns_none(self, isolated_db):
        item = create_item(canonical_name="x")
        assert get_most_recent_price(item.id) is None


# ---------------------------------------------------------------------------
# get_price_stats_for_item
# ---------------------------------------------------------------------------


class TestPriceStats:
    def test_computes_min_max_avg(self, isolated_db):
        item = create_item(canonical_name="x")
        store = create_store(name="A")
        for p in (2.0, 4.0, 6.0):
            add_price_point(item_id=item.id, store_id=store.id, unit_price=p,
                            unit="each", source="receipt", date=_days_ago(5))
        stats = get_price_stats_for_item(item.id)
        assert stats.count == 3
        assert stats.min_price == 2.0
        assert stats.max_price == 6.0
        assert stats.avg_price == pytest.approx(4.0)

    def test_no_data_returns_zero_count(self, isolated_db):
        item = create_item(canonical_name="x")
        stats = get_price_stats_for_item(item.id)
        assert stats.count == 0
        assert stats.min_price is None


# ---------------------------------------------------------------------------
# list_unit_prices
# ---------------------------------------------------------------------------


class TestListUnitPrices:
    def test_receipt_only_filter(self, isolated_db):
        item = create_item(canonical_name="x")
        store = create_store(name="A")
        add_price_point(item_id=item.id, store_id=store.id, unit_price=1.0,
                        unit="each", source="receipt", date=_days_ago(5))
        add_price_point(item_id=item.id, store_id=store.id, unit_price=99.0,
                        unit="each", source="manual", date=_days_ago(5))
        prices = list_unit_prices(item.id, receipt_only=True)
        assert prices == [1.0]

    def test_sources_filter(self, isolated_db):
        item = create_item(canonical_name="x")
        store = create_store(name="A")
        add_price_point(item_id=item.id, store_id=store.id, unit_price=1.0,
                        unit="each", source="receipt", date=_days_ago(5))
        add_price_point(item_id=item.id, store_id=store.id, unit_price=2.0,
                        unit="each", source="flyer", date=_days_ago(5))
        prices = list_unit_prices(item.id, sources=["flyer"])
        assert prices == [2.0]

    def test_limit(self, isolated_db):
        item = create_item(canonical_name="x")
        store = create_store(name="A")
        for v in (1.0, 2.0, 3.0, 4.0):
            add_price_point(item_id=item.id, store_id=store.id, unit_price=v,
                            unit="each", source="receipt", date=_days_ago(5))
        assert len(list_unit_prices(item.id, limit=2)) == 2


# ---------------------------------------------------------------------------
# get_usual_unit_price — median + fallback basis
# ---------------------------------------------------------------------------


class TestUsualUnitPrice:
    def test_receipt_median_when_enough_samples(self, isolated_db):
        item = create_item(canonical_name="x")
        store = create_store(name="A")
        for v in (2.0, 4.0, 6.0, 8.0):
            add_price_point(item_id=item.id, store_id=store.id, unit_price=v,
                            unit="each", source="receipt", date=_days_ago(5))
        usual, count, basis = get_usual_unit_price(item.id, min_samples=4)
        assert usual == pytest.approx(5.0)
        assert count == 4
        assert basis == "receipt_median"

    def test_falls_back_to_estimated(self, isolated_db):
        item = create_item(canonical_name="x")
        store = create_store(name="A")
        # Only 2 receipt samples → below min_samples=4; fallback includes manual.
        add_price_point(item_id=item.id, store_id=store.id, unit_price=3.0,
                        unit="each", source="receipt", date=_days_ago(5))
        add_price_point(item_id=item.id, store_id=store.id, unit_price=5.0,
                        unit="each", source="manual", date=_days_ago(5))
        usual, count, basis = get_usual_unit_price(item.id, min_samples=4)
        assert basis == "estimated_median"
        assert usual == pytest.approx(4.0)

    def test_no_data_returns_unknown(self, isolated_db):
        item = create_item(canonical_name="x")
        usual, count, basis = get_usual_unit_price(item.id)
        assert usual is None
        assert basis == "unknown"


# ---------------------------------------------------------------------------
# get_six_month_low_unit_price + get_last_seen_at_or_below
# ---------------------------------------------------------------------------


class TestSixMonthLowAndLastSeen:
    def test_six_month_low(self, isolated_db):
        item = create_item(canonical_name="x")
        store = create_store(name="A")
        for v in (4.0, 2.0, 6.0):
            add_price_point(item_id=item.id, store_id=store.id, unit_price=v,
                            unit="each", source="receipt", date=_days_ago(30))
        low, when = get_six_month_low_unit_price(item.id)
        assert low == 2.0
        assert when is not None

    def test_last_seen_at_or_below(self, isolated_db):
        item = create_item(canonical_name="x")
        store = create_store(name="A")
        add_price_point(item_id=item.id, store_id=store.id, unit_price=4.0,
                        unit="each", source="receipt", date=_days_ago(60))
        add_price_point(item_id=item.id, store_id=store.id, unit_price=5.0,
                        unit="each", source="receipt", date=_days_ago(5))
        seen = get_last_seen_at_or_below(item.id, price_ceiling=4.0)
        # The only row at/below 4.0 is 60 days ago.
        assert seen is not None
        assert seen.startswith(_days_ago(60))


# ---------------------------------------------------------------------------
# list_staple_item_ids
# ---------------------------------------------------------------------------


class TestListStapleItemIds:
    def test_flags_item_with_enough_line_count(self, isolated_db):
        item = create_item(canonical_name="eggs")
        store = create_store(name="A")
        for _ in range(4):
            add_price_point(item_id=item.id, store_id=store.id, unit_price=5.0,
                            unit="each", source="receipt", date=_days_ago(5))
        staples = list_staple_item_ids(min_line_items=4, min_distinct_receipts=3)
        assert any(iid == item.id for iid, _lc, _rc in staples)

    def test_excludes_item_below_thresholds(self, isolated_db):
        item = create_item(canonical_name="rare")
        store = create_store(name="A")
        add_price_point(item_id=item.id, store_id=store.id, unit_price=1.0,
                        unit="each", source="receipt", date=_days_ago(5))
        staples = list_staple_item_ids(min_line_items=4, min_distinct_receipts=3)
        assert not any(iid == item.id for iid, _, _ in staples)


# ---------------------------------------------------------------------------
# Active flyer helpers
# ---------------------------------------------------------------------------


class TestActiveFlyerHelpers:
    def test_get_active_flyer_unit_price(self, isolated_db):
        item = create_item(canonical_name="x")
        store = create_store(name="A")
        _seed_active_flyer_price(store.id, item.id, unit_price=3.5)
        assert get_active_flyer_unit_price(item.id, store.id) == 3.5

    def test_no_flyer_returns_none(self, isolated_db):
        item = create_item(canonical_name="x")
        store = create_store(name="A")
        assert get_active_flyer_unit_price(item.id, store.id) is None

    def test_get_best_current_quote_prefers_flyer(self, isolated_db):
        item = create_item(canonical_name="x")
        store = create_store(name="A")
        # History + flyer; flyer wins.
        add_price_point(item_id=item.id, store_id=store.id, unit_price=2.0,
                        unit="each", source="receipt", date=_days_ago(5))
        _seed_active_flyer_price(store.id, item.id, unit_price=3.5)
        quote = get_best_current_quote_for_item_store(item.id, store.id)
        assert quote == {"unit_price": 3.5, "source": "flyer"}

    def test_get_best_current_falls_back_to_latest(self, isolated_db):
        item = create_item(canonical_name="x")
        store = create_store(name="A")
        add_price_point(item_id=item.id, store_id=store.id, unit_price=2.0,
                        unit="each", source="receipt", date=_days_ago(5))
        quote = get_best_current_quote_for_item_store(item.id, store.id)
        assert quote["unit_price"] == 2.0
        assert quote["source"] in {"receipt", "latest"}


# ---------------------------------------------------------------------------
# Batch helpers
# ---------------------------------------------------------------------------


class TestBatchHelpers:
    def test_most_recent_prices_by_store_batch(self, isolated_db):
        store = create_store(name="A")
        i1 = create_item(canonical_name="a")
        i2 = create_item(canonical_name="b")
        add_price_point(item_id=i1.id, store_id=store.id, unit_price=1.0,
                        unit="each", source="receipt", date=_days_ago(5))
        add_price_point(item_id=i2.id, store_id=store.id, unit_price=2.0,
                        unit="each", source="receipt", date=_days_ago(5))
        m = get_most_recent_prices_by_store_batch([i1.id, i2.id], [store.id])
        assert m[(i1.id, store.id)].unit_price == 1.0
        assert m[(i2.id, store.id)].unit_price == 2.0

    def test_global_batch(self, isolated_db):
        store = create_store(name="A")
        item = create_item(canonical_name="x")
        add_price_point(item_id=item.id, store_id=store.id, unit_price=1.0,
                        unit="each", source="receipt", date=_days_ago(30))
        add_price_point(item_id=item.id, store_id=store.id, unit_price=2.0,
                        unit="each", source="receipt", date=_days_ago(1))
        m = get_most_recent_prices_global_batch([item.id])
        assert m[item.id].unit_price == 2.0

    def test_active_flyer_batch(self, isolated_db):
        item = create_item(canonical_name="x")
        store = create_store(name="A")
        _seed_active_flyer_price(store.id, item.id, unit_price=3.5)
        m = get_active_flyer_prices_batch([item.id], [store.id])
        assert m[(item.id, store.id)] == {"unit_price": 3.5, "source": "flyer"}

    def test_price_stats_batch(self, isolated_db):
        store = create_store(name="A")
        item = create_item(canonical_name="x")
        for v in (2.0, 4.0, 6.0):
            add_price_point(item_id=item.id, store_id=store.id, unit_price=v,
                            unit="each", source="receipt", date=_days_ago(5))
        m = get_price_stats_batch([item.id])
        stats = m[item.id]
        assert stats.min_price == 2.0
        assert stats.max_price == 6.0
        assert stats.avg_price == pytest.approx(4.0)
        assert stats.count == 3

    def test_usual_unit_price_batch(self, isolated_db):
        store = create_store(name="A")
        item = create_item(canonical_name="x")
        for v in (2.0, 4.0, 6.0, 8.0):
            add_price_point(item_id=item.id, store_id=store.id, unit_price=v,
                            unit="each", source="receipt", date=_days_ago(5))
        m = get_usual_unit_price_batch([item.id], min_samples=4)
        usual, count, basis = m[item.id]
        assert basis == "receipt_median"
        assert usual == pytest.approx(5.0)

    def test_six_month_low_batch(self, isolated_db):
        store = create_store(name="A")
        item = create_item(canonical_name="x")
        for v in (4.0, 2.0, 6.0):
            add_price_point(item_id=item.id, store_id=store.id, unit_price=v,
                            unit="each", source="receipt", date=_days_ago(10))
        m = get_six_month_low_batch([item.id])
        assert m[item.id][0] == 2.0

    def test_last_seen_at_or_below_batch(self, isolated_db):
        store = create_store(name="A")
        item = create_item(canonical_name="x")
        add_price_point(item_id=item.id, store_id=store.id, unit_price=4.0,
                        unit="each", source="receipt", date=_days_ago(60))
        m = get_last_seen_at_or_below_batch({item.id: 4.0})
        assert m[item.id] is not None

    def test_empty_inputs_return_empty_dicts(self, isolated_db):
        assert get_most_recent_prices_by_store_batch([], []) == {}
        assert get_most_recent_prices_global_batch([]) == {}
        assert get_active_flyer_prices_batch([], []) == {}
        assert get_price_stats_batch([]) == {}
        assert get_usual_unit_price_batch([]) == {}
        assert get_six_month_low_batch([]) == {}
        assert get_last_seen_at_or_below_batch({}) == {}
