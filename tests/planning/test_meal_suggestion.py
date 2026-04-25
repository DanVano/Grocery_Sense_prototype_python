"""
M5 — MealSuggestionService

Folds the original tests/test_meal_suggestion_service.py smoke into assertions.

Covers (mandatory):
  - Hard profile filters (allergies, avoid_ingredients, no_pork/no_beef)
    — user-safety critical: an allergen-bearing recipe must NEVER be suggested.
  - Scoring ordering (target_ingredients bias, max_recipes cap)
  - Variety penalty for recently_used_recipe_ids
  - Preference score uplift from prefer_meats / favorite_tags
  - Flyer-deal integration via flyer_deals table
  - explain_suggested_meal / format_meal_explanation output
  - FINDING: recipe_engine constructor arg is stored but ignored

All tests point the module-level recipe singleton at the sample fixture so
production recipes.json is untouched.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from Grocery_Sense.data.repositories.flyers_repo import FlyersRepo
from Grocery_Sense.data.repositories.stores_repo import create_store
from Grocery_Sense.recipes import recipe_engine as re_mod
from Grocery_Sense.recipes.recipe_engine import RecipeEngine
from Grocery_Sense.services.meal_suggestion_service import (
    MealSuggestionService,
    SuggestedMeal,
    explain_suggested_meal,
    format_meal_explanation,
)


FIXTURE_PATH = (
    Path(__file__).resolve().parent.parent / "fixtures" / "recipes_sample.json"
)


@pytest.fixture(autouse=True)
def _patch_recipe_singleton(monkeypatch):
    """Point the module-level recipe singleton at the sample fixture."""
    fresh = RecipeEngine(recipes_path=FIXTURE_PATH)
    monkeypatch.setattr(re_mod, "_default_engine", fresh)


@pytest.fixture
def svc() -> MealSuggestionService:
    return MealSuggestionService(price_history_service=None)


def _names(suggestions):
    return [s.recipe.get("name") for s in suggestions]


# ---------------------------------------------------------------------------
# Hard profile filters — user safety critical
# ---------------------------------------------------------------------------


class TestHardProfileFilters:
    def test_allergy_blocks_recipe(self, svc, isolated_db):
        profile = {"allergies": ["peanuts"], "diet": "meat eater"}
        suggestions = svc.suggest_meals_for_week(profile=profile, max_recipes=20)
        assert "Peanut Chicken Noodles" not in _names(suggestions)

    def test_avoid_ingredients_block(self, svc, isolated_db):
        profile = {"avoid_ingredients": ["bread"]}
        suggestions = svc.suggest_meals_for_week(profile=profile, max_recipes=20)
        assert "Bread and Butter" not in _names(suggestions)

    def test_no_pork_restriction(self, svc, isolated_db):
        profile = {"restrictions": ["no_pork"]}
        suggestions = svc.suggest_meals_for_week(profile=profile, max_recipes=20)
        assert "Pork Chops with Apple" not in _names(suggestions)

    def test_no_beef_restriction(self, svc, isolated_db):
        profile = {"restrictions": ["no_beef"]}
        suggestions = svc.suggest_meals_for_week(profile=profile, max_recipes=20)
        assert "Beef Stir Fry" not in _names(suggestions)

    def test_allergy_applies_even_when_recipe_matches_target_ingredients(
        self, svc, isolated_db
    ):
        """
        Even when a recipe is a strong ingredient match, the allergy filter
        must still drop it. This is the crown-jewel safety contract.
        """
        profile = {"allergies": ["peanuts"]}
        suggestions = svc.suggest_meals_for_week(
            profile=profile,
            target_ingredients=["peanuts", "chicken", "soy sauce"],
            max_recipes=20,
        )
        assert "Peanut Chicken Noodles" not in _names(suggestions)

    def test_empty_after_filters_returns_empty(self, svc, isolated_db):
        profile = {"allergies": ["rice", "pasta", "peanuts", "beef", "pork", "salmon", "quinoa", "bread"]}
        suggestions = svc.suggest_meals_for_week(profile=profile, max_recipes=20)
        # Chicken Thighs has rice, Veggie Pasta has pasta, etc. — everything should filter out.
        assert suggestions == []


# ---------------------------------------------------------------------------
# Scoring + ordering
# ---------------------------------------------------------------------------


class TestScoringAndOrdering:
    def test_returns_suggestions_without_profile(self, svc, isolated_db):
        suggestions = svc.suggest_meals_for_week(profile={}, max_recipes=5)
        assert len(suggestions) <= 5
        for s in suggestions:
            assert isinstance(s, SuggestedMeal)
            assert 0.0 <= s.preference_score <= 1.0

    def test_target_ingredients_biases_candidate_set(self, svc, isolated_db):
        suggestions = svc.suggest_meals_for_week(
            profile={},
            target_ingredients=["salmon"],
            max_recipes=20,
        )
        assert _names(suggestions) == ["Salmon Teriyaki"]

    def test_max_recipes_caps_output(self, svc, isolated_db):
        suggestions = svc.suggest_meals_for_week(profile={}, max_recipes=2)
        assert len(suggestions) == 2

    def test_prefer_meats_boosts_preference_score(self, svc, isolated_db):
        """
        With beef preference, Beef Stir Fry's preference_score rises +0.3,
        which is enough to move it ahead of otherwise tied recipes.
        """
        profile = {"prefer_meats": ["beef"]}
        suggestions = svc.suggest_meals_for_week(profile=profile, max_recipes=20)
        beef = next(s for s in suggestions if s.recipe["name"] == "Beef Stir Fry")
        assert beef.preference_score > 0

    def test_favorite_tag_boosts_preference_score(self, svc, isolated_db):
        profile = {"favorite_tags": ["weeknight"]}
        suggestions = svc.suggest_meals_for_week(profile=profile, max_recipes=20)
        wn = next(s for s in suggestions if s.recipe["name"] == "Chicken Thighs with Rice")
        assert wn.preference_score > 0

    def test_avoid_meats_zeroes_preference_score(self, svc, isolated_db):
        """
        avoid_meats subtracts 0.5 from preference_score. With a single avoid_meats
        hit and no positive bonuses, the score clamps to zero — it must not go
        negative or propagate into a negative total_score.
        """
        profile = {"avoid_meats": ["pork"]}
        suggestions = svc.suggest_meals_for_week(profile=profile, max_recipes=20)
        if any(s.recipe["name"] == "Pork Chops with Apple" for s in suggestions):
            pork = next(s for s in suggestions if s.recipe["name"] == "Pork Chops with Apple")
            assert pork.preference_score == 0.0
            assert pork.total_score >= 0.0


# ---------------------------------------------------------------------------
# Variety penalty
# ---------------------------------------------------------------------------


class TestVarietyPenalty:
    def test_recently_used_recipes_get_negative_variety(self, svc, isolated_db):
        suggestions = svc.suggest_meals_for_week(
            profile={},
            max_recipes=20,
            recently_used_recipe_ids={2},  # Beef Stir Fry id
        )
        beef = next(s for s in suggestions if s.recipe["name"] == "Beef Stir Fry")
        assert beef.variety_score < 0
        # Reason line should mention the penalty.
        assert any("recently" in r.lower() for r in beef.reasons)

    def test_no_recent_ids_means_zero_variety(self, svc, isolated_db):
        suggestions = svc.suggest_meals_for_week(profile={}, max_recipes=20)
        for s in suggestions:
            assert s.variety_score == 0


# ---------------------------------------------------------------------------
# Flyer-deal integration
# ---------------------------------------------------------------------------


class TestDealIntegration:
    def test_active_flyer_deal_boosts_price_score(self, svc, isolated_db):
        """
        Seed a flyer with a deal whose title overlaps a recipe ingredient.
        price_score should become > 0 for that recipe.
        """
        store = create_store(name="Deal Mart")
        repo = FlyersRepo()
        repo.ensure_schema()

        # Build an active flyer batch (today within window).
        from datetime import date, timedelta

        today = date.today()
        vf = (today - timedelta(days=1)).isoformat()
        vt = (today + timedelta(days=6)).isoformat()

        batch_id = repo.create_flyer_batch(
            store_id=store.id, valid_from=vf, valid_to=vt, source_type="test"
        )
        repo.add_deal(
            flyer_id=batch_id,
            store_id=store.id,
            title="chicken thighs",
            description="family pack",
            price_text="$5.99/kg",
            unit_price=5.99,
            unit="kg",
        )

        suggestions = svc.suggest_meals_for_week(profile={}, max_recipes=20)
        chicken = next(
            s for s in suggestions if s.recipe["name"] == "Chicken Thighs with Rice"
        )
        assert chicken.price_score > 0

    def test_no_deals_means_zero_price_score(self, svc, isolated_db):
        suggestions = svc.suggest_meals_for_week(profile={}, max_recipes=20)
        for s in suggestions:
            assert s.price_score == 0.0


# ---------------------------------------------------------------------------
# format_meal_explanation + explain_suggested_meal
# ---------------------------------------------------------------------------


class TestExplanation:
    def test_explain_includes_recipe_name(self, svc, isolated_db):
        suggestions = svc.suggest_meals_for_week(profile={}, max_recipes=1)
        text = explain_suggested_meal(suggestions[0])
        assert suggestions[0].recipe["name"] in text

    def test_format_high_scores_produces_summary_bits(self):
        text = format_meal_explanation(
            recipe_name="X",
            preference_score=0.8,
            deal_score=0.5,
            price_score=0.4,
            variety_score=0.5,
            reasons=["cheap chicken"],
        )
        assert "preferences" in text.lower()
        assert "sale" in text.lower()
        assert "cheaper" in text.lower()
        assert "variety" in text.lower()
        assert "cheap chicken" in text

    def test_format_low_scores_falls_back_to_generic_summary(self):
        text = format_meal_explanation(
            recipe_name="X",
            preference_score=0.0,
            deal_score=0.0,
            price_score=0.0,
            variety_score=0.0,
            reasons=[],
        )
        assert "reasonable match" in text

    def test_format_respects_max_reasons(self):
        reasons = [f"r{i}" for i in range(10)]
        text = format_meal_explanation(
            recipe_name="X",
            preference_score=0.5,
            deal_score=0.5,
            price_score=0.5,
            variety_score=0.5,
            reasons=reasons,
            max_reasons=3,
        )
        assert "r0" in text
        assert "r2" in text
        assert "r3" not in text  # cut off


# ---------------------------------------------------------------------------
# FINDING — constructor arg ignored
# ---------------------------------------------------------------------------


class TestInjectionFinding:
    def test_injected_recipe_engine_is_ignored(self, isolated_db, tmp_path):
        """
        FINDING: MealSuggestionService accepts a recipe_engine kwarg and stores
        it on self, but suggest_meals_for_week routes through the module-level
        `load_all_recipes` / `filter_recipes_by_ingredients_and_profile`
        singletons — so the injected engine has no effect. Tests that want a
        custom recipe set must monkeypatch re_mod._default_engine (see autouse
        fixture). Fix candidates: route through self.recipe_engine, or remove
        the constructor arg to stop pretending.
        """
        empty_engine = RecipeEngine(recipes_path=tmp_path / "missing.json")
        svc = MealSuggestionService(
            price_history_service=None, recipe_engine=empty_engine
        )
        # Despite the injected empty engine, the module singleton (our fixture)
        # still drives results → we get suggestions.
        suggestions = svc.suggest_meals_for_week(profile={}, max_recipes=3)
        assert len(suggestions) > 0
