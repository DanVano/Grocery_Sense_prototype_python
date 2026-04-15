"""
Grocery_Sense.services.deals_service

Service for:
- Fetching flyer-style deals (e.g. via Flipp or similar APIs)
- Caching raw deal JSON using config_store.cache_get/cache_set
- Grouping deals by store
- Heuristics for picking a small set of stores
- Ranking recipes by how well they match current deals

This module is deliberately decoupled from SQLite for now.
Later we can plug in the DB-backed items & price history.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import requests

from Grocery_Sense.config.config_store import (
    get_postal_code,
    get_store_priority,
    cache_get,
    cache_set,
)


# ---- Basic deal model ------------------------------------------------------


@dataclass
class Deal:
    """
    Lightweight representation of a flyer deal.

    Fields are intentionally generic and can be populated from any flyer API,
    not just Flipp.
    """
    name: str                   # "Chicken Thighs Family Pack"
    store: str                  # "Real Canadian Superstore"
    price: Optional[float]      # unit price if known (per kg or per unit)
    unit: Optional[str] = None  # "kg", "lb", "each", etc.
    raw: Optional[Dict[str, Any]] = None  # original JSON payload for debugging


# ---- Meat-aware heuristics -------------------------------------------------


MEAT_KEYWORDS = {
    "chicken", "beef", "pork", "turkey", "salmon", "fish",
    "steak", "thigh", "wings", "ribs", "ground beef", "ground pork",
}
MEAT_WEIGHT = 1.5
DEAL_BASE = 1.0
MAX_STORES = 3


def _is_meat_item(name: str) -> bool:
    low = (name or "").lower()
    return any(k in low for k in MEAT_KEYWORDS)


def group_deals_by_store(deals: List[Deal]) -> Dict[str, List[Deal]]:
    """
    Group deals by store name.
    """
    by_store: Dict[str, List[Deal]] = {}
    for d in deals:
        s = d.store or "Unknown"
        by_store.setdefault(s, []).append(d)
    return by_store


def choose_stores_min_trips(
    by_store: Dict[str, List[Deal]],
    allow_singleton_for_meat: bool = True,
    store_priority: Optional[List[str]] = None,
) -> List[str]:
    """
    Greedy selection of stores:

    - Prefer stores with more (meat-weighted) deals.
    - Cap number of stores at MAX_STORES.
    - Resolve ties using store_priority (user preference list, case-insensitive).
    - Drop stores that only provide a single item unless it's a meat/fish item
      (controlled by allow_singleton_for_meat).
    """
    store_priority = [s.lower() for s in (store_priority or [])]

    scores: List[Tuple[float, str]] = []
    for store_name, store_deals in by_store.items():
        score = 0.0
        for d in store_deals:
            score += MEAT_WEIGHT if _is_meat_item(d.name) else 1.0
        if store_name.lower() in store_priority:
            score += 0.5
        scores.append((score, store_name))

    scores.sort(reverse=True, key=lambda x: x[0])

    chosen: List[str] = []
    for _, store_name in scores:
        if len(chosen) >= MAX_STORES:
            break
        chosen.append(store_name)

    # prune stores with only one non-meat item
    pruned: List[str] = []
    for store_name in chosen:
        deals = by_store.get(store_name, [])
        if len(deals) == 1 and allow_singleton_for_meat:
            if not _is_meat_item(deals[0].name):
                continue
        pruned.append(store_name)

    return pruned or (chosen[:1] if chosen else [])


# ---- Recipe ranking against deals -----------------------------------------


def collect_favorite_ingredients(favorite_recipes: List[Dict[str, Any]]) -> List[str]:
    """
    Given a list of recipe dicts with an 'ingredients' list,
    return the top ~20 ingredient names by frequency.

    This is useful for focusing flyer searches on what users actually cook.
    """
    counts: Dict[str, int] = {}
    for r in favorite_recipes:
        for ing in r.get("ingredients", []):
            key = ing.lower().strip()
            if key:
                counts[key] = counts.get(key, 0) + 1

    sorted_items = sorted(counts.items(), key=lambda x: x[1], reverse=True)
    return [name for name, _ in sorted_items[:20]]


def rank_recipes_by_deals(
    favorite_recipes: List[Dict[str, Any]],
    deals: List[Deal],
    max_recipes: int = 9,
) -> List[Dict[str, Any]]:
    """
    Rank recipes based on how many of their ingredients have deals
    (with extra weight for meat items).

    favorite_recipes: list of dicts, each with at least:
        - "name": str
        - "ingredients": List[str]

    deals: list of Deal instances.

    Returns: top N recipe dicts (same objects as input).
    """
    deal_names = [d.name.lower() for d in deals]
    scored: List[Tuple[float, Dict[str, Any]]] = []

    for r in favorite_recipes:
        score = 0.0
        for ing in r.get("ingredients", []):
            low = ing.lower()
            hit = any(low in dn for dn in deal_names)
            if hit:
                score += DEAL_BASE + (MEAT_WEIGHT if _is_meat_item(low) else 0.0)
        if score > 0:
            scored.append((score, r))

    scored.sort(reverse=True, key=lambda x: x[0])
    return [r for _, r in scored[:max_recipes]]


# ---- External API integration (Flipp-style search) -------------------------


# NOTE:
# This URL is an example pointing at a *known* Flipp-style endpoint.
# It may change or require different parameters in the real world.
# Treat this as a starting point and be ready to adjust.
FLYER_SEARCH_URL = "https://backflipp.wishabi.com/flipp/items/search"


def _http_get_json(url: str, params: Dict[str, Any], timeout: int = 10) -> Dict[str, Any]:
    """
    Simple HTTP GET wrapper returning JSON. Raises on network/JSON errors.
    """
    resp = requests.get(url, params=params, timeout=timeout)
    resp.raise_for_status()
    return resp.json()


def _normalize_flier_items(data: Dict[str, Any]) -> List[Deal]:
    """
    Convert raw JSON from the flyer API into a list of Deal objects.

    This is intentionally conservative: you may need to adjust field names
    once you inspect the actual JSON returned by the endpoint.
    """
    items = data.get("items") or data.get("results") or []
    deals: List[Deal] = []

    for item in items:
        name = item.get("name") or item.get("brand") or "Unknown item"
        store = item.get("merchant") or item.get("store") or "Unknown store"

        # Price fields are highly API-specific. These are placeholders:
        price = None
        unit = None
        if "current_price" in item:
            price = item.get("current_price")
        elif "sale_price" in item:
            price = item.get("sale_price")

        if "unit" in item:
            unit = item.get("unit")

        deals.append(
            Deal(
                name=str(name),
                store=str(store),
                price=price,
                unit=unit,
                raw=item,
            )
        )

    return deals


def search_deals(
    term: str,
    postal_code: Optional[str] = None,
    max_age_days: int = 7,
    locale: str = "en-CA",
) -> List[Deal]:
    """
    Search flyer-style deals for a given search term and postal code.

    - Uses config_store.get_postal_code() if postal_code is not provided.
    - Caches raw JSON using config_store.cache_get/cache_set for up to max_age_days.
    - Normalizes into a list of Deal objects.

    The exact query parameters for the underlying API may need tuning once
    you inspect a real response.
    """
    term_clean = term.strip()
    if not term_clean:
        return []

    if postal_code is None:
        postal_code = get_postal_code()
    if not postal_code:
        raise ValueError("Postal code is required to search deals.")

    cache_key = f"deals:{postal_code}:{term_clean.lower()}"
    cached = cache_get(cache_key, max_age_days=max_age_days)
    if cached is not None:
        # cached format: list of dicts representing Deal, so rehydrate
        deals: List[Deal] = []
        for d in cached:
            deals.append(
                Deal(
                    name=d.get("name", ""),
                    store=d.get("store", ""),
                    price=d.get("price"),
                    unit=d.get("unit"),
                    raw=d.get("raw") or {},
                )
            )
        return deals

    # Example parameter set; adjust once you know the real API contract
    params = {
        "q": term_clean,
        "postal_code": postal_code,
        "locale": locale,
    }

    raw_json = _http_get_json(FLYER_SEARCH_URL, params=params)
    deals = _normalize_flier_items(raw_json)

    # store a JSON-serializable version in cache
    serializable = [
        {
            "name": d.name,
            "store": d.store,
            "price": d.price,
            "unit": d.unit,
            "raw": d.raw,
        }
        for d in deals
    ]
    cache_set(cache_key, serializable)

    return deals


# ---- High-level helper: suggest stores for a term --------------------------


def suggest_stores_for_term(
    term: str,
    postal_code: Optional[str] = None,
    max_age_days: int = 7,
) -> List[str]:
    """
    Convenience helper:

    - Searches deals for the given term
    - Groups deals by store
    - Uses store priorities from config_store
    - Picks a small set of stores to visit

    Returns: list of store names in recommended order.
    """
    deals = search_deals(term, postal_code=postal_code, max_age_days=max_age_days)
    if not deals:
        return []

    by_store = group_deals_by_store(deals)
    priorities = get_store_priority()
    return choose_stores_min_trips(by_store, allow_singleton_for_meat=True, store_priority=priorities)
