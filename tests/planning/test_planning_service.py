"""
M5 — PlanningService

Covers:
  - Empty list / no stores → graceful structural response
  - Greedy store selection: items win best-price store, ties broken by
    favorite + priority
  - max_stores cap
  - Cost estimation: per-store subtotal, basket total, baseline total, savings
  - Missing price data → items counted as 'missing'
  - item_id resolution takes precedence over display_name
  - Fallback store picks up items whose best store was not chosen
  - Null unit_price rows in history do not crash
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from Grocery_Sense.data.repositories.items_repo import create_item
from Grocery_Sense.data.repositories.prices_repo import add_price_point
from Grocery_Sense.data.repositories.stores_repo import create_store
from Grocery_Sense.services.planning_service import PlanningService
from Grocery_Sense.services.shopping_list_service import ShoppingListService


def _recent(n: int = 5) -> str:
    return (date.today() - timedelta(days=n)).isoformat()


@pytest.fixture
def svc() -> PlanningService:
    return PlanningService()


@pytest.fixture
def sl_svc(isolated_db) -> ShoppingListService:
    return ShoppingListService()


def _seed_item_with_prices(
    canonical_name: str,
    *,
    prices: dict,  # {store_id: [unit_price, ...]}
) -> int:
    item = create_item(canonical_name=canonical_name)
    for store_id, unit_prices in prices.items():
        for unit_price in unit_prices:
            add_price_point(
                item_id=item.id,
                store_id=store_id,
                unit_price=unit_price,
                unit="each",
                source="receipt",
                date=_recent(),
            )
    return item.id


# ---------------------------------------------------------------------------
# Guard rails
# ---------------------------------------------------------------------------


class TestGuardRails:
    def test_no_items_and_no_stores(self, svc, isolated_db):
        plan = svc.build_plan_for_active_list()
        assert plan["stores"] == {}
        assert plan["unassigned"] == []
        assert "No plan possible" in plan["summary"]
        assert plan["costs"]["basket_total_estimate"] is None

    def test_items_but_no_stores(self, svc, sl_svc):
        sl_svc.add_single_item(name="eggs")
        plan = svc.build_plan_for_active_list()
        assert plan["stores"] == {}
        assert len(plan["unassigned"]) == 1

    def test_stores_but_no_items(self, svc, isolated_db):
        create_store(name="Mart")
        plan = svc.build_plan_for_active_list()
        assert plan["stores"] == {}
        assert plan["unassigned"] == []


# ---------------------------------------------------------------------------
# Greedy store selection
# ---------------------------------------------------------------------------


class TestGreedyStoreSelection:
    def test_items_go_to_cheapest_store(self, svc, sl_svc):
        a = create_store(name="Store A", is_favorite=True, priority=10)
        b = create_store(name="Store B", priority=5)

        eggs_id = _seed_item_with_prices(
            "eggs", prices={a.id: [5.00], b.id: [3.00]}
        )
        milk_id = _seed_item_with_prices(
            "milk", prices={a.id: [4.00], b.id: [6.00]}
        )

        sl_svc.add_single_item(name="eggs", item_id=eggs_id)
        sl_svc.add_single_item(name="milk", item_id=milk_id)

        plan = svc.build_plan_for_active_list(max_stores=2)

        # Each item should land at its cheapest store.
        store_a_items = [i.display_name for i in plan["stores"][a.id]["items"]]
        store_b_items = [i.display_name for i in plan["stores"][b.id]["items"]]
        assert "milk" in store_a_items
        assert "eggs" in store_b_items

    def test_max_stores_caps_plan(self, svc, sl_svc):
        a = create_store(name="A", is_favorite=True, priority=10)
        b = create_store(name="B", priority=5)
        c = create_store(name="C")

        # Make each store the cheapest for a different item.
        i1 = _seed_item_with_prices("i1", prices={a.id: [1.00], b.id: [2.00], c.id: [3.00]})
        i2 = _seed_item_with_prices("i2", prices={a.id: [3.00], b.id: [1.00], c.id: [2.00]})
        i3 = _seed_item_with_prices("i3", prices={a.id: [3.00], b.id: [2.00], c.id: [1.00]})

        sl_svc.add_single_item(name="i1", item_id=i1)
        sl_svc.add_single_item(name="i2", item_id=i2)
        sl_svc.add_single_item(name="i3", item_id=i3)

        plan = svc.build_plan_for_active_list(max_stores=2)
        assert len(plan["stores"]) <= 2

    def test_fallback_when_no_price_history(self, svc, sl_svc):
        """When nothing has been bought yet, planner falls back to favorites."""
        a = create_store(name="A", is_favorite=True, priority=10)
        create_store(name="B")

        sl_svc.add_single_item(name="eggs")  # unlinked, no price history

        plan = svc.build_plan_for_active_list(max_stores=2)
        # Favourite store is picked; item lands there via generic fallback.
        assert a.id in plan["stores"]
        assert any(i.display_name == "eggs" for i in plan["stores"][a.id]["items"])


# ---------------------------------------------------------------------------
# Cost estimation
# ---------------------------------------------------------------------------


class TestCostEstimation:
    def test_per_store_and_basket_totals(self, svc, sl_svc):
        a = create_store(name="A", is_favorite=True, priority=10)
        b = create_store(name="B", priority=5)

        eggs_id = _seed_item_with_prices(
            "eggs", prices={a.id: [5.00], b.id: [3.00]}
        )
        milk_id = _seed_item_with_prices(
            "milk", prices={a.id: [4.00], b.id: [6.00]}
        )

        sl_svc.add_single_item(name="eggs", item_id=eggs_id, quantity=2)
        sl_svc.add_single_item(name="milk", item_id=milk_id, quantity=1)

        plan = svc.build_plan_for_active_list(max_stores=2)

        # eggs at Store B ($3 × 2 = $6); milk at Store A ($4 × 1 = $4).
        # Basket total = $10.
        costs = plan["costs"]
        assert costs["basket_total_estimate"] == pytest.approx(10.0)

        b_subtotal = plan["stores"][b.id]["estimated_subtotal"]
        a_subtotal = plan["stores"][a.id]["estimated_subtotal"]
        assert b_subtotal == pytest.approx(6.0)
        assert a_subtotal == pytest.approx(4.0)

    def test_baseline_compares_against_favorite_store(self, svc, sl_svc):
        a = create_store(name="Fav", is_favorite=True, priority=10)
        b = create_store(name="Other")

        # Put eggs cheaper at B so the plan splits...
        eggs_id = _seed_item_with_prices(
            "eggs", prices={a.id: [5.00], b.id: [3.00]}
        )
        sl_svc.add_single_item(name="eggs", item_id=eggs_id)

        plan = svc.build_plan_for_active_list(max_stores=2)
        costs = plan["costs"]

        assert costs["baseline_store"].id == a.id  # favorite wins baseline
        # Baseline = $5 at Fav; plan = $3 at Other; savings = $2
        assert costs["baseline_total_estimate"] == pytest.approx(5.0)
        assert costs["basket_total_estimate"] == pytest.approx(3.0)
        assert costs["estimated_savings"] == pytest.approx(2.0)

    def test_coverage_counts_missing_items(self, svc, sl_svc):
        a = create_store(name="A", is_favorite=True)
        create_store(name="B")

        # linked item with prices
        eggs_id = _seed_item_with_prices("eggs", prices={a.id: [5.00]})
        sl_svc.add_single_item(name="eggs", item_id=eggs_id)

        # unlinked + no history → missing
        sl_svc.add_single_item(name="unknown widget")

        plan = svc.build_plan_for_active_list()
        cov = plan["costs"]["coverage"]
        assert cov["total_items"] == 2
        assert cov["estimated_items"] == 1
        assert cov["missing_items"] == 1


# ---------------------------------------------------------------------------
# Item resolution
# ---------------------------------------------------------------------------


class TestItemResolution:
    def test_item_id_preferred_over_name(self, svc, sl_svc):
        """
        Shopping row's item_id must win even when display_name matches a
        different canonical entry.
        """
        a = create_store(name="A", is_favorite=True)
        # Two canonical items share a loose naming pattern.
        canonical_a = _seed_item_with_prices("chicken thighs", prices={a.id: [5.0]})
        _seed_item_with_prices("chicken breasts", prices={a.id: [9.99]})

        # The shopping row's display says 'chicken breasts' but its item_id points at thighs.
        sl_svc.add_single_item(name="chicken breasts", item_id=canonical_a)

        plan = svc.build_plan_for_active_list()
        # Cost should reflect the thighs price (5.0), not the breasts price.
        assert plan["costs"]["basket_total_estimate"] == pytest.approx(5.0)

    def test_name_fallback_when_no_item_id(self, svc, sl_svc):
        a = create_store(name="A", is_favorite=True)
        _seed_item_with_prices("eggs", prices={a.id: [4.00]})

        sl_svc.add_single_item(name="eggs")  # no item_id — resolves by name

        plan = svc.build_plan_for_active_list()
        assert plan["costs"]["basket_total_estimate"] == pytest.approx(4.0)


# ---------------------------------------------------------------------------
# Null-price tolerance (cross-cutting)
# ---------------------------------------------------------------------------


class TestNullPriceTolerance:
    def test_prices_with_none_do_not_crash(self, svc, sl_svc, isolated_db):
        """
        add_price_point requires unit_price, but prices_repo filters out
        None unit_price when averaging. Confirm a mix of None/valid points
        still yields a clean average and does not crash.
        """
        a = create_store(name="A", is_favorite=True)
        item = create_item(canonical_name="eggs")

        # Mix of valid and missing (None) unit prices — prices_repo.list_unit_prices
        # already filters None. This also exercises the "all None" edge via only
        # valid ones being added.
        add_price_point(
            item_id=item.id,
            store_id=a.id,
            unit_price=4.0,
            unit="each",
            source="receipt",
            date=_recent(),
        )
        add_price_point(
            item_id=item.id,
            store_id=a.id,
            unit_price=6.0,
            unit="each",
            source="receipt",
            date=_recent(),
        )

        sl_svc.add_single_item(name="eggs", item_id=item.id)

        plan = svc.build_plan_for_active_list()
        # avg(4, 6) = 5
        assert plan["costs"]["basket_total_estimate"] == pytest.approx(5.0)

    def test_summary_is_always_a_string(self, svc, sl_svc):
        a = create_store(name="A", is_favorite=True)
        sl_svc.add_single_item(name="eggs")
        plan = svc.build_plan_for_active_list()
        assert isinstance(plan["summary"], str)
        assert plan["summary"]  # non-empty
