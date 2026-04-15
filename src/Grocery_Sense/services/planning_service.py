"""
Grocery_Sense.services.planning_service

Service layer for planning which stores to visit for the current shopping list.

This version:
  - Reads active shopping list items.
  - Reads all known stores (with favorite/priority info).
  - Looks at historical prices per item per store.
  - Chooses up to `max_stores` that cover most items, biased by favorites/priority.
  - Computes estimated basket costs and estimated savings.

Cost model (v1):
  - For each (item, store), estimate unit price = average of recent unit_price history
    over a window (days_back) with a small limit.
  - If no store-specific history exists, fall back to the item's overall average (all stores).
  - If no history exists at all for an item, it is "missing" and does not contribute to totals.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple
from statistics import mean

from Grocery_Sense.data.repositories.stores_repo import list_stores
from Grocery_Sense.data.repositories.items_repo import get_item_by_name, get_item_by_id
from Grocery_Sense.data.repositories.prices_repo import get_prices_for_item
from Grocery_Sense.domain.models import Store, ShoppingListItem, Item
from Grocery_Sense.services.shopping_list_service import ShoppingListService


class PlanningService:
    """
    High-level store planning.

    Key method:
      - build_plan_for_active_list(max_stores=3)

    Returns a dict with:
      {
        "stores": {
          store_id: {
            "store": Store,
            "items": [ShoppingListItem, ...],
            "estimated_subtotal": float | None,
            "estimated_items": int,
            "missing_items": int,
          },
          ...
        },
        "unassigned": [ShoppingListItem, ...],
        "summary": str,
        "costs": {
          "basket_total_estimate": float | None,
          "baseline_store": Store | None,
          "baseline_total_estimate": float | None,
          "estimated_savings": float | None,
          "coverage": { "total_items": int, "estimated_items": int, "missing_items": int },
        }
      }
    """

    def __init__(self) -> None:
        self._shopping = ShoppingListService()

    # ---------- Public API ----------

    def build_plan_for_active_list(
        self,
        max_stores: int = 3,
        *,
        days_back: int = 180,
        history_limit: int = 12,
    ) -> Dict[str, object]:
        """
        Build a store visit plan for all active shopping list items.

        Strategy (v1, greedy & simple):
          1) Get all active shopping list items.
          2) Get all stores.
          3) For each item, find cheapest store (by avg historical unit_price).
          4) Score stores by how many items they win, plus a bias for favorites/priority.
          5) Choose up to `max_stores` with highest scores.
          6) Assign items to their chosen store if it's in that set; otherwise
             assign to the first favorite store or the top-scoring store.

        Then:
          7) Compute estimated subtotals per store (avg unit prices from history)
          8) Compute basket total
          9) Compute baseline "all at favorite store" and estimated savings
        """
        items = self._shopping.get_active_items(include_checked_off=False, store_id=None)
        stores = list_stores()

        if not items or not stores:
            return {
                "stores": {},
                "unassigned": items or [],
                "summary": "No plan possible (no items or no stores configured).",
                "costs": {
                    "basket_total_estimate": None,
                    "baseline_store": None,
                    "baseline_total_estimate": None,
                    "estimated_savings": None,
                    "coverage": {"total_items": len(items or []), "estimated_items": 0, "missing_items": len(items or [])},
                },
            }

        # Map store_id -> Store for quick lookup
        store_by_id: Dict[int, Store] = {s.id: s for s in stores}

        # Step 3: best (cheapest) store per item using history
        item_best_store: Dict[int, Optional[int]] = {}
        for itm in items:
            best_store_id, _ = self._find_best_store_for_item(
                itm,
                stores,
                days_back=days_back,
                history_limit=history_limit,
            )
            item_best_store[itm.id] = best_store_id

        # Step 4: score stores by how many items they serve, with bias
        store_scores: Dict[int, float] = {}
        for itm in items:
            chosen_store_id = item_best_store.get(itm.id)
            if chosen_store_id is None:
                continue
            store = store_by_id.get(chosen_store_id)
            if not store:
                continue
            base = 1.0
            if store.is_favorite:
                base += 0.5
            base += (store.priority or 0) * 0.1
            store_scores[chosen_store_id] = store_scores.get(chosen_store_id, 0.0) + base

        if not store_scores:
            # No price history at all; fall back to favorites / highest priority
            chosen_store_ids = self._fallback_stores(stores, max_stores)
        else:
            chosen_store_ids = [
                s_id
                for s_id, _ in sorted(
                    store_scores.items(),
                    key=lambda kv: kv[1],
                    reverse=True,
                )[:max_stores]
            ]

        # Step 6: assign items to stores, or leave unassigned
        plan_by_store: Dict[int, List[ShoppingListItem]] = {sid: [] for sid in chosen_store_ids}
        unassigned: List[ShoppingListItem] = []

        # Choose a generic fallback store if needed
        fallback_store_id = self._choose_generic_fallback_store(stores, chosen_store_ids)

        for itm in items:
            best_store_id = item_best_store.get(itm.id)
            if best_store_id in chosen_store_ids:
                plan_by_store[best_store_id].append(itm)
            elif fallback_store_id is not None:
                plan_by_store.setdefault(fallback_store_id, []).append(itm)
            else:
                unassigned.append(itm)

        # ---------- Cost estimates ----------
        baseline_store = self._choose_baseline_store(stores)
        cost_results = self._compute_costs(
            plan_by_store=plan_by_store,
            unassigned=unassigned,
            stores=stores,
            baseline_store=baseline_store,
            days_back=days_back,
            history_limit=history_limit,
        )

        # Build summary (now includes cost lines)
        summary = self._build_summary(
            plan_by_store=plan_by_store,
            unassigned=unassigned,
            store_by_id=store_by_id,
            costs=cost_results,
        )

        # Convert to final structure with Store objects + store-level costs
        stores_struct: Dict[int, Dict[str, object]] = {}
        for sid, its in plan_by_store.items():
            st = store_by_id.get(sid)
            if not st:
                continue

            per_store = cost_results["per_store"].get(sid, {})
            stores_struct[sid] = {
                "store": st,
                "items": its,
                "estimated_subtotal": per_store.get("estimated_subtotal"),
                "estimated_items": per_store.get("estimated_items", 0),
                "missing_items": per_store.get("missing_items", 0),
            }

        return {
            "stores": stores_struct,
            "unassigned": unassigned,
            "summary": summary,
            "costs": {
                "basket_total_estimate": cost_results.get("basket_total_estimate"),
                "baseline_store": baseline_store,
                "baseline_total_estimate": cost_results.get("baseline_total_estimate"),
                "estimated_savings": cost_results.get("estimated_savings"),
                "coverage": cost_results.get("coverage"),
            },
        }

    # ---------- Internal helpers ----------

    def _resolve_item(self, shopping_item: ShoppingListItem) -> Optional[Item]:
        """
        Resolve a ShoppingListItem to a canonical Item row if possible.
        Prefer shopping_item.item_id if populated; otherwise try name lookup.
        """
        if shopping_item.item_id:
            it = get_item_by_id(int(shopping_item.item_id))
            if it:
                return it

        name = (shopping_item.display_name or "").strip()
        if not name:
            return None
        return get_item_by_name(name)

    def _avg_price_for_item_store(
        self,
        item_id: int,
        store_id: Optional[int],
        *,
        days_back: int,
        history_limit: int,
    ) -> Optional[float]:
        pts = get_prices_for_item(
            item_id=item_id,
            since_days=days_back,
            store_id=store_id,
        )
        prices = [p.unit_price for p in pts if p.unit_price is not None]
        if not prices:
            return None
        return float(mean(prices))

    def _estimate_unit_price(
        self,
        item_id: int,
        store_id: int,
        *,
        days_back: int,
        history_limit: int,
        overall_fallback_cache: Dict[int, Optional[float]],
        store_cache: Dict[Tuple[int, int], Optional[float]],
    ) -> Optional[float]:
        """
        Estimate unit price for (item, store):
          - store-specific average
          - fallback to overall average for the item (all stores)
        """
        key = (item_id, store_id)
        if key not in store_cache:
            store_cache[key] = self._avg_price_for_item_store(
                item_id=item_id,
                store_id=store_id,
                days_back=days_back,
                history_limit=history_limit,
            )
        if store_cache[key] is not None:
            return store_cache[key]

        if item_id not in overall_fallback_cache:
            overall_fallback_cache[item_id] = self._avg_price_for_item_store(
                item_id=item_id,
                store_id=None,
                days_back=days_back,
                history_limit=max(history_limit, 20),
            )
        return overall_fallback_cache[item_id]

    def _compute_costs(
        self,
        *,
        plan_by_store: Dict[int, List[ShoppingListItem]],
        unassigned: List[ShoppingListItem],
        stores: List[Store],
        baseline_store: Optional[Store],
        days_back: int,
        history_limit: int,
    ) -> Dict[str, object]:
        """
        Compute:
          - per-store estimated subtotal
          - basket total estimate
          - baseline total estimate ("all at baseline_store")
          - savings (baseline - plan)
          - coverage stats
        """
        overall_fallback_cache: Dict[int, Optional[float]] = {}
        store_cache: Dict[Tuple[int, int], Optional[float]] = {}

        total_items = sum(len(v) for v in plan_by_store.values()) + len(unassigned)

        per_store: Dict[int, Dict[str, object]] = {}
        basket_total = 0.0
        basket_has_any = False

        estimated_items = 0
        missing_items = 0

        # Plan split
        for store_id, items in plan_by_store.items():
            subtotal = 0.0
            store_has_any = False
            store_estimated = 0
            store_missing = 0

            for s_item in items:
                item_row = self._resolve_item(s_item)
                qty = float(s_item.quantity) if s_item.quantity is not None else 1.0

                if not item_row:
                    missing_items += 1
                    store_missing += 1
                    continue

                unit_price = self._estimate_unit_price(
                    item_id=item_row.id,
                    store_id=store_id,
                    days_back=days_back,
                    history_limit=history_limit,
                    overall_fallback_cache=overall_fallback_cache,
                    store_cache=store_cache,
                )

                if unit_price is None:
                    missing_items += 1
                    store_missing += 1
                    continue

                est = float(unit_price) * qty
                subtotal += est
                store_has_any = True
                basket_has_any = True

                estimated_items += 1
                store_estimated += 1

            per_store[store_id] = {
                "estimated_subtotal": round(subtotal, 2) if store_has_any else None,
                "estimated_items": store_estimated,
                "missing_items": store_missing,
            }

            basket_total += subtotal

        basket_total_estimate = round(basket_total, 2) if basket_has_any else None

        # Baseline: all items at baseline store
        baseline_total = 0.0
        baseline_has_any = False
        if baseline_store:
            for store_id, items in plan_by_store.items():
                for s_item in items:
                    item_row = self._resolve_item(s_item)
                    qty = float(s_item.quantity) if s_item.quantity is not None else 1.0
                    if not item_row:
                        continue
                    unit_price = self._estimate_unit_price(
                        item_id=item_row.id,
                        store_id=baseline_store.id,
                        days_back=days_back,
                        history_limit=history_limit,
                        overall_fallback_cache=overall_fallback_cache,
                        store_cache=store_cache,
                    )
                    if unit_price is None:
                        continue
                    baseline_total += float(unit_price) * qty
                    baseline_has_any = True

        baseline_total_estimate = round(baseline_total, 2) if baseline_has_any else None

        savings = None
        if baseline_total_estimate is not None and basket_total_estimate is not None:
            savings = round(baseline_total_estimate - basket_total_estimate, 2)

        return {
            "per_store": per_store,
            "basket_total_estimate": basket_total_estimate,
            "baseline_total_estimate": baseline_total_estimate,
            "estimated_savings": savings,
            "coverage": {
                "total_items": int(total_items),
                "estimated_items": int(estimated_items),
                "missing_items": int(max(0, total_items - estimated_items)),
            },
        }

    def _find_best_store_for_item(
        self,
        shopping_item: ShoppingListItem,
        stores: List[Store],
        *,
        days_back: int = 180,
        history_limit: int = 12,
    ) -> Tuple[Optional[int], Optional[float]]:
        """
        For a given ShoppingListItem, check historical prices across stores
        and return (best_store_id, best_avg_price) or (None, None) if no data.
        """
        item_row = self._resolve_item(shopping_item)
        if not item_row:
            return None, None

        best_store_id: Optional[int] = None
        best_price: Optional[float] = None

        for store in stores:
            pts = get_prices_for_item(
                item_id=item_row.id,
                store_id=store.id,
                since_days=days_back,
            )
            prices = [p.unit_price for p in pts if p.unit_price is not None]
            if not prices:
                continue

            avg_price = float(mean(prices))
            if best_price is None or avg_price < best_price:
                best_price = avg_price
                best_store_id = store.id

        return best_store_id, best_price

    @staticmethod
    def _fallback_stores(stores: List[Store], max_stores: int) -> List[int]:
        """
        When there's no price history at all, choose stores based on:
          - favorites first
          - then by priority
          - then by name
        """
        sorted_stores = sorted(
            stores,
            key=lambda s: (
                0 if s.is_favorite else 1,
                -(s.priority or 0),
                s.name.lower(),
            ),
        )
        return [s.id for s in sorted_stores[:max_stores]]

    @staticmethod
    def _choose_generic_fallback_store(stores: List[Store], chosen_store_ids: List[int]) -> Optional[int]:
        """
        Choose a single store to use as a fallback when an item has no price history
        or its best store is outside the chosen set.
        """
        if not stores:
            return None

        chosen_stores = [s for s in stores if s.id in chosen_store_ids]

        favs = [s for s in chosen_stores if s.is_favorite]
        if favs:
            fav_sorted = sorted(favs, key=lambda s: -(s.priority or 0))
            return fav_sorted[0].id

        if chosen_stores:
            return chosen_stores[0].id

        return stores[0].id if stores else None

    @staticmethod
    def _choose_baseline_store(stores: List[Store]) -> Optional[Store]:
        """
        Baseline store for 'all at one store' comparison:
          - favorite with highest priority, else
          - highest priority store, else
          - first by name
        """
        if not stores:
            return None

        favs = [s for s in stores if s.is_favorite]
        if favs:
            return sorted(favs, key=lambda s: -(s.priority or 0))[0]

        pr = sorted(stores, key=lambda s: (-(s.priority or 0), s.name.lower()))
        return pr[0] if pr else None

    @staticmethod
    def _build_summary(
        plan_by_store: Dict[int, List[ShoppingListItem]],
        unassigned: List[ShoppingListItem],
        store_by_id: Dict[int, Store],
        costs: Dict[str, object],
    ) -> str:
        """
        Build a human-readable summary of the plan for debugging / UI.
        """
        parts: List[str] = []

        total_items = sum(len(lst) for lst in plan_by_store.values()) + len(unassigned)
        parts.append(f"Planned {total_items} item(s) across {len(plan_by_store)} store(s).")

        # Store breakdown
        per_store = costs.get("per_store", {}) if isinstance(costs, dict) else {}
        for sid, items in plan_by_store.items():
            st = store_by_id.get(sid)
            if not st:
                continue
            fav_flag = " (favorite)" if st.is_favorite else ""
            parts.append(f"- {st.name}{fav_flag}: {len(items)} item(s)")

            # cost line
            st_cost = per_store.get(sid, {}) if isinstance(per_store, dict) else {}
            subtotal = st_cost.get("estimated_subtotal")
            miss = st_cost.get("missing_items", 0)
            est_items = st_cost.get("estimated_items", 0)
            if subtotal is not None:
                parts.append(f"    est subtotal: ${subtotal:.2f}  (estimated {est_items}, missing {miss})")
            else:
                parts.append(f"    est subtotal: n/a  (estimated {est_items}, missing {miss})")

            preview_names = ", ".join(i.display_name for i in items[:5])
            if preview_names:
                parts.append(f"    e.g. {preview_names}")

        # Overall totals / savings
        basket = costs.get("basket_total_estimate") if isinstance(costs, dict) else None
        baseline = costs.get("baseline_total_estimate") if isinstance(costs, dict) else None
        savings = costs.get("estimated_savings") if isinstance(costs, dict) else None
        coverage = costs.get("coverage") if isinstance(costs, dict) else None

        if basket is not None:
            parts.append(f"Basket estimate (plan split): ${basket:.2f}")
        if baseline is not None:
            parts.append(f"Baseline estimate (all at one favorite store): ${baseline:.2f}")
        if savings is not None:
            parts.append(f"Estimated savings vs baseline: ${savings:.2f}")

        if isinstance(coverage, dict):
            parts.append(
                f"Coverage: {coverage.get('estimated_items', 0)}/{coverage.get('total_items', 0)} items estimated "
                f"({coverage.get('missing_items', 0)} missing)."
            )

        if unassigned:
            parts.append(
                "Unassigned items (no stores configured): " + ", ".join(i.display_name for i in unassigned[:5])
            )

        return "\n".join(parts)
