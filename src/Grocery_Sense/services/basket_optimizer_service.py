from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

# Repos (DB)
from Grocery_Sense.data.repositories import shopping_list_repo, stores_repo, prices_repo
from Grocery_Sense.data.repositories.prices_repo import (
    get_most_recent_prices_by_store_batch,
    get_most_recent_prices_global_batch,
    get_price_stats_batch,
)

# Preferences (optional; code fails-safe if not present)
try:
    from Grocery_Sense.services import preferences_service
    from Grocery_Sense.config import config_store
except Exception:  # pragma: no cover
    preferences_service = None  # type: ignore
    config_store = None  # type: ignore


# ---------------------------------------------------------------------------
# Small “phrase-safe” matcher (reduces false positives)
# ---------------------------------------------------------------------------

# Things we never want preference excludes to accidentally “hit”
# (Ex: "olives" should NOT flag "olive oil")
DEFAULT_EXCLUDE_SAFE_PHRASES: List[str] = [
    "olive oil",
]

def _norm(s: str) -> str:
    return (s or "").strip().lower()

def _today_date() -> _dt.date:
    return _dt.date.today()

def _parse_date(s: Any) -> Optional[_dt.date]:
    try:
        if isinstance(s, _dt.date):
            return s
        t = str(s).strip()
        if not t:
            return None
        # Accept YYYY-MM-DD (most common)
        return _dt.date.fromisoformat(t[:10])
    except Exception:
        return None

def phrase_safe_hit(
    text: str,
    term: str,
    *,
    safe_phrases: Optional[List[str]] = None,
) -> bool:
    """
    Returns True if `term` is a meaningful phrase hit in `text`,
    while trying to avoid obvious false positives.
    """
    txt = _norm(text)
    trm = _norm(term)
    if not txt or not trm:
        return False

    safe_phrases = safe_phrases or DEFAULT_EXCLUDE_SAFE_PHRASES
    for sp in safe_phrases:
        if _norm(sp) and _norm(sp) in txt and trm in {"olive", "olives"}:
            # If the text explicitly contains "olive oil", don't treat "olive/olives" as a hit.
            return False

    # Whole-word-ish match for single tokens
    if " " not in trm:
        # quick boundary check without importing regex
        # pad with spaces and replace punctuation-ish with spaces
        cleaned = "".join(ch if ch.isalnum() else " " for ch in txt)
        tokens = [t for t in cleaned.split() if t]
        return trm in tokens

    # Phrase match for multi-word terms
    return trm in txt


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class PricePick:
    store_id: int
    store_name: str
    unit_price: Optional[float]   # None => unknown
    unit: str
    source: str                  # "flyer" | "history_store" | "history_any" | "unknown"


@dataclass
class BasketItemPlan:
    item_id: int
    name: str
    quantity: float
    unit: str

    # preference annotations (for UI tooltips)
    starred: bool = False
    hard_excluded: bool = False
    soft_hits: List[Tuple[str, List[str]]] = field(default_factory=list)  # [(ingredient_hit, [members])]

    # pricing
    chosen: Optional[PricePick] = None
    usual_avg_unit_price_180d: Optional[float] = None
    lowest_unit_price_180d: Optional[float] = None


@dataclass
class StorePlan:
    store_id: int
    store_name: str
    items: List[BasketItemPlan] = field(default_factory=list)
    total_estimated: float = 0.0
    unknown_count: int = 0


@dataclass
class BasketOptimizationResult:
    mode: str  # "one_store" | "two_store"
    stores: List[StorePlan] = field(default_factory=list)

    basket_total_estimated: float = 0.0
    basket_usual_avg_estimated: Optional[float] = None
    basket_lowest_estimated: Optional[float] = None

    save_vs_usual_avg: Optional[float] = None
    save_vs_lowest: Optional[float] = None

    warnings: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Basket optimizer service
# ---------------------------------------------------------------------------

class BasketOptimizerService:
    """
    Milestone 3:
    - Uses your ACTIVE shopping list as the basket
    - Considers ONLY stores in your DB (stores table)
    - If flyer data exists (prices.source='flyer' joined to flyer_sources with active valid_from/to),
      it uses that for estimates; otherwise it falls back to most recent historical prices.
    - Computes:
        * Estimated total for 1-store (fast trip) or max-2-store (savings mode)
        * You save $X vs usual basket (avg over last 6 months / 180 days)
        * You save $Y vs lowest price seen (last 6 months / 180 days)
    - Stars soft-excluded items and provides “why” details (ingredient hit + member list)
    """

    def __init__(self) -> None:
        pass

    def optimize(self, *, mode: str = "two_store") -> BasketOptimizationResult:
        """
        mode:
          - "one_store" => pick the single best store
          - "two_store" => pick up to two stores (savings mode)
        """
        mode = (mode or "").strip().lower()
        if mode not in {"one_store", "two_store"}:
            mode = "two_store"

        basket_items = shopping_list_repo.list_active_items()
        stores = stores_repo.list_stores()

        result = BasketOptimizationResult(mode=mode)

        if not basket_items:
            result.warnings.append("Your active shopping list is empty.")
            return result

        if not stores:
            result.warnings.append("No stores found in your database. Add stores first.")
            return result

        # Build preference context (fail-safe)
        eff = None
        safe_phrases = list(DEFAULT_EXCLUDE_SAFE_PHRASES)
        if preferences_service is not None:
            try:
                eff = preferences_service.compute_effective_preferences()
            except Exception:
                eff = None

        # Normalize basket items and precompute stats
        normalized: List[BasketItemPlan] = []
        for it in basket_items:
            # shopping_list_repo returns ShoppingListItem dataclass (id, item_id, name, quantity, unit, etc.)
            try:
                item_id = int(getattr(it, "item_id", 0) or 0)
            except Exception:
                item_id = 0
            if item_id <= 0:
                # Can't optimize without item_id (in this milestone)
                continue

            name = str(getattr(it, "display_name", "") or "").strip() or f"Item {item_id}"
            unit = str(getattr(it, "unit", "") or "").strip().lower() or "each"
            try:
                qty = float(getattr(it, "quantity", 1.0) or 1.0)
            except Exception:
                qty = 1.0
            if qty <= 0:
                qty = 1.0

            plan = BasketItemPlan(item_id=item_id, name=name, quantity=qty, unit=unit)

            # preference annotations (optional)
            if eff is not None:
                self._apply_preference_annotations(plan, eff, safe_phrases)

            normalized.append(plan)

        if not normalized:
            result.warnings.append("No optimizable items found (missing item_id on shopping list entries).")
            return result

        all_item_ids = [p.item_id for p in normalized]

        # Batch-load stats for savings lines (avg + min over 180 days) — 1 query
        stats_map = get_price_stats_batch(all_item_ids, since_days=180)
        for plan in normalized:
            s = stats_map.get(plan.item_id)
            if s and s.count > 0:
                plan.usual_avg_unit_price_180d = s.avg_price
                plan.lowest_unit_price_180d = s.min_price

        # Estimate per-store unit prices for each item
        store_map: Dict[int, str] = {s.id: s.name for s in stores}
        all_store_ids = list(store_map.keys())

        # 1 query: active flyer prices
        flyer_map = self._load_active_flyer_unit_prices(
            item_ids=all_item_ids,
            store_ids=all_store_ids,
        )

        # 2 queries (batch): most-recent per (item, store) + global any-store fallback
        store_history = get_most_recent_prices_by_store_batch(all_item_ids, all_store_ids)
        global_history = get_most_recent_prices_global_batch(all_item_ids)

        # Build price matrix from in-memory dicts — 0 extra DB calls
        price_matrix: Dict[Tuple[int, int], PricePick] = {}
        for store_id, store_name in store_map.items():
            for p in normalized:
                price_matrix[(store_id, p.item_id)] = self._pick_price_from_maps(
                    item_id=p.item_id,
                    store_id=store_id,
                    store_name=store_name,
                    flyer_map=flyer_map,
                    store_history=store_history,
                    global_history=global_history,
                )

        # Choose best store(s)
        if mode == "one_store":
            chosen_store_ids = [self._choose_best_single_store(normalized, stores, price_matrix)]
        else:
            chosen_store_ids = self._choose_best_two_stores(normalized, stores, price_matrix)

        # Build store plans and assign items to stores
        store_plans: Dict[int, StorePlan] = {sid: StorePlan(store_id=sid, store_name=store_map[sid]) for sid in chosen_store_ids}

        # Assign each item to the store where it’s cheapest (or known)
        for item in normalized:
            best_sid = self._best_store_for_item(item.item_id, chosen_store_ids, price_matrix)
            chosen = price_matrix.get((best_sid, item.item_id))
            item.chosen = chosen
            store_plans[best_sid].items.append(item)

        # Totals + unknown counts
        basket_total = 0.0
        unknown_total = 0
        for sp in store_plans.values():
            total = 0.0
            unknown = 0
            for item in sp.items:
                unit_price = item.chosen.unit_price if item.chosen else None
                if unit_price is None:
                    unknown += 1
                    continue
                total += unit_price * item.quantity
            sp.total_estimated = float(total)
            sp.unknown_count = int(unknown)
            basket_total += sp.total_estimated
            unknown_total += sp.unknown_count

        result.stores = list(store_plans.values())
        result.basket_total_estimated = float(basket_total)

        # Savings lines
        avg_total = 0.0
        lowest_total = 0.0
        avg_unknown = 0
        low_unknown = 0
        for item in normalized:
            if item.usual_avg_unit_price_180d is None:
                avg_unknown += 1
            else:
                avg_total += float(item.usual_avg_unit_price_180d) * item.quantity

            if item.lowest_unit_price_180d is None:
                low_unknown += 1
            else:
                lowest_total += float(item.lowest_unit_price_180d) * item.quantity

        result.basket_usual_avg_estimated = None if avg_unknown == len(normalized) else float(avg_total)
        result.basket_lowest_estimated = None if low_unknown == len(normalized) else float(lowest_total)

        if result.basket_usual_avg_estimated is not None:
            result.save_vs_usual_avg = float(result.basket_usual_avg_estimated - result.basket_total_estimated)
        if result.basket_lowest_estimated is not None:
            result.save_vs_lowest = float(result.basket_lowest_estimated - result.basket_total_estimated)

        # Warnings
        if unknown_total > 0:
            result.warnings.append(
                f"{unknown_total} basket item(s) have unknown prices in the DB. Totals are partial estimates."
            )
        if mode == "two_store" and len(chosen_store_ids) == 2:
            result.warnings.append(
                "Two-store mode may save more, but requires an extra trip (time + gas)."
            )

        # Preference warnings
        hard_hits = sum(1 for it in normalized if it.hard_excluded)
        if hard_hits:
            result.warnings.append(
                f"{hard_hits} basket item(s) match a household HARD exclude (or allergy). Double-check these."
            )

        # sort store plans by total
        result.stores.sort(key=lambda x: x.total_estimated)

        return result

    # ---------------------------------------------------------------------
    # Preferences helpers
    # ---------------------------------------------------------------------

    def _apply_preference_annotations(self, item: BasketItemPlan, eff: Any, safe_phrases: List[str]) -> None:
        name = _norm(item.name)

        # Hard excludes: household-level (allergies + master hard excludes)
        hard_terms = list(getattr(eff, "hard_excludes", set()) or [])
        for term in hard_terms:
            if phrase_safe_hit(name, term, safe_phrases=safe_phrases):
                item.hard_excluded = True
                break

        # Soft excludes: map term -> members
        soft_map = getattr(eff, "soft_excludes", {}) or {}
        hits: List[Tuple[str, List[str]]] = []
        starred = False
        for term, members in soft_map.items():
            if not term:
                continue
            if phrase_safe_hit(name, term, safe_phrases=safe_phrases):
                mems = list(members or [])
                hits.append((str(term), mems))
                # star only if any SECONDARY member is involved
                if preferences_service is not None:
                    try:
                        # we consider "star excluders" the SECONDARY excluders
                        # so if any members exist and not just master => star
                        master_name = getattr(config_store.get_master_member(), "name", "Master")  # type: ignore
                        if any(m != master_name for m in mems):
                            starred = True
                    except Exception:
                        starred = True
                else:
                    starred = True

        item.soft_hits = hits
        item.starred = starred

    # ---------------------------------------------------------------------
    # Price selection helpers
    # ---------------------------------------------------------------------

    def _pick_price_from_maps(
        self,
        *,
        item_id: int,
        store_id: int,
        store_name: str,
        flyer_map: Dict[Tuple[int, int], Tuple[float, str]],
        store_history: Dict[Tuple[int, int], Any],
        global_history: Dict[int, Any],
    ) -> PricePick:
        """In-memory version of _pick_price_for_item_store — no DB calls."""
        # 1) Active flyer price
        flyer = flyer_map.get((store_id, item_id))
        if flyer:
            unit_price, unit = flyer
            return PricePick(store_id=store_id, store_name=store_name,
                             unit_price=unit_price, unit=unit, source="flyer")

        # 2) Most recent store-specific history
        pr = store_history.get((item_id, store_id))
        if pr and getattr(pr, "unit_price", None) is not None:
            return PricePick(
                store_id=store_id, store_name=store_name,
                unit_price=float(pr.unit_price),
                unit=str(getattr(pr, "unit", None) or "each").strip().lower(),
                source="history_store",
            )

        # 3) Global any-store fallback
        pr2 = global_history.get(item_id)
        if pr2 and getattr(pr2, "unit_price", None) is not None:
            return PricePick(
                store_id=store_id, store_name=store_name,
                unit_price=float(pr2.unit_price),
                unit=str(getattr(pr2, "unit", None) or "each").strip().lower(),
                source="history_any",
            )

        return PricePick(store_id=store_id, store_name=store_name,
                         unit_price=None, unit="each", source="unknown")

    def _pick_price_for_item_store(
        self,
        *,
        item_id: int,
        store_id: int,
        store_name: str,
        flyer_map: Dict[Tuple[int, int], Tuple[float, str]],
    ) -> PricePick:
        """
        Priority:
          1) Active flyer unit_price (if exists)
          2) Most recent historical price for that store
          3) Most recent historical price any store
          4) Unknown
        """
        # 1) Flyer
        flyer = flyer_map.get((store_id, item_id))
        if flyer:
            unit_price, unit = flyer
            return PricePick(store_id=store_id, store_name=store_name, unit_price=unit_price, unit=unit, source="flyer")

        # 2) Most recent store-specific history
        pr = prices_repo.get_most_recent_price(item_id=item_id, store_id=store_id)
        if pr and getattr(pr, "unit_price", None) is not None:
            return PricePick(
                store_id=store_id,
                store_name=store_name,
                unit_price=float(pr.unit_price),
                unit=str(getattr(pr, "unit", None) or "each").strip().lower(),
                source="history_store",
            )

        # 3) Most recent any-store history (global estimate fallback)
        pr2 = prices_repo.get_most_recent_price(item_id=item_id, store_id=None)
        if pr2 and getattr(pr2, "unit_price", None) is not None:
            return PricePick(
                store_id=store_id,
                store_name=store_name,
                unit_price=float(pr2.unit_price),
                unit=str(getattr(pr2, "unit", None) or "each").strip().lower(),
                source="history_any",
            )

        # 4) Unknown
        return PricePick(
            store_id=store_id,
            store_name=store_name,
            unit_price=None,
            unit="each",
            source="unknown",
        )

    def _best_store_for_item(
        self,
        item_id: int,
        store_ids: List[int],
        price_matrix: Dict[Tuple[int, int], PricePick],
    ) -> int:
        """
        Choose the store with the lowest known unit_price. If only one store has known price, pick it.
        """
        best_sid = store_ids[0]
        best_price: Optional[float] = None
        for sid in store_ids:
            pick = price_matrix.get((sid, item_id))
            p = pick.unit_price if pick else None
            if p is None:
                continue
            if best_price is None or p < best_price:
                best_price = p
                best_sid = sid
        return best_sid

    # ---------------------------------------------------------------------
    # Store selection
    # ---------------------------------------------------------------------

    def _score_store(self, s: Any, total: float, unknown: int) -> float:
        “””
        Base store score: estimated cost + unknown-item penalty + favourite/priority bonus.
        Lower is better.
        “””
        score = total + (unknown * 5.0)
        try:
            if bool(getattr(s, “is_favorite”, False)):
                score *= 0.985
            pr = int(getattr(s, “priority”, 0) or 0)
            if pr > 0:
                score *= max(0.97, 1.0 - (min(pr, 10) * 0.002))
        except Exception:
            pass
        return score

    def _choose_best_single_store(
        self,
        items: List[BasketItemPlan],
        stores: List[Any],
        price_matrix: Dict[Tuple[int, int], PricePick],
    ) -> int:
        “””
        Best single store by estimated total cost, with a small bonus for favorites/priority.
        “””
        best_id = int(stores[0].id)
        best_score: Optional[float] = None

        for s in stores:
            sid = int(s.id)
            total = 0.0
            unknown = 0
            for it in items:
                pick = price_matrix.get((sid, it.item_id))
                if not pick or pick.unit_price is None:
                    unknown += 1
                    continue
                total += pick.unit_price * it.quantity

            score = self._score_store(s, total, unknown)
            if best_score is None or score < best_score:
                best_score = score
                best_id = sid

        return best_id

    def _choose_best_two_stores(
        self,
        items: List[BasketItemPlan],
        stores: List[Any],
        price_matrix: Dict[Tuple[int, int], PricePick],
    ) -> List[int]:
        “””
        Choose up to two stores. We:
          1) rank stores by single-store score
          2) evaluate store pairs among top K candidates
          3) pick pair with lowest basket assignment total + small travel penalty
        “””
        if len(stores) == 1:
            return [int(stores[0].id)]

        # Step 1: rank by single-store score
        ranked = []
        for s in stores:
            sid = int(s.id)
            total = 0.0
            unknown = 0
            for it in items:
                pick = price_matrix.get((sid, it.item_id))
                if not pick or pick.unit_price is None:
                    unknown += 1
                    continue
                total += pick.unit_price * it.quantity
            ranked.append((self._score_store(s, total, unknown), sid))
        ranked.sort(key=lambda x: x[0])

        # Evaluate pairs among top K
        K = min(8, len(ranked))
        candidates = [sid for _score, sid in ranked[:K]]

        best_pair: List[int] = [candidates[0], candidates[1]]
        best_score: Optional[float] = None

        # Build once; used inside the pair loop for the favourite tie-breaker
        store_by_id = {int(s.id): s for s in stores}

        for i in range(len(candidates)):
            for j in range(i + 1, len(candidates)):
                a = candidates[i]
                b = candidates[j]
                total = 0.0
                unknown = 0

                for it in items:
                    pa = price_matrix.get((a, it.item_id))
                    pb = price_matrix.get((b, it.item_id))
                    ua = pa.unit_price if pa else None
                    ub = pb.unit_price if pb else None

                    if ua is None and ub is None:
                        unknown += 1
                        continue
                    if ua is None:
                        total += ub * it.quantity  # type: ignore
                    elif ub is None:
                        total += ua * it.quantity
                    else:
                        total += min(ua, ub) * it.quantity

                # Two-store travel penalty + weaker favourite tie-breaker
                score = total + (unknown * 5.0) + 6.0
                try:
                    if (
                        bool(getattr(store_by_id.get(a), “is_favorite”, False))
                        or bool(getattr(store_by_id.get(b), “is_favorite”, False))
                    ):
                        score *= 0.99
                except Exception:
                    pass

                if best_score is None or score < best_score:
                    best_score = score
                    best_pair = [a, b]

        return best_pair

    # ---------------------------------------------------------------------
    # Flyer loading (active by valid_from/valid_to)
    # ---------------------------------------------------------------------

    def _load_active_flyer_unit_prices(
        self,
        *,
        item_ids: List[int],
        store_ids: List[int],
    ) -> Dict[Tuple[int, int], Tuple[float, str]]:
        """
        Returns {(store_id, item_id): (unit_price, unit)} for ACTIVE flyer sources.

        Uses:
          prices.source='flyer'
          prices.flyer_source_id -> flyer_sources(id)
          flyer_sources.valid_from/valid_to must include today
        """
        # If the schema/table isn't present yet, fail-safe.
        try:
            from Grocery_Sense.data.connection import get_connection
        except Exception:
            return {}

        if not item_ids or not store_ids:
            return {}

        today = _today_date().isoformat()

        # Build query with IN clauses (safe enough for local sqlite usage)
        item_ids_csv = ",".join(str(int(x)) for x in sorted(set(item_ids)) if int(x) > 0)
        store_ids_csv = ",".join(str(int(x)) for x in sorted(set(store_ids)) if int(x) > 0)
        if not item_ids_csv or not store_ids_csv:
            return {}

        sql = f"""
            SELECT
                p.store_id AS store_id,
                p.item_id AS item_id,
                p.unit_price AS unit_price,
                COALESCE(p.unit, 'each') AS unit,
                fs.valid_from AS valid_from,
                fs.valid_to AS valid_to
            FROM prices p
            JOIN flyer_sources fs ON fs.id = p.flyer_source_id
            WHERE
                p.source = 'flyer'
                AND p.store_id IN ({store_ids_csv})
                AND p.item_id IN ({item_ids_csv})
        """

        out: Dict[Tuple[int, int], Tuple[float, str]] = {}
        try:
            with get_connection() as conn:
                conn.row_factory = getattr(conn, "row_factory", None)  # keep as-is
                cur = conn.execute(sql)
                rows = cur.fetchall()
        except Exception:
            return {}

        for r in rows:
            try:
                sid = int(r["store_id"])
                iid = int(r["item_id"])
                up = float(r["unit_price"])
                unit = str(r["unit"] or "each").strip().lower()
                vf = _parse_date(r["valid_from"])
                vt = _parse_date(r["valid_to"])
                if vf and vt:
                    td = _today_date()
                    if not (vf <= td <= vt):
                        continue
                else:
                    # If valid_from/to not set, treat as not active
                    continue

                key = (sid, iid)
                if key not in out or up < out[key][0]:
                    out[key] = (up, unit)
            except Exception:
                continue

        return out
