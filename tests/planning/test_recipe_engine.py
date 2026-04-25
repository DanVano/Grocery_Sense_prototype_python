"""
M5 — RecipeEngine

Pure file-based engine; tests pin:
  - Loading + caching semantics (force_reload)
  - Malformed JSON rejection (no silent single-bogus-recipe coercion)
  - Dict-wrapper `{"recipes": [...]}` accepted
  - Missing file returns [] (not an error)
  - Ingredient filter + score ranking
  - Profile hard filters: allergies, avoid_ingredients, no_pork/no_beef
  - Soft bonuses: prefer_meats, favorite_tags
  - get_recipe_by_name case-insensitive
  - Module-level singleton delegates to _default_engine
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from Grocery_Sense.recipes import recipe_engine as re_mod
from Grocery_Sense.recipes.recipe_engine import (
    Recipe,
    RecipeEngine,
    filter_recipes_by_ingredients_and_profile,
    get_recipe_by_name,
    load_all_recipes,
)


FIXTURE_PATH = (
    Path(__file__).resolve().parent.parent / "fixtures" / "recipes_sample.json"
)


@pytest.fixture
def engine() -> RecipeEngine:
    return RecipeEngine(recipes_path=FIXTURE_PATH)


# ---------------------------------------------------------------------------
# Recipe dataclass
# ---------------------------------------------------------------------------


class TestRecipeWrapper:
    def test_extracts_core_fields(self):
        r = Recipe({
            "id": 42,
            "name": "  Chicken Thighs  ",
            "ingredients": ["chicken", "", "  ", "rice"],
            "tags": ["Weeknight", "  ", "chicken"],
        })
        assert r.id == 42
        assert r.name == "Chicken Thighs"
        assert r.ingredients == ["chicken", "rice"]
        assert r.tags == ["weeknight", "chicken"]

    def test_stringifies_non_string_ingredients(self):
        """
        FINDING: Recipe.ingredients uses str(i) on every entry, so a None or
        numeric item becomes its stringified form ('None', '42') instead of
        being filtered. Callers that expect allergy-safe lists should
        sanitize at write time, not rely on this property.
        """
        r = Recipe({"ingredients": [None, 42, "rice"]})
        assert r.ingredients == ["None", "42", "rice"]

    def test_handles_missing_keys(self):
        r = Recipe({})
        assert r.id is None
        assert r.name == ""
        assert r.ingredients == []
        assert r.tags == []


# ---------------------------------------------------------------------------
# Loading + caching
# ---------------------------------------------------------------------------


class TestLoadAllRecipes:
    def test_loads_sample_fixture(self, engine):
        recipes = engine.load_all_recipes()
        assert len(recipes) == 8
        assert {r["name"] for r in recipes} >= {
            "Chicken Thighs with Rice",
            "Beef Stir Fry",
            "Salmon Teriyaki",
        }

    def test_cache_avoids_reread_without_force_reload(self, tmp_path):
        path = tmp_path / "recipes.json"
        path.write_text(json.dumps([{"name": "A", "ingredients": ["x"]}]), encoding="utf-8")
        eng = RecipeEngine(recipes_path=path)

        first = eng.load_all_recipes()
        assert len(first) == 1

        # Rewrite with different contents; cache should mask the change.
        path.write_text(
            json.dumps([{"name": "A"}, {"name": "B", "ingredients": ["y"]}]),
            encoding="utf-8",
        )
        cached = eng.load_all_recipes()
        assert len(cached) == 1

        # force_reload picks up the new file.
        reloaded = eng.load_all_recipes(force_reload=True)
        assert len(reloaded) == 2

    def test_missing_file_returns_empty(self, tmp_path):
        eng = RecipeEngine(recipes_path=tmp_path / "nope.json")
        assert eng.load_all_recipes() == []

    def test_dict_wrapper_shape_accepted(self, tmp_path):
        path = tmp_path / "wrapped.json"
        path.write_text(
            json.dumps({"recipes": [{"name": "Wrapped"}]}), encoding="utf-8"
        )
        eng = RecipeEngine(recipes_path=path)
        recipes = eng.load_all_recipes()
        assert len(recipes) == 1
        assert recipes[0]["name"] == "Wrapped"

    def test_malformed_json_raises_value_error(self, tmp_path):
        """A bare string at the top level must not be silently treated as a recipe."""
        path = tmp_path / "bad.json"
        path.write_text(json.dumps("not a recipe list"), encoding="utf-8")
        eng = RecipeEngine(recipes_path=path)
        with pytest.raises(ValueError):
            eng.load_all_recipes()


# ---------------------------------------------------------------------------
# filter_recipes_by_ingredients_and_profile
# ---------------------------------------------------------------------------


class TestIngredientFilter:
    def test_ranks_by_ingredient_overlap(self, engine):
        # rice is in 1, 2, 4
        results = engine.filter_recipes_by_ingredients_and_profile(
            include_ingredients=["rice"]
        )
        names = {r["name"] for r in results}
        assert "Chicken Thighs with Rice" in names
        assert "Beef Stir Fry" in names
        assert "Salmon Teriyaki" in names
        # veggie pasta has no rice
        assert "Veggie Pasta Primavera" not in names

    def test_returns_empty_when_no_matches(self, engine):
        assert engine.filter_recipes_by_ingredients_and_profile(
            include_ingredients=["unicorn"]
        ) == []

    def test_respects_max_results(self, engine):
        results = engine.filter_recipes_by_ingredients_and_profile(
            include_ingredients=["rice"], max_results=1
        )
        assert len(results) == 1

    def test_higher_overlap_ranks_first(self, engine):
        # Both "Chicken Thighs with Rice" (garlic, rice) and "Salmon Teriyaki" (rice)
        # match "rice"; "Chicken Thighs with Rice" also matches "garlic".
        results = engine.filter_recipes_by_ingredients_and_profile(
            include_ingredients=["rice", "garlic"]
        )
        assert results[0]["name"] == "Chicken Thighs with Rice"


# ---------------------------------------------------------------------------
# Hard profile filters — user safety
# ---------------------------------------------------------------------------


class TestProfileHardFilters:
    def test_allergy_blocks_recipe(self, engine):
        # "rice" matches both Chicken Thighs (1) and Peanut Chicken Noodles (6);
        # the peanuts allergy must hide recipe 6 while keeping recipe 1.
        results = engine.filter_recipes_by_ingredients_and_profile(
            include_ingredients=["rice"],
            profile={"allergies": ["peanuts"]},
        )
        names = {r["name"] for r in results}
        assert "Chicken Thighs with Rice" in names
        assert "Peanut Chicken Noodles" not in names

    def test_avoid_ingredients_blocks_recipe(self, engine):
        results = engine.filter_recipes_by_ingredients_and_profile(
            include_ingredients=["bread"],
            profile={"avoid_ingredients": ["bread"]},
        )
        assert results == []

    def test_no_pork_restriction(self, engine):
        results = engine.filter_recipes_by_ingredients_and_profile(
            include_ingredients=["pork"],
            profile={"restrictions": ["no_pork"]},
        )
        assert results == []

    def test_no_beef_restriction(self, engine):
        results = engine.filter_recipes_by_ingredients_and_profile(
            include_ingredients=["beef"],
            profile={"restrictions": ["no_beef"]},
        )
        assert results == []

    def test_allergies_are_substring_matched(self, engine):
        """
        FINDING: allergy terms are substring-matched against the joined
        ingredients text. Passing 'chicken' as an allergy would wipe out
        any chicken recipe — correct for real allergies, but also blocks
        things like 'chicken broth' if 'chicken' is listed as avoid.
        Documents current behavior so callers pass specific allergens.
        """
        results = engine.filter_recipes_by_ingredients_and_profile(
            include_ingredients=["chicken"],
            profile={"allergies": ["chicken"]},
        )
        assert results == []


# ---------------------------------------------------------------------------
# Soft profile bonuses
# ---------------------------------------------------------------------------


class TestProfileSoftBonuses:
    def test_prefer_meats_bumps_beef_recipe(self, engine):
        """
        With prefer_meats=['beef'], Beef Stir Fry gets +0.2, outranking
        another 1-overlap recipe even when both match 'rice' equally.
        """
        # Both "Beef Stir Fry" and "Salmon Teriyaki" match rice once.
        # With the beef preference, Beef Stir Fry should win.
        results = engine.filter_recipes_by_ingredients_and_profile(
            include_ingredients=["rice"],
            profile={"prefer_meats": ["beef"]},
            max_results=8,
        )
        # Chicken Thighs with Rice matches 'rice' + has 'garlic' → wins on pure overlap.
        # Beef Stir Fry matches 'rice' once + +0.2 bonus → beats Salmon Teriyaki.
        names = [r["name"] for r in results]
        assert names.index("Beef Stir Fry") < names.index("Salmon Teriyaki")

    def test_favorite_tags_bonus(self, engine):
        """
        favorite_tags adds +0.1 per match; combined with ingredient overlap
        it breaks ties between otherwise equally-scored recipes.
        """
        results = engine.filter_recipes_by_ingredients_and_profile(
            include_ingredients=["rice"],
            profile={"favorite_tags": ["weeknight"]},
            max_results=8,
        )
        names = [r["name"] for r in results]
        # Beef Stir Fry has 'weeknight' tag; Salmon Teriyaki does not.
        assert names.index("Beef Stir Fry") < names.index("Salmon Teriyaki")


# ---------------------------------------------------------------------------
# get_recipe_by_name
# ---------------------------------------------------------------------------


class TestGetRecipeByName:
    def test_case_insensitive_match(self, engine):
        r = engine.get_recipe_by_name("CHICKEN thighs WITH rice")
        assert r is not None
        assert r["id"] == 1

    def test_unknown_returns_none(self, engine):
        assert engine.get_recipe_by_name("nonexistent dish") is None


# ---------------------------------------------------------------------------
# Module-level singleton delegation
# ---------------------------------------------------------------------------


class TestModuleSingleton:
    """
    The module exposes load_all_recipes / filter_... / get_recipe_by_name as
    delegates to _default_engine. Tests confirm the delegation works but use
    a fresh engine pointed at our fixture via monkeypatch.
    """

    @pytest.fixture(autouse=True)
    def _patch_singleton(self, monkeypatch):
        fresh = RecipeEngine(recipes_path=FIXTURE_PATH)
        monkeypatch.setattr(re_mod, "_default_engine", fresh)

    def test_load_all_recipes_delegates(self):
        recipes = load_all_recipes()
        assert len(recipes) == 8

    def test_filter_delegates(self):
        results = filter_recipes_by_ingredients_and_profile(
            include_ingredients=["rice"]
        )
        assert any(r["name"] == "Chicken Thighs with Rice" for r in results)

    def test_get_recipe_by_name_delegates(self):
        assert get_recipe_by_name("Bread and Butter") is not None
