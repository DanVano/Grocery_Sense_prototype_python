from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional

from Grocery_Sense.data.db import get_connection


@dataclass
class StoreRow:
    id: int
    name: str


class FlyersRepo:
    """
    Stores flyer imports and deals in SQLite.

    Tables:
      - stores
      - flyer_batches   (store_id + valid_from/to)
      - flyer_deals     (individual deal rows linked to flyer_batches)
    """

    # -------------------------------------------------------------------------
    # Schema
    # -------------------------------------------------------------------------

    def ensure_schema(self) -> None:
        with get_connection() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS stores (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL
                )
                """
            )

            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS flyer_batches (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    store_id INTEGER NOT NULL,
                    valid_from TEXT,
                    valid_to TEXT,
                    source TEXT,
                    source_label TEXT,
                    imported_at TEXT NOT NULL,
                    FOREIGN KEY(store_id) REFERENCES stores(id)
                )
                """
            )

            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS flyer_deals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    flyer_id INTEGER NOT NULL,
                    store_id INTEGER NOT NULL,
                    page_index INTEGER,
                    title TEXT,
                    description TEXT,
                    price_text TEXT,
                    deal_qty REAL,
                    deal_total REAL,
                    unit_price REAL,
                    unit TEXT,
                    norm_unit_price REAL,
                    norm_unit TEXT,
                    norm_note TEXT,
                    item_id TEXT,
                    mapping_confidence REAL,
                    confidence REAL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(flyer_id) REFERENCES flyer_batches(id),
                    FOREIGN KEY(store_id) REFERENCES stores(id)
                )
                """
            )

            conn.execute("CREATE INDEX IF NOT EXISTS idx_flyer_deals_flyer_id ON flyer_deals(flyer_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_flyer_deals_store_id ON flyer_deals(store_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_flyer_batches_store_id ON flyer_batches(store_id)")

            conn.commit()

    # -------------------------------------------------------------------------
    # Stores
    # -------------------------------------------------------------------------

    def upsert_store(self, name: str) -> int:
        self.ensure_schema()
        name = (name or "").strip()
        if not name:
            raise ValueError("Store name is required")

        now = datetime.utcnow().isoformat(timespec="seconds")

        with get_connection() as conn:
            conn.execute(
                """
                INSERT INTO stores (name, created_at)
                VALUES (?, ?)
                ON CONFLICT(name) DO UPDATE SET name=excluded.name
                """,
                (name, now),
            )
            row = conn.execute("SELECT id FROM stores WHERE name = ?", (name,)).fetchone()
            if not row:
                raise RuntimeError("Failed to upsert store")
            conn.commit()
            return int(row["id"])

    def list_stores(self) -> List[StoreRow]:
        self.ensure_schema()
        with get_connection() as conn:
            rows = conn.execute("SELECT id, name FROM stores ORDER BY name ASC").fetchall()
        out: List[StoreRow] = []
        for r in rows:
            out.append(StoreRow(id=int(r["id"]), name=str(r["name"])))
        return out

    # -------------------------------------------------------------------------
    # Flyer batches
    # -------------------------------------------------------------------------

    def create_flyer_batch(
        self,
        *,
        store_id: int,
        valid_from: Optional[str],
        valid_to: Optional[str],
        source: Optional[str] = None,
        source_label: Optional[str] = None,
    ) -> int:
        self.ensure_schema()
        now = datetime.utcnow().isoformat(timespec="seconds")

        with get_connection() as conn:
            conn.execute(
                """
                INSERT INTO flyer_batches (store_id, valid_from, valid_to, source, source_label, imported_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (int(store_id), valid_from, valid_to, source, source_label, now),
            )
            flyer_id = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
            conn.commit()
            return int(flyer_id)

    # -------------------------------------------------------------------------
    # Deals
    # -------------------------------------------------------------------------

    def add_deal(
        self,
        *,
        flyer_id: int,
        store_id: int,
        page_index: Optional[int],
        title: Optional[str],
        description: Optional[str],
        price_text: Optional[str],
        deal_qty: Optional[float],
        deal_total: Optional[float],
        unit_price: Optional[float],
        unit: Optional[str],
        norm_unit_price: Optional[float],
        norm_unit: Optional[str],
        norm_note: Optional[str],
        item_id: Optional[str],
        mapping_confidence: Optional[float],
        confidence: Optional[float],
    ) -> int:
        self.ensure_schema()
        now = datetime.utcnow().isoformat(timespec="seconds")

        with get_connection() as conn:
            conn.execute(
                """
                INSERT INTO flyer_deals (
                    flyer_id, store_id, page_index, title, description, price_text,
                    deal_qty, deal_total, unit_price, unit,
                    norm_unit_price, norm_unit, norm_note,
                    item_id, mapping_confidence, confidence, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    int(flyer_id),
                    int(store_id),
                    page_index,
                    title,
                    description,
                    price_text,
                    deal_qty,
                    deal_total,
                    unit_price,
                    unit,
                    norm_unit_price,
                    norm_unit,
                    norm_note,
                    item_id,
                    mapping_confidence,
                    confidence,
                    now,
                ),
            )
            deal_id = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
            conn.commit()
            return int(deal_id)

    def list_deals_for_flyer(
        self,
        flyer_id: int,
        *,
        apply_preferences: bool = True,
        include_soft_excluded: bool = True,
        include_disallowed_oils: bool = False,
    ) -> List[Dict[str, Any]]:
        self.ensure_schema()

        with get_connection() as conn:
            rows = conn.execute(
                """
                SELECT
                    d.id,
                    d.flyer_id,
                    d.store_id,
                    s.name as store_name,
                    d.page_index,
                    d.title,
                    d.description,
                    d.price_text,
                    d.deal_qty,
                    d.deal_total,
                    d.unit_price,
                    d.unit,
                    d.norm_unit_price,
                    d.norm_unit,
                    d.norm_note,
                    d.item_id,
                    d.mapping_confidence,
                    d.confidence,
                    d.created_at
                FROM flyer_deals d
                JOIN stores s ON s.id = d.store_id
                WHERE d.flyer_id = ?
                ORDER BY
                    s.name ASC,
                    (d.norm_unit_price IS NULL) ASC,
                    d.norm_unit_price ASC,
                    d.created_at DESC
                """,
                (int(flyer_id),),
            ).fetchall()

        deals: List[Dict[str, Any]] = []
        for r in rows:
            deals.append(
                {
                    "id": r["id"],
                    "flyer_id": r["flyer_id"],
                    "store_id": r["store_id"],
                    "store_name": r["store_name"],
                    "page_index": r["page_index"],
                    "title": r["title"] or "",
                    "description": r["description"] or "",
                    "price_text": r["price_text"] or "",
                    "deal_qty": r["deal_qty"],
                    "deal_total": r["deal_total"],
                    "unit_price": r["unit_price"],
                    "unit": r["unit"] or "",
                    "norm_unit_price": r["norm_unit_price"],
                    "norm_unit": r["norm_unit"] or "",
                    "norm_note": r["norm_note"] or "",
                    "item_id": r["item_id"],
                    "mapping_confidence": r["mapping_confidence"],
                    "confidence": r["confidence"],
                    "created_at": r["created_at"],
                }
            )

        if not deals:
            return []

        if apply_preferences:
            eff = self._try_get_effective_preferences()
            if eff is not None:
                deals = self._apply_preferences_to_deals(
                    deals,
                    eff,
                    filter_disallowed_oils=not include_disallowed_oils,
                )

        if not include_soft_excluded:
            deals = [
                d
                for d in deals
                if not (isinstance(d.get("pref_soft_excluded_by"), list) and len(d.get("pref_soft_excluded_by") or []) > 0)
            ]

        return deals

    def list_active_deals(
        self,
        *,
        store_id: Optional[int] = None,
        as_of: Optional[str] = None,
        limit: int = 500,
        apply_preferences: bool = True,
        include_soft_excluded: bool = True,
        include_disallowed_oils: bool = False,
    ) -> List[Dict[str, Any]]:
        """
        Return deals from flyer batches that are ACTIVE as of a date.

        Active = flyer_batches.valid_from <= as_of <= flyer_batches.valid_to
        (dates are compared as YYYY-MM-DD strings; SQLite date() is used).

        If apply_preferences=True and preferences_service is available, this will:
        - remove hard-excluded items (allergies + master hard excludes)
        - optionally remove disallowed oils (baseline oils_allowed)
        - annotate soft-excluded deals via `pref_soft_excluded_by` + `pref_oil_allowed`
        """
        if not as_of:
            as_of = datetime.now().date().isoformat()

        sql = """
            SELECT
                d.id,
                d.flyer_id,
                d.store_id,
                s.name as store_name,
                b.valid_from as flyer_valid_from,
                b.valid_to as flyer_valid_to,
                d.page_index,
                d.title,
                d.description,
                d.price_text,
                d.deal_qty,
                d.deal_total,
                d.unit_price,
                d.unit,
                d.norm_unit_price,
                d.norm_unit,
                d.norm_note,
                d.item_id,
                d.mapping_confidence,
                d.confidence,
                d.created_at
            FROM flyer_deals d
            JOIN flyer_batches b ON b.id = d.flyer_id
            JOIN stores s ON s.id = d.store_id
            WHERE
                b.valid_from IS NOT NULL AND b.valid_to IS NOT NULL
                AND TRIM(b.valid_from) <> '' AND TRIM(b.valid_to) <> ''
                AND date(b.valid_from) <= date(?)
                AND date(b.valid_to) >= date(?)
        """
        params: List[Any] = [as_of, as_of]

        if store_id is not None:
            sql += """ AND d.store_id = ? """
            params.append(int(store_id))

        sql += """
            ORDER BY
                s.name ASC,
                (d.norm_unit_price IS NULL) ASC,
                d.norm_unit_price ASC,
                d.created_at DESC
            LIMIT ?
        """
        params.append(int(limit))

        with get_connection() as conn:
            rows = conn.execute(sql, params).fetchall()

        deals: List[Dict[str, Any]] = []
        for r in rows:
            deals.append(
                {
                    "id": r["id"],
                    "flyer_id": r["flyer_id"],
                    "store_id": r["store_id"],
                    "store_name": r["store_name"],
                    "flyer_valid_from": r["flyer_valid_from"],
                    "flyer_valid_to": r["flyer_valid_to"],
                    "page_index": r["page_index"],
                    "title": r["title"] or "",
                    "description": r["description"] or "",
                    "price_text": r["price_text"] or "",
                    "deal_qty": r["deal_qty"],
                    "deal_total": r["deal_total"],
                    "unit_price": r["unit_price"],
                    "unit": r["unit"] or "",
                    "norm_unit_price": r["norm_unit_price"],
                    "norm_unit": r["norm_unit"] or "",
                    "norm_note": r["norm_note"] or "",
                    "item_id": r["item_id"],
                    "mapping_confidence": r["mapping_confidence"],
                    "confidence": r["confidence"],
                    "created_at": r["created_at"],
                }
            )

        if not deals:
            return []

        if apply_preferences:
            eff = self._try_get_effective_preferences()
            if eff is not None:
                deals = self._apply_preferences_to_deals(
                    deals,
                    eff,
                    filter_disallowed_oils=not include_disallowed_oils,
                )

        if not include_soft_excluded:
            deals = [
                d
                for d in deals
                if not (isinstance(d.get("pref_soft_excluded_by"), list) and len(d.get("pref_soft_excluded_by") or []) > 0)
            ]

        return deals

    # -------------------------------------------------------------------------
    # Preferences integration (fail-safe)
    # -------------------------------------------------------------------------

    def _try_get_effective_preferences(self) -> Any:
        """
        Returns an EffectivePreferences-like object if available, else None.

        We avoid hard dependencies so repo still works even if prefs aren't wired yet.
        """
        try:
            from Grocery_Sense.services.preferences_service import compute_effective_preferences

            return compute_effective_preferences()
        except Exception:
            return None

    def _apply_preferences_to_deals(
        self,
        deals: List[Dict[str, Any]],
        eff: Any,
        *,
        filter_disallowed_oils: bool = True,
    ) -> List[Dict[str, Any]]:
        """
        Applies household preference rules to raw flyer deals.

        Rules:
          - allergies always hard exclude
          - master hard excludes override all
          - secondary excludes become soft excludes (we annotate with who + what matched)
          - optionally filter oils based on baseline oils_allowed (or annotate via pref_oil_allowed)
        """
        if not deals:
            return deals

        def normalize_token(s: str) -> str:
            return re.sub(r"[^a-z0-9 ]+", " ", (s or "").lower()).strip()

        def deal_text(d: Dict[str, Any]) -> str:
            return f"{d.get('title','')} {d.get('description','')}"

        # ---- pull effective prefs in a robust way ----
        hard_set = set(normalize_token(x) for x in (getattr(eff, "hard_excludes", None) or []))

        # Soft excludes can come as:
        # 1) eff.soft_excludes: ingredient -> [members]
        # 2) eff.soft_excludes_by_member: member -> [ingredients]
        soft_ingredient_to_members: Dict[str, List[str]] = {}
        soft_by_member = getattr(eff, "soft_excludes_by_member", None)
        if isinstance(soft_by_member, dict):
            # invert member->items into item->members
            for member_name, items in soft_by_member.items():
                for item in (items or []):
                    it = normalize_token(str(item))
                    if not it:
                        continue
                    soft_ingredient_to_members.setdefault(it, [])
                    if str(member_name) not in soft_ingredient_to_members[it]:
                        soft_ingredient_to_members[it].append(str(member_name))
        else:
            soft_map = getattr(eff, "soft_excludes", None)
            if isinstance(soft_map, dict):
                for ingredient, members in soft_map.items():
                    ing = normalize_token(str(ingredient))
                    if not ing:
                        continue
                    mlst: List[str] = []
                    if isinstance(members, list):
                        mlst = [str(x) for x in members if str(x).strip()]
                    soft_ingredient_to_members[ing] = mlst

        oils_allowed_raw = getattr(eff, "oils_allowed", None) or set()
        oils_allowed_norm = set(normalize_token(str(x)) for x in oils_allowed_raw if str(x).strip())

        # Determine oils list (fallback to preferences_service.OILS if present)
        oils_list: List[str] = []
        try:
            from Grocery_Sense.services import preferences_service

            oils_list = [str(x).strip().lower() for x in getattr(preferences_service, "OILS", []) or []]
        except Exception:
            oils_list = ["olive oil", "avocado oil", "vegetable oil", "canola oil", "coconut oil"]

        oils_list_norm = [normalize_token(o) for o in oils_list if normalize_token(o)]

        def find_hard_hits(text: str) -> set[str]:
            t = normalize_token(text)
            hits: set[str] = set()
            for c in hard_set:
                if not c:
                    continue
                if f" {c} " in f" {t} ":
                    hits.add(c)
            return hits

        def find_soft_hits_and_excluders(text: str) -> tuple[List[str], List[str]]:
            """
            Returns:
              (soft_excluders, soft_hit_ingredients)
            where:
              soft_excluders = unique member names
              soft_hit_ingredients = unique ingredients (tokens) that matched
            """
            t = normalize_token(text)
            excluders: List[str] = []
            hit_ings: List[str] = []

            for ing, members in soft_ingredient_to_members.items():
                if not ing:
                    continue
                if f" {ing} " in f" {t} ":
                    if ing not in hit_ings:
                        hit_ings.append(ing)
                    for m in (members or []):
                        if m and m not in excluders:
                            excluders.append(m)

            return excluders, hit_ings

        def oil_hit(text: str) -> Optional[str]:
            t = normalize_token(text)
            for o in oils_list_norm:
                if o and f" {o} " in f" {t} ":
                    return o
            return None

        filtered: List[Dict[str, Any]] = []

        for d in deals:
            txt = deal_text(d)

            # 1) Hard excludes => remove
            hard_hits = find_hard_hits(txt)
            if hard_hits:
                d["pref_hard_excluded"] = True
                d["pref_hard_excluded_hits"] = sorted(hard_hits)
                continue
            d["pref_hard_excluded"] = False
            d["pref_hard_excluded_hits"] = []

            # 2) Soft excludes => annotate who + ingredient hit(s)
            excluders, hit_ings = find_soft_hits_and_excluders(txt)
            d["pref_soft_excluded_by"] = excluders
            d["pref_soft_excluded_hits"] = hit_ings  # <-- this is what the tooltip uses

            # 3) Oils => filter or annotate
            oh = oil_hit(txt)
            if oh:
                if oils_allowed_norm:
                    allowed = oh in oils_allowed_norm
                    d["pref_oil_allowed"] = bool(allowed)
                    d["pref_oil_hit"] = oh
                    if filter_disallowed_oils and not allowed:
                        continue
                else:
                    # oils unrestricted if oils_allowed is empty
                    d["pref_oil_allowed"] = True
                    d["pref_oil_hit"] = oh
            else:
                d["pref_oil_allowed"] = True
                d["pref_oil_hit"] = ""

            filtered.append(d)

        return filtered

