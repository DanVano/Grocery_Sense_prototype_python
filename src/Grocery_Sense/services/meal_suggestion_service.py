"""
Grocery_Sense.services.meal_suggestion_service

High-level engine for suggesting value-focused meals for the week.

Combines:
- User profile (diet, allergies, meat prefs, favorite tags)
- Recipe data (from recipes.json via RecipeEngine)
- Current flyer deals (via deals_service.search_deals)
- Historical baseline prices (via injected PriceHistoryService)

This is the "Choice C" brain:
    preferences + sales + historical avg (weighted less)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from Grocery_Sense.config.config_store import get_user_profile
from Grocery_Sense.recipes.recipe_engine import (
    RecipeEngine,
    load_all_recipes,
    filter_recipes_by_ingredients_and_profile,
)
from Grocery_Sense.services.deals_service import Deal


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class SuggestedMeal:
    recipe: dict
    total_score: float
    preference_score: float
    deal_score: float
    price_score: float
    variety_score: float
    reasons: list[str]
    explanation: str | None = None


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _lower_list(values: Optional[Iterable[str]]) -> List[str]:
    if not values:
        return []
    return [v.strip().lower() for v in values if isinstance(v, str) and v.strip()]


def _extract_core_ingredients(recipe: Dict[str, Any]) -> List[str]:
    ings = recipe.get("ingredients") or []
    return [str(i).strip() for i in ings if str(i).strip()]


def _recipe_has_disallowed_ingredients(recipe: Dict[str, Any], profile: Dict[str, Any]) -> bool:
    """
    Hard filter using allergies / avoid_ingredients / restrictions.
    This is a safety net; RecipeEngine already does some of this.
    """
    ingredients_text = " ".join(_extract_core_ingredients(recipe)).lower()

    allergies = set(_lower_list(profile.get("allergies", [])))
    avoid = set(_lower_list(profile.get("avoid_ingredients", [])))
    restrictions = set(_lower_list(profile.get("restrictions", [])))

    for term in allergies | avoid:
        if term and term in ingredients_text:
            return True

    # Map some restrictions to ingredient bans
    if "no_pork" in restrictions and "pork" in ingredients_text:
        return True
    if "no_beef" in restrictions and "beef" in ingredients_text:
        return True

    return False


def _compute_preference_score(recipe: Dict[str, Any], profile: Dict[str, Any]) -> float:
    """
    Preference score in [0, 1] based on:
    - prefer_meats (positive)
    - avoid_meats (negative)
    - favorite_tags (positive)
    """
    ingredients_text = " ".join(_extract_core_ingredients(recipe)).lower()
    tags = {str(t).strip().lower() for t in (recipe.get("tags") or [])}

    prefer_meats = _lower_list(profile.get("prefer_meats", []))
    avoid_meats = _lower_list(profile.get("avoid_meats", []))
    favorite_tags = _lower_list(profile.get("favorite_tags", []))

    score = 0.0

    for meat in prefer_meats:
        if meat and meat in ingredients_text:
            score += 0.3

    for meat in avoid_meats:
        if meat and meat in ingredients_text:
            score -= 0.5

    for tag in favorite_tags:
        if tag and tag in tags:
            score += 0.2

    # clamp
    if score < 0.0:
        score = 0.0
    if score > 1.0:
        score = 1.0
    return score


def _compute_price_contribution_for_ingredient(
    name: str,
    baseline_price: Optional[float],
    deals: Sequence[Deal],
    reasons_out: List[str],
) -> float:
    """
    Contribution in [0, 1] for a single ingredient.
    Adds textual reasons into reasons_out when appropriate.
    """
    ing_low = name.lower()
    relevant = [d for d in deals if ing_low in d.name.lower()]

    if not relevant and baseline_price is None:
        return 0.0

    deal_price = None
    best_deal: Optional[Deal] = None
    for d in relevant:
        if d.price is None:
            continue
        if deal_price is None or d.price < deal_price:
            deal_price = d.price
            best_deal = d

    if baseline_price is not None and baseline_price > 0 and deal_price is not None:
        discount = (baseline_price - deal_price) / baseline_price
        discount = max(0.0, min(1.0, discount))
        if discount >= 0.15 and best_deal is not None:
            pct = int(discount * 100)
            reasons_out.append(
                f"{name} is about {pct}% below your usual price at {best_deal.store}."
            )
        return discount

    if deal_price is not None and baseline_price is None:
        # Some upside, but we don’t know how good vs history.
        if best_deal is not None:
            reasons_out.append(
                f"{name} is on sale at {best_deal.store} (price {best_deal.price})."
            )
        return 0.15

    # baseline exists but no current deal
    return 0.0


def _compute_price_score_for_recipe(
    recipe: Dict[str, Any],
    price_history_service: Any,
    deals_by_ingredient: Dict[str, List[Deal]],
    reasons_out: List[str],
    baseline_window_days: int = 90,
) -> float:
    ingredients = _extract_core_ingredients(recipe)
    if not ingredients:
        return 0.0

    contributions: List[float] = []

    for ing in ingredients:
        ing_low = ing.lower()

        baseline = None
        if price_history_service is not None:
            try:
                baseline = price_history_service.get_baseline_price(
                    ing_low,
                    window_days=baseline_window_days,
                )
            except AttributeError:
                baseline = None

        deals = deals_by_ingredient.get(ing_low, [])
        contrib = _compute_price_contribution_for_ingredient(
            ing_low, baseline, deals, reasons_out
        )
        contributions.append(contrib)

    if not contributions:
        return 0.0

    avg = sum(contributions) / len(contributions)
    if avg < 0.0:
        avg = 0.0
    if avg > 1.0:
        avg = 1.0
    return avg


def _compute_variety_score(
    recipe: Dict[str, Any],
    recently_used_recipe_ids: Optional[Iterable[Any]],
) -> float:
    """
    Very simple variety heuristic:
    - If recipe ID appears in recently_used_recipe_ids -> small penalty.
    """
    if not recently_used_recipe_ids:
        return 0.0

    rid = recipe.get("id")
    if rid is None:
        return 0.0

    if rid in recently_used_recipe_ids:
        return -0.2
    return 0.0


def _collect_all_ingredients(recipes: Sequence[Dict[str, Any]]) -> List[str]:
    seen = set()
    result: List[str] = []
    for r in recipes:
        for ing in _extract_core_ingredients(r):
            low = ing.lower()
            if low not in seen:
                seen.add(low)
                result.append(low)
    return result


def _fetch_deals_for_ingredients(
    ingredients: Sequence[str],
) -> Dict[str, List[Deal]]:
    """
    Query active flyer deals from the local DB (flyer_deals + flyer_batches).

    Single query pulls all deals valid today; ingredient matching is done in
    Python via substring search. No API calls — the Flipp client populates
    these tables during the background flyer sync.

    Tracked items (items.is_tracked = 1) could additionally be searched via
    the live API, but that is deferred until the Flipp client is wired up.
    """
    import datetime as _dt
    from Grocery_Sense.data.connection import get_connection

    today = _dt.date.today().isoformat()

    try:
        with get_connection() as conn:
            rows = conn.execute(
                """
                SELECT
                    d.title,
                    d.description,
                    COALESCE(d.unit_price, d.norm_unit_price, d.deal_total) AS price,
                    d.unit,
                    s.name AS store_name
                FROM flyer_deals d
                JOIN flyer_batches b ON b.id = d.flyer_id
                LEFT JOIN stores s ON s.id = d.store_id
                WHERE b.status = 'active'
                  AND b.valid_from <= ?
                  AND b.valid_to   >= ?
                """,
                (today, today),
            ).fetchall()
    except Exception:
        rows = []

    # Build a flat list of (searchable_text, Deal) pairs once
    local_deals: List[tuple] = []
    for r in rows:
        title = str(r["title"] or r["description"] or "").lower()
        price_val = r["price"]
        price = float(price_val) if price_val is not None else None
        unit = str(r["unit"] or "each").strip()
        store = str(r["store_name"] or "")
        local_deals.append((
            title,
            Deal(name=title, store=store, price=price, unit=unit, raw={}),
        ))

    # Match each ingredient against deal titles in one pass per ingredient
    out: Dict[str, List[Deal]] = {}
    for ing in ingredients:
        ing_low = ing.lower().strip()
        if not ing_low:
            continue
        matched = [deal for text, deal in local_deals if ing_low in text or text in ing_low]
        out[ing_low] = matched

    return out


# ---------------------------------------------------------------------------
# MealSuggestionService
# ---------------------------------------------------------------------------


class MealSuggestionService:
    """
    Suggests recipes based on:
    - user profile (via config_store or passed-in)
    - recipe set (via RecipeEngine or passed-in)
    - flyer deals (deals_service)
    - receipt-based historical prices (PriceHistoryService)
    """

    def __init__(
        self,
        price_history_service: Any | None = None,
        recipe_engine: RecipeEngine | None = None,
    ) -> None:
        self.price_history_service = price_history_service
        self.recipe_engine = recipe_engine or RecipeEngine()

    # ---- Public API -----------------------------------------------------

    def suggest_meals_for_week(
        self,
        profile: Optional[Dict[str, Any]] = None,
        target_ingredients: Optional[Iterable[str]] = None,
        max_recipes: int = 6,
        recently_used_recipe_ids: Optional[Iterable[Any]] = None,
    ) -> List[SuggestedMeal]:
        """
        High-level entrypoint.

        profile:
            If None, uses config_store.get_user_profile().

        target_ingredients:
            - If provided, we first filter recipes using
              filter_recipes_by_ingredients_and_profile().
            - If None/empty, we consider all recipes, and let scoring decide.

        max_recipes:
            Number of suggestions to return.

        recently_used_recipe_ids:
            Optional set/list of recipe IDs cooked recently; helps encourage variety.
        """
        if profile is None:
            profile = get_user_profile()

        # 1) Get candidate recipes
        if target_ingredients:
            recipes = filter_recipes_by_ingredients_and_profile(
                include_ingredients=target_ingredients,
                profile=profile,
                max_results=200,  # big enough, scoring will narrow down
            )
        else:
            recipes = load_all_recipes()

        # Safety: re-check hard constraints, in case recipes.json changed
        filtered: List[Dict[str, Any]] = []
        for r in recipes:
            if _recipe_has_disallowed_ingredients(r, profile):
                continue
            filtered.append(r)

        if not filtered:
            return []

        # 2) Load active flyer deals from local DB, matched to recipe ingredients
        all_ingredients = _collect_all_ingredients(filtered)
        deals_by_ingredient = _fetch_deals_for_ingredients(all_ingredients)

        # 3) Score each recipe
        suggestions: List[SuggestedMeal] = []

        for r in filtered:
            reasons: List[str] = []

            price_score = _compute_price_score_for_recipe(
                r,
                self.price_history_service,
                deals_by_ingredient,
                reasons,
            )
            preference_score = _compute_preference_score(r, profile)
            variety_score = _compute_variety_score(r, recently_used_recipe_ids)

            # Choice C weighting:
            #  - price_score       -> 0.5
            #  - preference_score  -> 0.3
            #  - variety_score     -> 0.2
            total = (0.5 * price_score) + (0.3 * preference_score) + (0.2 * variety_score)

            # Add some generic reasons when scores are non-zero
            if preference_score > 0.5:
                reasons.append("Matches your meat or tag preferences.")
            if variety_score < 0:
                reasons.append("You cooked this recently, slightly deprioritized.")

            suggestions.append(
                SuggestedMeal(
                    recipe=r,
                    total_score=total,
                    deal_score=0.0,
                    price_score=price_score,
                    preference_score=preference_score,
                    variety_score=variety_score,
                    reasons=reasons,
                )
            )

        # 4) Sort & truncate
        suggestions.sort(key=lambda s: s.total_score, reverse=True)
        return suggestions[:max_recipes]

def format_meal_explanation(
    recipe_name: str,
    preference_score: float,
    deal_score: float,
    price_score: float,
    variety_score: float,
    reasons: list[str],
    max_reasons: int = 4,
) -> str:
    """
    Build a human-readable explanation string for why a meal was suggested.

    This is intentionally generic and does NOT depend on any particular
    dataclass type – you pass in the pieces you already have:
      - recipe_name:   display name of the recipe
      - preference_score: how well it fits the user's preferences (0–1 or similar)
      - deal_score:    how much current flyer deals helped this recipe
      - price_score:   how good the current prices look vs historical
      - variety_score: how much it improves variety vs recent meals
      - reasons:       a flat list of specific human-readable bullet points
      - max_reasons:   how many of those reasons to include

    Returns:
      A multi-line string you can show in UI, logs, or debug tools.
    """

    lines: list[str] = []

    lines.append(f"Why we suggested '{recipe_name}':")

    # High-level summary line based on scores
    summary_bits: list[str] = []

    # You can tune these thresholds to your scoring scale
    if preference_score > 0.3:
        summary_bits.append("matches your eating preferences")
    if deal_score > 0.2:
        summary_bits.append("uses ingredients that are on sale this week")
    if price_score > 0.2:
        summary_bits.append("is cheaper than your usual prices")
    if variety_score > 0.2:
        summary_bits.append("adds variety compared to your recent meals")

    if summary_bits:
        lines.append(" • " + "; ".join(summary_bits) + ".")
    else:
        lines.append(" • Overall a reasonable match based on your profile and history.")

    # Detailed bullet reasons (already assembled elsewhere)
    if reasons:
        lines.append("")
        lines.append("Details:")
        for r in reasons[:max_reasons]:
            lines.append(f" • {r}")

    return "\n".join(lines)


def explain_suggested_meal(meal: "SuggestedMeal") -> str:
    """Wrapper that unpacks a SuggestedMeal into format_meal_explanation."""
    recipe_name = meal.recipe.get("name", "Unknown recipe") if meal.recipe else "Unknown recipe"
    return format_meal_explanation(
        recipe_name=recipe_name,
        preference_score=meal.preference_score,
        deal_score=meal.deal_score,
        price_score=meal.price_score,
        variety_score=meal.variety_score,
        reasons=meal.reasons,
    )

