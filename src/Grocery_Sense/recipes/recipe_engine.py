"""
Grocery_Sense.recipes.recipe_engine

Shared recipe engine for Grocery Sense.

This module is intentionally:
- PURE BACKEND (no UI, no HTTP)
- File-based for now (reads recipes.json)
- Designed so AI Chef can later reuse the same recipes.json structure

Expected recipe shape (from recipes.json):
    {
        "id": optional str|int,
        "name": "Chicken Thighs with Rice",
        "ingredients": ["chicken thighs", "rice", "garlic", ...],
        "steps": [...],
        "tags": ["chicken", "under_30_min", "weeknight", ...]
    }

The engine provides:
- load_all_recipes(): load and cache recipes from JSON
- filter_recipes_by_ingredients_and_profile(): basic matching driven by
  ingredients + user profile (diet, allergies, avoid lists)

Later:
- A higher-level MealSuggestionService will call this and combine it with:
    - DealsService (Flipp)
    - PriceHistoryService (receipts)
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

# Default location: place recipes.json in the same folder as this file
_DEFAULT_RECIPES_PATH = Path(__file__).resolve().with_name("recipes.json")


@dataclass
class Recipe:
    """Lightweight wrapper; internally we still mostly use dicts for flexibility."""
    raw: Dict[str, Any]

    @property
    def id(self) -> Any:
        return self.raw.get("id")

    @property
    def name(self) -> str:
        return str(self.raw.get("name", "")).strip()

    @property
    def ingredients(self) -> List[str]:
        ings = self.raw.get("ingredients") or []
        return [str(i).strip() for i in ings if str(i).strip()]

    @property
    def tags(self) -> List[str]:
        tags = self.raw.get("tags") or []
        return [str(t).strip().lower() for t in tags if str(t).strip()]


# ---------------------------------------------------------------------------
# Core engine
# ---------------------------------------------------------------------------


class RecipeEngine:
    """
    Core recipe engine.

    Responsibilities:
    - Load recipes from recipes.json
    - Do basic filtering by ingredients + user profile
    - Provide a simple scoring mechanism suitable for feeding
      into higher-level planning/suggestion services.
    """

    def __init__(self, recipes_path: Optional[Path] = None) -> None:
        self._recipes_path: Path = Path(recipes_path) if recipes_path else _DEFAULT_RECIPES_PATH
        self._cache: Optional[List[Dict[str, Any]]] = None

    # ---- Loading --------------------------------------------------------

    def load_all_recipes(self, force_reload: bool = False) -> List[Dict[str, Any]]:
        """
        Load and cache all recipes from recipes.json.

        Returns a list of raw recipe dicts (not Recipe objects) for ease
        of JSON serialization and compatibility with existing code.
        """
        if self._cache is not None and not force_reload:
            return list(self._cache)

        if not self._recipes_path.exists():
            # No recipes file yet; caller should handle empty list.
            self._cache = []
            return []

        with self._recipes_path.open("r", encoding="utf-8") as f:
            data = json.load(f)

        # Normalize to list. Accepts either a bare list of recipes or a dict
        # wrapper of the form {"recipes": [...]}. Any other shape is rejected
        # with a clear error so malformed JSON doesn't silently become a
        # single bogus recipe.
        if isinstance(data, list):
            self._cache = list(data)
        elif isinstance(data, dict) and isinstance(data.get("recipes"), list):
            self._cache = list(data["recipes"])
        else:
            raise ValueError(
                f"recipes.json at {self._recipes_path} must be a list of recipes "
                f"or an object with a 'recipes' list; got {type(data).__name__}"
            )

        return list(self._cache)

    # ---- Public filtering API ------------------------------------------

    def filter_recipes_by_ingredients_and_profile(
        self,
        include_ingredients: Iterable[str],
        profile: Optional[Dict[str, Any]] = None,
        max_results: int = 10,
    ) -> List[Dict[str, Any]]:
        """
        Rough equivalent of old filter_recipes(), but scoped to Grocery Sense.

        - include_ingredients: names of things the user wants to use/buy
        - profile: dict with keys like:
            - "diet": "vegan" | "vegetarian" | "meat eater" | "pescatarian" | ...
            - "allergies": [...]
            - "avoid_ingredients": [...]
            - "restrictions": [...]
        """
        recipes = self.load_all_recipes()
        if not recipes:
            return []

        include_set = _normalize_set(include_ingredients)

        filtered_scored: List[Tuple[float, Dict[str, Any]]] = []

        for raw in recipes:
            r = Recipe(raw)

            # Hard-profile filtering first
            if profile and not _recipe_satisfies_profile(r, profile):
                continue

            recipe_ingredients = _normalize_set(r.ingredients)
            match_count = len(include_set & recipe_ingredients)
            if match_count <= 0:
                continue

            # Simple score: ingredient overlap (0..N) scaled a bit,
            # plus a tiny bonus for "favorite" tags etc. (future).
            base_score = float(match_count)
            bonus = _profile_small_bonus(r, profile or {})
            total_score = base_score + bonus

            filtered_scored.append((total_score, raw))

        # Sort by score, descending
        filtered_scored.sort(key=lambda t: t[0], reverse=True)

        return [r for _, r in filtered_scored[:max_results]]

    # ---- Convenience helpers -------------------------------------------

    def get_recipe_by_name(self, name: str) -> Optional[Dict[str, Any]]:
        """
        Case-insensitive name lookup.
        """
        name_low = name.strip().lower()
        for raw in self.load_all_recipes():
            if str(raw.get("name", "")).strip().lower() == name_low:
                return raw
        return None


# ---------------------------------------------------------------------------
# Helper functions (module-internal)
# ---------------------------------------------------------------------------


def _normalize_set(values: Iterable[str]) -> Set[str]:
    return {
        str(v).strip().lower()
        for v in values
        if isinstance(v, str) and v.strip()
    }


def _recipe_satisfies_profile(recipe: Recipe, profile: Dict[str, Any]) -> bool:
    """
    Hard constraints:
    - allergies
    - avoid_ingredients
    - (optionally) diet-based tag enforcement later
    """
    ingredients_text = " ".join(recipe.ingredients).lower()

    allergies = _normalize_set(profile.get("allergies", []))
    avoid = _normalize_set(profile.get("avoid_ingredients", []))
    restrictions = _normalize_set(profile.get("restrictions", []))

    # Allergies & avoid: if any term appears in the ingredients, reject
    for term in allergies | avoid:
        if term and term in ingredients_text:
            return False

    # Diet / restrictions: for now, we only soft-handle via tags in scoring.
    # Later you could enforce, e.g. vegan recipes must have "vegan" tag.
    # Example (commented out):
    #
    # diet = str(profile.get("diet", "")).lower()
    # if diet == "vegan" and "vegan" not in recipe.tags:
    #     return False

    # You can also map special restrictions like "no_pork" to term checks here.
    for r in restrictions:
        if r == "no_pork" and "pork" in ingredients_text:
            return False
        if r == "no_beef" and "beef" in ingredients_text:
            return False

    return True


def _profile_small_bonus(recipe: Recipe, profile: Dict[str, Any]) -> float:
    """
    Soft preferences only (small weight):
    - prefer_meats: +0.2 per preferred meat present
    - favorite_tags: +0.1 per matched tag

    This is deliberately mild; the heavy lifting will be done later
    by MealSuggestionService which combines price/value info.
    """
    ingredients_text = " ".join(recipe.ingredients).lower()
    tags = set(recipe.tags)

    prefer_meats = _normalize_set(profile.get("prefer_meats", []))
    favorite_tags = _normalize_set(profile.get("favorite_tags", []))

    bonus = 0.0

    for meat in prefer_meats:
        if meat and meat in ingredients_text:
            bonus += 0.2

    for tag in favorite_tags:
        if tag and tag in tags:
            bonus += 0.1

    return bonus


# ---------------------------------------------------------------------------
# Module-level singleton (optional convenience)
# ---------------------------------------------------------------------------

# You can either import the class and instantiate it,
# or use these convenience functions:

_default_engine = RecipeEngine()


def load_all_recipes(force_reload: bool = False) -> List[Dict[str, Any]]:
    return _default_engine.load_all_recipes(force_reload=force_reload)


def filter_recipes_by_ingredients_and_profile(
    include_ingredients: Iterable[str],
    profile: Optional[Dict[str, Any]] = None,
    max_results: int = 10,
) -> List[Dict[str, Any]]:
    return _default_engine.filter_recipes_by_ingredients_and_profile(
        include_ingredients=include_ingredients,
        profile=profile,
        max_results=max_results,
    )


def get_recipe_by_name(name: str) -> Optional[Dict[str, Any]]:
    return _default_engine.get_recipe_by_name(name)
