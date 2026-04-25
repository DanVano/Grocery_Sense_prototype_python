"""
M5 — WeeklyPlannerService

Folds the original tests/test_weekly_planner.py smoke.

Covers:
  - _aggregate_ingredients dedups by normalized name, sorted by count desc
  - build_weekly_plan wires suggestions → planned_ingredients
  - map_ingredients=True annotates PlannedIngredient with mapping results
  - map_ingredients=False leaves mapping fields blank
  - persist_to_shopping_list=True writes rows with notes
  - summarize_weekly_plan formatting
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from Grocery_Sense.data.repositories.items_repo import create_item
from Grocery_Sense.recipes import recipe_engine as re_mod
from Grocery_Sense.recipes.recipe_engine import RecipeEngine
from Grocery_Sense.services.meal_suggestion_service import (
    MealSuggestionService,
    SuggestedMeal,
)
from Grocery_Sense.services.shopping_list_service import ShoppingListService
from Grocery_Sense.services.weekly_planner_service import (
    PlannedIngredient,
    WeeklyPlan,
    WeeklyPlannerService,
    _aggregate_ingredients,
    summarize_weekly_plan,
)


FIXTURE_PATH = (
    Path(__file__).resolve().parent.parent / "fixtures" / "recipes_sample.json"
)


@pytest.fixture(autouse=True)
def _patch_recipe_singleton(monkeypatch):
    fresh = RecipeEngine(recipes_path=FIXTURE_PATH)
    monkeypatch.setattr(re_mod, "_default_engine", fresh)


@pytest.fixture
def planner(isolated_db) -> WeeklyPlannerService:
    return WeeklyPlannerService(
        meal_suggestion_service=MealSuggestionService(price_history_service=None),
        shopping_list_service=ShoppingListService(),
    )


def _meal(name: str, ingredients: list, rid: int = 1) -> SuggestedMeal:
    return SuggestedMeal(
        recipe={"id": rid, "name": name, "ingredients": ingredients},
        total_score=1.0,
        preference_score=0.5,
        deal_score=0.0,
        price_score=0.0,
        variety_score=0.0,
        reasons=[],
    )


# ---------------------------------------------------------------------------
# _aggregate_ingredients (pure helper)
# ---------------------------------------------------------------------------


class TestAggregateIngredients:
    def test_dedup_by_normalized_name(self):
        suggestions = [
            _meal("A", ["Rice", "garlic"], rid=1),
            _meal("B", ["RICE ", "onion"], rid=2),
        ]
        planned = _aggregate_ingredients(suggestions)
        names = [p.name.lower() for p in planned]
        assert names.count("rice") == 1

    def test_tracks_all_recipes_using_ingredient(self):
        suggestions = [
            _meal("A", ["rice"], rid=1),
            _meal("B", ["rice"], rid=2),
            _meal("C", ["rice"], rid=3),
        ]
        planned = _aggregate_ingredients(suggestions)
        rice = next(p for p in planned if p.name.lower() == "rice")
        assert set(rice.recipe_names) == {"A", "B", "C"}
        assert rice.approximate_count == 3

    def test_sort_by_count_then_name(self):
        """Higher count first; ties broken alphabetically."""
        suggestions = [
            _meal("A", ["rice", "zucchini"], rid=1),
            _meal("B", ["rice", "apples"], rid=2),
        ]
        planned = _aggregate_ingredients(suggestions)
        # rice appears twice; zucchini and apples each appear once.
        assert planned[0].name.lower() == "rice"
        # Among singletons, alphabetical — apples before zucchini.
        singleton_names = [p.name.lower() for p in planned[1:]]
        assert singleton_names.index("apples") < singleton_names.index("zucchini")

    def test_empty_returns_empty(self):
        assert _aggregate_ingredients([]) == []


# ---------------------------------------------------------------------------
# build_weekly_plan
# ---------------------------------------------------------------------------


class TestBuildWeeklyPlan:
    def test_returns_weekly_plan_with_suggestions(self, planner):
        plan = planner.build_weekly_plan(
            num_recipes=3, map_ingredients=False, persist_to_shopping_list=False
        )
        assert isinstance(plan, WeeklyPlan)
        assert len(plan.suggestions) == 3
        assert len(plan.planned_ingredients) > 0
        assert all(isinstance(p, PlannedIngredient) for p in plan.planned_ingredients)

    def test_num_recipes_caps_suggestions(self, planner):
        plan = planner.build_weekly_plan(
            num_recipes=2, map_ingredients=False, persist_to_shopping_list=False
        )
        assert len(plan.suggestions) == 2

    def test_map_ingredients_false_leaves_mapping_unset(self, planner):
        plan = planner.build_weekly_plan(
            num_recipes=2, map_ingredients=False, persist_to_shopping_list=False
        )
        for ing in plan.planned_ingredients:
            assert ing.item_id is None
            assert ing.match_method is None

    def test_map_ingredients_true_annotates_mapping(self, planner, isolated_db):
        # Seed a canonical item that ingredients from the sample fixture will map to.
        create_item(canonical_name="rice")
        plan = planner.build_weekly_plan(
            num_recipes=4, map_ingredients=True, persist_to_shopping_list=False
        )

        rice = next(
            (p for p in plan.planned_ingredients if p.name.lower() == "rice"), None
        )
        assert rice is not None
        assert rice.item_id is not None
        assert rice.canonical_name == "rice"
        assert rice.match_method in {"alias", "fuzzy"}
        assert rice.match_confidence is not None

    def test_map_ingredients_true_handles_no_match(self, planner, isolated_db):
        """
        With no canonical items seeded, every mapping attempt returns
        method='none'. Service must still populate match_method/confidence
        on every PlannedIngredient rather than leaving them None.
        """
        plan = planner.build_weekly_plan(
            num_recipes=2, map_ingredients=True, persist_to_shopping_list=False
        )
        for ing in plan.planned_ingredients:
            assert ing.item_id is None
            assert ing.match_method == "none"


# ---------------------------------------------------------------------------
# persist_to_shopping_list
# ---------------------------------------------------------------------------


class TestPersistence:
    def test_writes_rows_to_shopping_list(self, planner, isolated_db):
        plan = planner.build_weekly_plan(
            num_recipes=2,
            map_ingredients=False,
            persist_to_shopping_list=True,
            added_by="test",
        )

        items = planner.shopping_list_service.get_active_items()
        # Every aggregated ingredient should land in the list.
        assert len(items) == len(plan.planned_ingredients)

        # Notes should include "Used in: <recipe names>"
        for row in items:
            assert "Used in:" in (row.notes or "")

    def test_quantity_reflects_approximate_count(self, planner, isolated_db):
        plan = planner.build_weekly_plan(
            num_recipes=4,
            map_ingredients=False,
            persist_to_shopping_list=True,
        )
        items = planner.shopping_list_service.get_active_items()
        by_name = {i.display_name.lower(): i for i in items}

        for ing in plan.planned_ingredients:
            row = by_name[ing.name.lower()]
            # quantity clamped to at least 1.0
            assert row.quantity >= 1.0
            if ing.approximate_count > 1:
                assert row.quantity == float(ing.approximate_count)


# ---------------------------------------------------------------------------
# summarize_weekly_plan
# ---------------------------------------------------------------------------


class TestSummarize:
    def test_includes_suggestion_count_and_scores(self):
        suggestions = [_meal("A", ["rice"], rid=1), _meal("B", ["beef"], rid=2)]
        planned = _aggregate_ingredients(suggestions)
        plan = WeeklyPlan(suggestions=suggestions, planned_ingredients=planned)

        lines = summarize_weekly_plan(plan)
        assert lines[0] == "Weekly plan: 2 recipes"
        assert any("A" in line and "score" in line for line in lines)
        assert any("B" in line for line in lines)
        assert any("unique items" in line for line in lines)

    def test_handles_empty_plan(self):
        plan = WeeklyPlan(suggestions=[], planned_ingredients=[])
        lines = summarize_weekly_plan(plan)
        assert lines[0] == "Weekly plan: 0 recipes"
