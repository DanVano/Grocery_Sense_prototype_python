"""
Grocery_Sense.services.weekly_planner_service

WeeklyPlannerService:
- Orchestrates MealSuggestionService to pick recipes for the week
- Aggregates ingredients into a combined shopping list view
- Optionally persists items into ShoppingListService

✅ Now wires Ingredient Mapping:
- Aggregated ingredients are mapped to canonical item_id (best-effort)
- When persisting to shopping list, passes item_id to avoid re-mapping
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence

from Grocery_Sense.services.meal_suggestion_service import (
    MealSuggestionService,
    SuggestedMeal,
)
from Grocery_Sense.services.shopping_list_service import ShoppingListService


@dataclass
class PlannedIngredient:
    name: str
    recipe_names: List[str]
    approximate_count: int

    # ✅ mapping outputs (best-effort)
    item_id: Optional[int] = None
    canonical_name: Optional[str] = None
    match_confidence: Optional[float] = None
    match_method: Optional[str] = None  # alias/fuzzy/none


@dataclass
class WeeklyPlan:
    suggestions: List[SuggestedMeal]
    planned_ingredients: List[PlannedIngredient]


def _normalize_ingredient_name(name: str) -> str:
    return " ".join(str(name).strip().lower().split())


def _extract_ingredients(recipe: Dict[str, Any]) -> List[str]:
    ings = recipe.get("ingredients") or []
    out: List[str] = []
    for x in ings:
        s = str(x).strip()
        if s:
            out.append(s)
    return out


def _aggregate_ingredients(
    suggestions: Sequence[SuggestedMeal],
) -> List[PlannedIngredient]:
    agg: Dict[str, Dict[str, Any]] = {}

    for s in suggestions:
        recipe = s.recipe
        recipe_name = str(recipe.get("name", "")).strip() or "Unnamed Recipe"

        for ing in _extract_ingredients(recipe):
            norm = _normalize_ingredient_name(ing)
            if not norm:
                continue

            if norm not in agg:
                agg[norm] = {
                    "display": ing.strip(),
                    "recipes": set(),
                    "count": 0,
                }

            agg[norm]["recipes"].add(recipe_name)
            agg[norm]["count"] += 1

    planned: List[PlannedIngredient] = []
    for _, data in agg.items():
        planned.append(
            PlannedIngredient(
                name=str(data["display"]),
                recipe_names=sorted(list(data["recipes"])),
                approximate_count=int(data["count"]),
            )
        )

    planned.sort(key=lambda x: (-x.approximate_count, x.name.lower()))
    return planned


class WeeklyPlannerService:
    def __init__(
        self,
        meal_suggestion_service: MealSuggestionService,
        shopping_list_service: ShoppingListService,
    ) -> None:
        self.meal_suggestion_service = meal_suggestion_service
        self.shopping_list_service = shopping_list_service
        self._mapper = None

    def _get_mapper(self):
        if self._mapper is None:
            from Grocery_Sense.data.repositories import items_repo as _items_repo
            from Grocery_Sense.services.ingredient_mapping_service import IngredientMappingService
            self._mapper = IngredientMappingService(items_repo=_items_repo)
        return self._mapper

    def build_weekly_plan(
        self,
        num_recipes: int = 6,
        target_ingredients: Optional[Iterable[str]] = None,
        recently_used_recipe_ids: Optional[Iterable[Any]] = None,
        persist_to_shopping_list: bool = False,
        planned_store_id: Optional[int] = None,
        added_by: Optional[str] = None,
        map_ingredients: bool = True,
    ) -> WeeklyPlan:
        suggestions = self.meal_suggestion_service.suggest_meals_for_week(
            target_ingredients=target_ingredients,
            max_recipes=num_recipes,
            recently_used_recipe_ids=recently_used_recipe_ids,
        )

        planned_ingredients = _aggregate_ingredients(suggestions)

        # ✅ best-effort mapping for each aggregated ingredient
        if map_ingredients:
            mapper = self._get_mapper()
            for ing in planned_ingredients:
                res = mapper.map_to_item(ing.name)
                if res and res.item_id:
                    ing.item_id = res.item_id
                    ing.canonical_name = res.canonical_name
                    ing.match_confidence = float(res.confidence)
                    ing.match_method = str(res.method)
                else:
                    ing.match_confidence = float(res.confidence) if res else None
                    ing.match_method = str(res.method) if res else "none"

        plan = WeeklyPlan(
            suggestions=list(suggestions),
            planned_ingredients=planned_ingredients,
        )

        if persist_to_shopping_list:
            self._persist_plan_to_shopping_list(
                plan=plan,
                planned_store_id=planned_store_id,
                added_by=added_by,
            )

        return plan

    def _persist_plan_to_shopping_list(
        self,
        plan: WeeklyPlan,
        planned_store_id: Optional[int],
        added_by: Optional[str],
    ) -> None:
        for ing in plan.planned_ingredients:
            notes_parts: List[str] = []
            if ing.recipe_names:
                notes_parts.append("Used in: " + ", ".join(ing.recipe_names))

            # include mapping summary in notes (nice for demo/debug)
            if ing.item_id is not None and ing.match_confidence is not None:
                label = ing.canonical_name or f"item_id={ing.item_id}"
                notes_parts.append(f"Mapped: {label} ({ing.match_confidence:.2f}, {ing.match_method})")

            notes = " | ".join(notes_parts) if notes_parts else None
            quantity = max(1.0, float(ing.approximate_count))

            # ✅ pass item_id and disable auto_map to avoid extra fuzzy calls
            self.shopping_list_service.add_single_item(
                name=ing.name,
                quantity=quantity,
                unit="each",
                planned_store_id=planned_store_id,
                notes=notes,
                added_by=added_by,
                item_id=ing.item_id,
                auto_map=False,
            )


def summarize_weekly_plan(plan: WeeklyPlan) -> list[str]:
    lines: list[str] = []
    lines.append(f"Weekly plan: {len(plan.suggestions)} recipes")
    for i, s in enumerate(plan.suggestions, 1):
        name = s.recipe.get("name") or s.recipe.get("title") or f"Recipe {i}"
        lines.append(f"{i}. {name} (score={s.total_score:.2f})")
    if plan.planned_ingredients:
        lines.append(f"Planned ingredients: {len(plan.planned_ingredients)} unique items")
    return lines
