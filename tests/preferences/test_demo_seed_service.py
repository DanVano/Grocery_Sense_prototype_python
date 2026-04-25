"""
M6 — demo_seed_service

Covers the deterministic demo dataset generator:
  - _demo_stores returns 3 plausible stores with expected flags
  - _demo_items returns 30 items spanning categories + units
  - seed_demo_data creates exactly 3 stores + 30 items + n_price_points rows
  - reset_first=True clears prior data (idempotent reseed)
  - reset_first=False accumulates stores/items where UNIQUE allows
  - Same seed produces deterministic output
  - Generated rows are valid for downstream services (prices_repo queries)
"""

from __future__ import annotations

import pytest

from Grocery_Sense.data.connection import get_connection
from Grocery_Sense.data.repositories.items_repo import list_items
from Grocery_Sense.data.repositories.prices_repo import get_prices_for_item
from Grocery_Sense.data.repositories.stores_repo import list_stores
from Grocery_Sense.services.demo_seed_service import (
    DemoItemSpec,
    _demo_items,
    _demo_stores,
    reset_all_demo_data,
    seed_demo_data,
)


# ---------------------------------------------------------------------------
# Pure-data helpers
# ---------------------------------------------------------------------------


class TestDemoCatalog:
    def test_three_stores(self):
        stores = _demo_stores()
        assert len(stores) == 3
        names = {s["name"] for s in stores}
        assert names == {"Walmart", "Save-On-Foods", "Real Canadian Superstore"}

    def test_exactly_one_favorite_store(self):
        stores = _demo_stores()
        favorites = [s for s in stores if s["is_favorite"]]
        assert len(favorites) == 1
        assert favorites[0]["name"] == "Walmart"

    def test_thirty_items(self):
        items = _demo_items()
        assert len(items) == 30
        assert all(isinstance(it, DemoItemSpec) for it in items)

    def test_items_cover_multiple_categories(self):
        categories = {it.category for it in _demo_items()}
        assert {"dairy", "meat", "pantry", "bakery", "produce"} <= categories

    def test_items_cover_both_units(self):
        units = {it.unit for it in _demo_items()}
        assert units == {"each", "kg"}

    def test_item_specs_have_positive_prices(self):
        for it in _demo_items():
            assert it.base_price > 0
            assert 0 < it.price_volatility <= 1.0


# ---------------------------------------------------------------------------
# seed_demo_data
# ---------------------------------------------------------------------------


class TestSeedDemoData:
    def test_creates_stores_items_and_prices(self, isolated_db):
        counts = seed_demo_data(reset_first=False, n_price_points=50)
        assert counts["stores"] == 3
        assert counts["items"] == 30
        assert counts["price_points"] == 50

        assert len(list_stores()) == 3
        assert len(list_items()) == 30

        with get_connection() as c:
            n_prices = c.execute("SELECT COUNT(*) FROM prices").fetchone()[0]
        assert n_prices == 50

    def test_reset_first_clears_prior_data(self, isolated_db):
        # First pass.
        seed_demo_data(reset_first=False, n_price_points=20)
        assert len(list_stores()) == 3

        # Second pass with reset_first=True: wipes and re-seeds.
        seed_demo_data(reset_first=True, n_price_points=20)
        assert len(list_stores()) == 3
        assert len(list_items()) == 30

    def test_without_reset_first_reseed_duplicates_stores(self, isolated_db):
        """
        FINDING: seed_demo_data(reset_first=False) calls create_store in a
        loop with no UNIQUE on stores.name — so a second reseed creates
        duplicate store rows. Locks in the current behaviour; fix
        candidates: use upsert logic or enforce UNIQUE.
        """
        seed_demo_data(reset_first=False, n_price_points=10)
        seed_demo_data(reset_first=False, n_price_points=10)
        # Second call creates 3 more store rows.
        assert len(list_stores()) == 6

    def test_without_reset_items_dedupe_by_canonical_name(self, isolated_db):
        """
        items.canonical_name IS UNIQUE; items_repo.create_item falls back to
        returning the existing row on UNIQUE violation. So a reseed without
        reset_first does NOT duplicate items.
        """
        seed_demo_data(reset_first=False, n_price_points=10)
        seed_demo_data(reset_first=False, n_price_points=10)
        assert len(list_items()) == 30

    def test_generated_prices_queryable_by_item(self, isolated_db):
        counts = seed_demo_data(reset_first=True, n_price_points=100)
        all_items = list_items()

        # Pick a few items and confirm the price history queries succeed.
        checked = 0
        for item in all_items[:5]:
            points = get_prices_for_item(item.id, since_days=120)
            if points:
                checked += 1
                for p in points:
                    assert p.unit_price > 0
                    assert p.unit in {"each", "kg"}
        # At least some items should have prices given 100 points / 30 items.
        assert checked >= 1

    def test_deterministic_with_same_seed(self, isolated_db):
        """
        Same random seed → identical (item_name, store_name, price, date)
        sequence. Comparison goes through names rather than raw ids because
        reset_all_demo_data does not reset sqlite_sequence, so autoincrement
        ids drift between runs.
        """
        def _snapshot():
            with get_connection() as c:
                rows = c.execute(
                    """
                    SELECT i.canonical_name, s.name, p.unit_price, p.date
                    FROM prices p
                    JOIN items i ON i.id = p.item_id
                    JOIN stores s ON s.id = p.store_id
                    ORDER BY p.id
                    """
                ).fetchall()
            return [tuple(r) for r in rows]

        seed_demo_data(reset_first=True, n_price_points=50, seed=42)
        first = _snapshot()

        seed_demo_data(reset_first=True, n_price_points=50, seed=42)
        second = _snapshot()

        assert first == second

    def test_different_seed_produces_different_data(self, isolated_db):
        seed_demo_data(reset_first=True, n_price_points=50, seed=1)
        with get_connection() as c:
            a = c.execute("SELECT SUM(unit_price) FROM prices").fetchone()[0]

        seed_demo_data(reset_first=True, n_price_points=50, seed=999)
        with get_connection() as c:
            b = c.execute("SELECT SUM(unit_price) FROM prices").fetchone()[0]

        assert a != b

    def test_walmart_bias_lower_than_save_on(self, isolated_db):
        """
        Store bias: Walmart=0.94, Save-On=1.03. Over a sufficiently large
        sample, Walmart should be meaningfully cheaper on shared items.
        """
        seed_demo_data(reset_first=True, n_price_points=1000, seed=42)

        with get_connection() as c:
            walmart_id = c.execute(
                "SELECT id FROM stores WHERE name = 'Walmart'"
            ).fetchone()[0]
            save_on_id = c.execute(
                "SELECT id FROM stores WHERE name = 'Save-On-Foods'"
            ).fetchone()[0]

            walmart_avg = c.execute(
                "SELECT AVG(unit_price) FROM prices WHERE store_id = ?",
                (walmart_id,),
            ).fetchone()[0]
            save_on_avg = c.execute(
                "SELECT AVG(unit_price) FROM prices WHERE store_id = ?",
                (save_on_id,),
            ).fetchone()[0]

        # Not a strict check — relies on biases but tolerates stochastic noise
        # (sample size 1000 / 3 stores ≈ 333 prices each).
        assert walmart_avg < save_on_avg


# ---------------------------------------------------------------------------
# reset_all_demo_data
# ---------------------------------------------------------------------------


class TestResetAllDemoData:
    def test_clears_all_seeded_tables(self, isolated_db):
        seed_demo_data(reset_first=False, n_price_points=20)
        reset_all_demo_data()

        with get_connection() as c:
            counts = tuple(
                c.execute(
                    "SELECT "
                    "(SELECT COUNT(*) FROM stores),"
                    "(SELECT COUNT(*) FROM items),"
                    "(SELECT COUNT(*) FROM prices),"
                    "(SELECT COUNT(*) FROM shopping_list),"
                    "(SELECT COUNT(*) FROM item_aliases),"
                    "(SELECT COUNT(*) FROM receipts),"
                    "(SELECT COUNT(*) FROM flyer_sources)"
                ).fetchone()
            )
        assert counts == (0, 0, 0, 0, 0, 0, 0)

    def test_reset_without_prior_seed_is_safe(self, isolated_db):
        reset_all_demo_data()  # must not raise on empty DB
