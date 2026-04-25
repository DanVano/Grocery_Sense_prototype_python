"""
M5 — End-to-end integration smoke

Consolidates the narrative from the legacy top-level smoke tests
(test_backend.py, test_repo_usage.py, test_shopping_list_service.py,
test_weekly_planner.py, test_meal_suggestion_service.py) into a single
assertion-based integration test that walks the happy path:

  1. DB initialised + store + canonical items
  2. Seed price history so the planner has signal
  3. Populate a shopping list
  4. Run PlanningService → non-empty structural plan
  5. Run BasketOptimizerService → non-empty result with savings lines
  6. Check-off + clear flow soft-deletes items correctly

This is the one integration test that exists; the unit-level coverage for
each service already lives in sibling files.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pytest

from Grocery_Sense.data.repositories.items_repo import create_item
from Grocery_Sense.data.repositories.prices_repo import add_price_point
from Grocery_Sense.data.repositories.stores_repo import create_store, list_stores
from Grocery_Sense.recipes import recipe_engine as re_mod
from Grocery_Sense.recipes.recipe_engine import RecipeEngine
from Grocery_Sense.services.basket_optimizer_service import BasketOptimizerService
from Grocery_Sense.services.meal_suggestion_service import MealSuggestionService
from Grocery_Sense.services.planning_service import PlanningService
from Grocery_Sense.services.shopping_list_service import ShoppingListService
from Grocery_Sense.services.weekly_planner_service import (
    WeeklyPlannerService,
    summarize_weekly_plan,
)


FIXTURE_PATH = (
    Path(__file__).resolve().parent.parent / "fixtures" / "recipes_sample.json"
)


@pytest.fixture(autouse=True)
def _patch_recipe_singleton(monkeypatch):
    monkeypatch.setattr(re_mod, "_default_engine", RecipeEngine(recipes_path=FIXTURE_PATH))


def _recent(n: int = 3) -> str:
    return (date.today() - timedelta(days=n)).isoformat()


def test_full_basket_happy_path(isolated_db):
    # --- 1) Stores + canonical items ---
    a = create_store(name="Store A", is_favorite=True, priority=10, flipp_store_id="A001")
    b = create_store(name="Store B", priority=5, flipp_store_id="B001")

    eggs = create_item(canonical_name="eggs", category="dairy", default_unit="each")
    milk = create_item(canonical_name="milk", category="dairy", default_unit="each")
    apples = create_item(canonical_name="apples", category="produce")

    # --- 2) Price history driving the optimizer ---
    # eggs cheaper at A, milk cheaper at B, apples only at A.
    for unit_price, store in ((3.00, a), (4.00, b)):
        add_price_point(item_id=eggs.id, store_id=store.id, unit_price=unit_price,
                        unit="each", source="receipt", date=_recent(30))
    for unit_price, store in ((5.00, a), (3.50, b)):
        add_price_point(item_id=milk.id, store_id=store.id, unit_price=unit_price,
                        unit="each", source="receipt", date=_recent(30))
    add_price_point(item_id=apples.id, store_id=a.id, unit_price=2.00,
                    unit="each", source="receipt", date=_recent(30))

    # --- 3) Shopping list ---
    sl = ShoppingListService()
    sl.add_items_from_text("eggs, milk, apples", planned_store_id=a.id)
    items = sl.get_active_items()
    # Bind each list row to its canonical item_id so the basket optimizer
    # can price them.
    by_name = {r.display_name: r for r in items}
    for name, item in (("eggs", eggs), ("milk", milk), ("apples", apples)):
        from Grocery_Sense.data.repositories import shopping_list_repo as sl_repo
        with __import__("Grocery_Sense.data.connection", fromlist=["get_connection"]).get_connection() as conn:
            conn.execute(
                "UPDATE shopping_list SET item_id = ? WHERE id = ?",
                (item.id, by_name[name].id),
            )
            conn.commit()

    # --- 4) Planning service ---
    plan = PlanningService().build_plan_for_active_list(max_stores=2)
    assert plan["stores"], "planner should pick at least one store"
    assert plan["costs"]["basket_total_estimate"] is not None
    assert plan["costs"]["coverage"]["total_items"] == 3

    # --- 5) Basket optimizer ---
    result = BasketOptimizerService().optimize(mode="two_store")
    assert result.stores, "optimizer should produce at least one store plan"
    assert result.basket_total_estimated > 0

    # Every basket item ended up assigned to exactly one store plan.
    all_assigned = sum(len(sp.items) for sp in result.stores)
    assert all_assigned == 3

    # --- 6) Shopping-list check-off + clear ---
    first = sl.get_active_items()[0]
    sl.check_off_item(first.id, checked=True)
    after_check = {i.id for i in sl.get_active_items(include_checked_off=True) if i.is_checked_off}
    assert first.id in after_check

    sl.clear_all_checked_off()
    remaining = {i.id for i in sl.get_active_items()}
    assert first.id not in remaining
    assert len(remaining) == 2


def test_weekly_planner_end_to_end(isolated_db):
    planner = WeeklyPlannerService(
        meal_suggestion_service=MealSuggestionService(price_history_service=None),
        shopping_list_service=ShoppingListService(),
    )
    weekly = planner.build_weekly_plan(
        num_recipes=3, map_ingredients=False, persist_to_shopping_list=False
    )

    assert len(weekly.suggestions) == 3
    assert len(weekly.planned_ingredients) > 0

    summary_lines = summarize_weekly_plan(weekly)
    assert summary_lines[0] == "Weekly plan: 3 recipes"
