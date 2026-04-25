"""
M3 — deals_service (pure-logic surface)

Covers:
  - _is_meat_item keyword detection
  - group_deals_by_store
  - choose_stores_min_trips: MAX_STORES cap, singleton pruning, priority tiebreak
  - collect_favorite_ingredients: top-N frequency
  - rank_recipes_by_deals: meat-weighted scoring, empty-deals handling
  - _normalize_flier_items: tolerant field aliasing, missing-field safety

The HTTP-bound search_deals() / suggest_stores_for_term() paths are out of
scope for M3 (moved to M2 External Data Gateway coverage).
"""

from __future__ import annotations

from Grocery_Sense.services import deals_service as ds
from Grocery_Sense.services.deals_service import (
    Deal,
    _is_meat_item,
    _normalize_flier_items,
    choose_stores_min_trips,
    collect_favorite_ingredients,
    group_deals_by_store,
    rank_recipes_by_deals,
)


# ---------------------------------------------------------------------------
# _is_meat_item
# ---------------------------------------------------------------------------


class TestIsMeatItem:
    def test_recognizes_canonical_keywords(self):
        for name in ("chicken thighs", "ground beef", "pork loin", "salmon fillet"):
            assert _is_meat_item(name) is True

    def test_recognizes_compound_terms(self):
        assert _is_meat_item("family pack chicken wings") is True
        assert _is_meat_item("beef steak marinated") is True

    def test_rejects_non_meat(self):
        for name in ("apples", "bread", "olive oil", "milk 2L"):
            assert _is_meat_item(name) is False

    def test_handles_empty_and_none_safely(self):
        assert _is_meat_item("") is False
        assert _is_meat_item(None) is False  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# group_deals_by_store
# ---------------------------------------------------------------------------


class TestGroupDealsByStore:
    def test_groups_by_store_name(self):
        deals = [
            Deal(name="chicken", store="Loblaws", price=5.0),
            Deal(name="bread", store="Loblaws", price=2.5),
            Deal(name="milk", store="Save-On", price=4.0),
        ]
        grouped = group_deals_by_store(deals)
        assert set(grouped) == {"Loblaws", "Save-On"}
        assert len(grouped["Loblaws"]) == 2
        assert len(grouped["Save-On"]) == 1

    def test_missing_store_becomes_unknown(self):
        deals = [Deal(name="mystery item", store="", price=None)]
        grouped = group_deals_by_store(deals)
        assert "Unknown" in grouped


# ---------------------------------------------------------------------------
# choose_stores_min_trips
# ---------------------------------------------------------------------------


class TestChooseStoresMinTrips:
    def _make(self, **stores) -> dict:
        """stores: dict of store_name -> list of item names."""
        return {
            name: [Deal(name=item, store=name, price=1.0) for item in items]
            for name, items in stores.items()
        }

    def test_caps_at_max_stores(self):
        by_store = self._make(
            A=["chicken", "beef", "pork"],
            B=["salmon", "fish"],
            C=["apples", "bread"],
            D=["milk", "eggs"],
            E=["butter"],
        )
        chosen = choose_stores_min_trips(by_store)
        assert len(chosen) <= ds.MAX_STORES

    def test_prunes_singleton_non_meat(self):
        by_store = self._make(
            A=["chicken", "beef"],
            B=["bread"],  # singleton non-meat → pruned
        )
        chosen = choose_stores_min_trips(by_store, allow_singleton_for_meat=True)
        assert "A" in chosen
        assert "B" not in chosen

    def test_keeps_singleton_meat(self):
        by_store = self._make(
            A=["chicken", "apples"],
            B=["ground beef"],  # singleton meat → kept
        )
        chosen = choose_stores_min_trips(by_store, allow_singleton_for_meat=True)
        assert "A" in chosen
        assert "B" in chosen

    def test_priority_breaks_tie(self):
        """Same raw score → user-prioritized store gets +0.5 and wins the ordering."""
        by_store = self._make(
            A=["apples", "bread"],
            B=["milk", "eggs"],
            C=["chicken", "fish"],
        )
        # Without priority, C wins on meat-weight. With priority, we can elevate B.
        chosen = choose_stores_min_trips(by_store, store_priority=["B"])
        assert chosen[0] == "C"  # meat-weight still beats +0.5 priority
        chosen_priority_heavy = choose_stores_min_trips(
            self._make(A=["apples", "bread"], B=["milk", "eggs"]),
            store_priority=["B"],
        )
        assert chosen_priority_heavy[0] == "B"

    def test_empty_input_returns_empty(self):
        assert choose_stores_min_trips({}) == []

    def test_returns_first_chosen_when_all_pruned(self):
        """When every store is a non-meat singleton, fall back to top-scored single store."""
        by_store = self._make(A=["bread"], B=["milk"])
        chosen = choose_stores_min_trips(by_store, allow_singleton_for_meat=True)
        assert len(chosen) == 1


# ---------------------------------------------------------------------------
# collect_favorite_ingredients
# ---------------------------------------------------------------------------


class TestCollectFavoriteIngredients:
    def test_sorts_by_frequency(self):
        recipes = [
            {"ingredients": ["chicken", "onion", "garlic"]},
            {"ingredients": ["chicken", "garlic"]},
            {"ingredients": ["chicken"]},
        ]
        top = collect_favorite_ingredients(recipes)
        assert top[0] == "chicken"
        # chicken appears 3x, garlic 2x, onion 1x — chicken must be first.

    def test_caps_at_twenty(self):
        recipes = [{"ingredients": [f"ing_{i}" for i in range(50)]}]
        assert len(collect_favorite_ingredients(recipes)) == 20

    def test_empty_recipes_empty_result(self):
        assert collect_favorite_ingredients([]) == []

    def test_handles_missing_ingredients_key(self):
        assert collect_favorite_ingredients([{}, {"name": "bare"}]) == []


# ---------------------------------------------------------------------------
# rank_recipes_by_deals
# ---------------------------------------------------------------------------


class TestRankRecipesByDeals:
    def test_ranks_higher_when_more_ingredient_hits(self):
        deals = [
            Deal(name="chicken thighs", store="A", price=5.0),
            Deal(name="apples", store="B", price=2.0),
        ]
        recipes = [
            {"name": "chicken apple stew", "ingredients": ["chicken", "apples"]},
            {"name": "apple pie", "ingredients": ["apples"]},
            {"name": "random", "ingredients": ["pasta"]},
        ]
        ranked = rank_recipes_by_deals(recipes, deals)
        assert ranked[0]["name"] == "chicken apple stew"
        assert "random" not in [r["name"] for r in ranked]

    def test_meat_weight_boosts_score(self):
        deals = [
            Deal(name="ground beef", store="A", price=8.0),
            Deal(name="rice", store="B", price=3.0),
        ]
        recipes = [
            {"name": "beef dish", "ingredients": ["ground beef"]},
            {"name": "rice dish", "ingredients": ["rice"]},
        ]
        ranked = rank_recipes_by_deals(recipes, deals)
        assert ranked[0]["name"] == "beef dish"

    def test_empty_deals_returns_empty(self):
        recipes = [{"name": "r", "ingredients": ["chicken"]}]
        assert rank_recipes_by_deals(recipes, []) == []

    def test_respects_max_recipes(self):
        deals = [Deal(name="x", store="S", price=1.0)]
        recipes = [{"name": f"r{i}", "ingredients": ["x"]} for i in range(20)]
        ranked = rank_recipes_by_deals(recipes, deals, max_recipes=5)
        assert len(ranked) == 5


# ---------------------------------------------------------------------------
# _normalize_flier_items — defensive field mapping
# ---------------------------------------------------------------------------


class TestNormalizeFlierItems:
    def test_handles_items_key(self):
        payload = {
            "items": [
                {
                    "name": "chicken breast",
                    "merchant": "Superstore",
                    "current_price": 4.99,
                    "unit": "lb",
                }
            ]
        }
        deals = _normalize_flier_items(payload)
        assert len(deals) == 1
        d = deals[0]
        assert d.name == "chicken breast"
        assert d.store == "Superstore"
        assert d.price == 4.99
        assert d.unit == "lb"

    def test_falls_back_to_results_key(self):
        payload = {
            "results": [{"name": "bread", "store": "Save-On", "sale_price": 2.50}]
        }
        deals = _normalize_flier_items(payload)
        assert len(deals) == 1
        assert deals[0].price == 2.50  # sale_price taken when current_price absent

    def test_unknowns_when_fields_missing(self):
        payload = {"items": [{}]}
        deals = _normalize_flier_items(payload)
        assert len(deals) == 1
        assert deals[0].name == "Unknown item"
        assert deals[0].store == "Unknown store"
        assert deals[0].price is None
        assert deals[0].unit is None

    def test_empty_payload_returns_empty_list(self):
        assert _normalize_flier_items({}) == []

    def test_preserves_raw_payload_for_debug(self):
        raw = {"name": "x", "merchant": "y", "custom_field": 123}
        deals = _normalize_flier_items({"items": [raw]})
        assert deals[0].raw is raw
