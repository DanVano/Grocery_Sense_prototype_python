from __future__ import annotations

import functools
import hashlib
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from Grocery_Sense.data.connection import get_connection

# -----------------------------------------------------------------------------
# Helpers: hashing (used by ingest), phrase-safe matching (used by preferences)
# -----------------------------------------------------------------------------

def compute_sha256(data: bytes) -> str:
    h = hashlib.sha256()
    h.update(data)
    return h.hexdigest()


def _norm(s: Any) -> str:
    return re.sub(r"\s+", " ", str(s or "").strip().lower()).strip()


def _split_words(phrase: str) -> List[str]:
    phrase = _norm(phrase)
    if not phrase:
        return []
    return [w for w in re.split(r"\s+", phrase) if w]


@functools.lru_cache(maxsize=2048)
def _compile_phrase_regex(phrase: str) -> Optional[re.Pattern]:
    """
    Phrase-safe matcher:
      - enforces word boundaries
      - allows common separators between words (space, hyphen, slash, punctuation)
    """
    words = _split_words(phrase)
    if not words:
        return None

    sep = r"(?:\s|[-/.,()])+"  # cheap + effective
    if len(words) == 1:
        pat = rf"\b{re.escape(words[0])}\b"
    else:
        pat = r"\b" + sep.join(re.escape(w) for w in words) + r"\b"
    return re.compile(pat, flags=re.IGNORECASE)


def _find_spans(text: str, phrases: Iterable[str]) -> List[Tuple[int, int, str]]:
    """Return spans (start, end, phrase) for protected phrases found in text."""
    spans: List[Tuple[int, int, str]] = []
    for ph in phrases:
        rx = _compile_phrase_regex(ph)
        if not rx:
            continue
        for m in rx.finditer(text):
            spans.append((m.start(), m.end(), _norm(ph)))
    spans.sort(key=lambda t: (t[0], -(t[1] - t[0])))
    return spans


def _span_overlaps(a: Tuple[int, int], b: Tuple[int, int]) -> bool:
    return not (a[1] <= b[0] or b[1] <= a[0])


def _is_within_protected(
    match_span: Tuple[int, int],
    protected_spans: Sequence[Tuple[int, int, str]],
    *,
    allow_if_phrase_equals: Optional[str] = None,
) -> bool:
    """True if match_span overlaps any protected span (unless it's the same phrase)."""
    allow = _norm(allow_if_phrase_equals or "")
    for s, e, ph in protected_spans:
        if allow and ph == allow:
            continue
        if _span_overlaps(match_span, (s, e)):
            return True
    return False


def _best_phrase_hit(
    text: str,
    phrases: Sequence[str],
    protected_spans: Sequence[Tuple[int, int, str]],
) -> Optional[Tuple[str, Tuple[int, int]]]:
    """
    Return (phrase, (start,end)) for the best match in `text`, skipping matches inside protected spans.
    "Best" = first match among phrases sorted by length desc (caller should sort).
    """
    for ph in phrases:
        rx = _compile_phrase_regex(ph)
        if not rx:
            continue
        for m in rx.finditer(text):
            span = (m.start(), m.end())
            if _is_within_protected(span, protected_spans, allow_if_phrase_equals=ph):
                continue
            return (_norm(ph), span)
    return None


def _extract_text_window(text: str, span: Tuple[int, int], *, pad: int = 28) -> str:
    """Return a short snippet around the match span (for tooltips/debug)."""
    s, e = span
    lo = max(0, s - pad)
    hi = min(len(text), e + pad)
    return text[lo:hi].strip()


# -----------------------------------------------------------------------------
# Data models
# -----------------------------------------------------------------------------

@dataclass
class StoreRow:
    id: int
    name: str


# -----------------------------------------------------------------------------
# Repo
# -----------------------------------------------------------------------------

class FlyersRepo:
    """
    Stores flyer imports and deals in SQLite.

    This file is the "high" version that combines:
      - the newer preference-aware deal-feed logic (phrase-safe matching + protected phrases)
      - the richer flyer_deals schema used by ingest/mapping
      - active-deals-by-valid-date filtering
    """

    # Common phrases that should NOT be treated as ingredient hits for generic excludes.
    # This is the "don't touch list" you mentioned (ex: avoid 'olives' triggering 'olive oil').
    DONT_TOUCH_PHRASES: Sequence[str] = (
        "olive oil",
        "extra virgin olive oil",
        "avocado oil",
        "canola oil",
        "vegetable oil",
        "sunflower oil",
        "grapeseed oil",
    )

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
                    source_type TEXT,
                    source_ref TEXT,
                    note TEXT,
                    status TEXT DEFAULT 'active',
                    imported_at TEXT NOT NULL
                )
                """
            )

            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS flyer_assets (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    flyer_id INTEGER NOT NULL,
                    asset_type TEXT NOT NULL,      -- 'pdf', 'image'
                    path TEXT NOT NULL,
                    sha256 TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(flyer_id) REFERENCES flyer_batches(id) ON DELETE CASCADE
                )
                """
            )

            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS flyer_raw_json (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    flyer_id INTEGER NOT NULL,
                    sha256 TEXT,
                    json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(flyer_id) REFERENCES flyer_batches(id) ON DELETE CASCADE
                )
                """
            )

            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS flyer_deals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    flyer_id INTEGER NOT NULL,
                    asset_id INTEGER,
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
                    FOREIGN KEY(flyer_id) REFERENCES flyer_batches(id) ON DELETE CASCADE,
                    FOREIGN KEY(asset_id) REFERENCES flyer_assets(id) ON DELETE SET NULL
                )
                """
            )

            conn.execute("CREATE INDEX IF NOT EXISTS idx_flyer_deals_flyer_id ON flyer_deals(flyer_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_flyer_deals_store_id ON flyer_deals(store_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_flyer_batches_store_id ON flyer_batches(store_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_flyer_batches_status ON flyer_batches(status)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_flyer_batches_valid ON flyer_batches(valid_from, valid_to)")

            conn.commit()

    # -------------------------------------------------------------------------
    # Stores (optional convenience; stores_repo may also manage this table)
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
            return int(row[0] if isinstance(row, tuple) else row["id"])

    def list_stores(self) -> List[StoreRow]:
        self.ensure_schema()
        with get_connection() as conn:
            try:
                rows = conn.execute("SELECT id, name FROM stores ORDER BY name ASC").fetchall()
            except Exception:
                return []
        out: List[StoreRow] = []
        for r in rows:
            if isinstance(r, sqlite3.Row):
                out.append(StoreRow(id=int(r["id"]), name=str(r["name"])))
            else:
                out.append(StoreRow(id=int(r[0]), name=str(r[1])))
        return out

    # -------------------------------------------------------------------------
    # Flyer batches / assets / raw json
    # -------------------------------------------------------------------------

    def create_flyer_batch(
        self,
        *,
        store_id: int,
        valid_from: Optional[str],
        valid_to: Optional[str],
        source_type: Optional[str] = None,
        source_ref: Optional[str] = None,
        note: Optional[str] = None,
        status: str = "active",
    ) -> int:
        self.ensure_schema()
        now = datetime.utcnow().isoformat(timespec="seconds")

        with get_connection() as conn:
            conn.execute(
                """
                INSERT INTO flyer_batches (store_id, valid_from, valid_to, source_type, source_ref, note, status, imported_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (int(store_id), valid_from, valid_to, source_type, source_ref, note, status, now),
            )
            row = conn.execute("SELECT last_insert_rowid()").fetchone()
            conn.commit()
            return int(row[0])

    # Back-compat alias (older code called it create_batch)
    def create_batch(
        self,
        *,
        source: str,
        store_id: int,
        flyer_name: str = "",
        valid_from: str = "",
        valid_to: str = "",
        status: str = "active",
    ) -> int:
        note = flyer_name or ""
        return self.create_flyer_batch(
            store_id=store_id,
            valid_from=valid_from or None,
            valid_to=valid_to or None,
            source_type=source or None,
            source_ref=None,
            note=note,
            status=status or "active",
        )

    def set_batch_status(self, flyer_id: int, status: str) -> None:
        self.ensure_schema()
        with get_connection() as conn:
            conn.execute("UPDATE flyer_batches SET status=? WHERE id=?", (status, int(flyer_id)))
            conn.commit()

    def add_asset(
        self,
        flyer_id: int,
        *,
        asset_type: str,
        path: str,
        sha256: Optional[str] = None,
    ) -> int:
        self.ensure_schema()
        now = datetime.utcnow().isoformat(timespec="seconds")
        with get_connection() as conn:
            conn.execute(
                """
                INSERT INTO flyer_assets (flyer_id, asset_type, path, sha256, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (int(flyer_id), (asset_type or "").strip(), (path or "").strip(), sha256, now),
            )
            row = conn.execute("SELECT last_insert_rowid()").fetchone()
            conn.commit()
            return int(row[0])

    def add_raw_json(
        self,
        flyer_id: int,
        *,
        raw_json: str,
        sha256: Optional[str] = None,
    ) -> int:
        self.ensure_schema()
        now = datetime.utcnow().isoformat(timespec="seconds")
        with get_connection() as conn:
            conn.execute(
                """
                INSERT INTO flyer_raw_json (flyer_id, sha256, json, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (int(flyer_id), sha256, raw_json, now),
            )
            row = conn.execute("SELECT last_insert_rowid()").fetchone()
            conn.commit()
            return int(row[0])

    # -------------------------------------------------------------------------
    # Deals
    # -------------------------------------------------------------------------

    def add_deal(
        self,
        *,
        flyer_id: int,
        store_id: int,
        asset_id: Optional[int] = None,
        page_index: Optional[int] = None,
        title: Optional[str] = None,
        description: Optional[str] = None,
        price_text: Optional[str] = None,
        deal_qty: Optional[float] = None,
        deal_total: Optional[float] = None,
        unit_price: Optional[float] = None,
        unit: Optional[str] = None,
        norm_unit_price: Optional[float] = None,
        norm_unit: Optional[str] = None,
        norm_note: Optional[str] = None,
        item_id: Optional[str] = None,
        mapping_confidence: Optional[float] = None,
        confidence: Optional[float] = None,
    ) -> int:
        self.ensure_schema()
        now = datetime.utcnow().isoformat(timespec="seconds")

        with get_connection() as conn:
            conn.execute(
                """
                INSERT INTO flyer_deals (
                    flyer_id, asset_id, store_id, page_index, title, description, price_text,
                    deal_qty, deal_total, unit_price, unit,
                    norm_unit_price, norm_unit, norm_note,
                    item_id, mapping_confidence, confidence, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    int(flyer_id),
                    int(asset_id) if asset_id is not None else None,
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
            row = conn.execute("SELECT last_insert_rowid()").fetchone()
            conn.commit()
            return int(row[0])

    # Back-compat alias (older code inserted a simplified "deals" list)
    def insert_deals(self, batch_id: int, store_id: int, deals: List[Dict[str, Any]]) -> int:
        """
        Back-compat: accepts a list of "simple" deals and maps into the richer schema.

        Expected keys (best-effort):
          title, description, price_text, deal_total/price, unit_price, unit, valid_from, valid_to, page_index
        """
        if not deals:
            return 0
        count = 0
        for d in deals:
            price = d.get("deal_total", None)
            if price is None:
                price = d.get("price", None)
            self.add_deal(
                flyer_id=batch_id,
                store_id=store_id,
                page_index=d.get("page_index", None),
                title=d.get("title", None),
                description=d.get("description", None),
                price_text=d.get("price_text", None),
                deal_total=price if isinstance(price, (int, float)) else None,
                unit_price=d.get("unit_price", None),
                unit=d.get("unit", None),
            )
            count += 1
        return count

    def list_deals_for_flyer(
        self,
        flyer_id: int,
        *,
        apply_preferences: bool = True,
        include_soft_excluded: bool = True,
        include_disallowed_oils: bool = False,
        limit: int = 5000,
    ) -> List[Dict[str, Any]]:
        self.ensure_schema()

        # Try query with store name (if stores table exists)
        sql_join = """
            SELECT
                d.*,
                b.valid_from AS valid_from,
                b.valid_to   AS valid_to,
                b.status     AS flyer_status,
                s.name       AS store_name
            FROM flyer_deals d
            JOIN flyer_batches b ON b.id = d.flyer_id
            LEFT JOIN stores s ON s.id = d.store_id
            WHERE d.flyer_id = ?
            ORDER BY
                COALESCE(d.norm_unit_price, d.unit_price, d.deal_total) ASC,
                d.created_at DESC
            LIMIT ?
        """
        sql_nojoin = """
            SELECT
                d.*,
                b.valid_from AS valid_from,
                b.valid_to   AS valid_to,
                b.status     AS flyer_status
            FROM flyer_deals d
            JOIN flyer_batches b ON b.id = d.flyer_id
            WHERE d.flyer_id = ?
            ORDER BY
                COALESCE(d.norm_unit_price, d.unit_price, d.deal_total) ASC,
                d.created_at DESC
            LIMIT ?
        """

        with get_connection() as conn:
            conn.row_factory = sqlite3.Row
            try:
                rows = conn.execute(sql_join, (int(flyer_id), int(limit))).fetchall()
            except Exception:
                rows = conn.execute(sql_nojoin, (int(flyer_id), int(limit))).fetchall()

        deals = [self._row_to_deal_dict(r) for r in rows]

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
            deals = [d for d in deals if not bool(d.get("pref_soft_excluded"))]

        return deals

    def list_active_deals(
        self,
        *,
        store_id: Optional[int] = None,
        store_ids: Optional[List[int]] = None,
        on_date: Optional[str] = None,
        as_of: Optional[str] = None,  # alias
        limit: int = 5000,
        preferences_aware: bool = True,
        apply_preferences: Optional[bool] = None,  # alias
        include_soft_excluded: bool = True,
        filter_disallowed_oils: bool = False,
        include_disallowed_oils: Optional[bool] = None,  # alias
    ) -> List[Dict[str, Any]]:
        """
        Return deals from flyer batches that are ACTIVE as of a date.

        Active = flyer_batches.status='active' AND valid_from <= day <= valid_to
        (dates are compared as YYYY-MM-DD strings; SQLite date() is used).

        If preferences_aware/apply_preferences=True and preferences_service is available:
        - remove hard-excluded items (allergies + master hard excludes)
        - optionally filter disallowed oils (baseline oils_allowed)
        - annotate soft-excluded deals with pref_* fields (hit + who)
        """
        day = (on_date or as_of or datetime.now().date().isoformat()).strip()
        if apply_preferences is None:
            apply_preferences = bool(preferences_aware)
        if include_disallowed_oils is not None:
            # legacy flag name
            filter_disallowed_oils = bool(not include_disallowed_oils)

        sql = """
            SELECT
                d.*,
                b.valid_from AS valid_from,
                b.valid_to   AS valid_to,
                b.status     AS flyer_status,
                s.name       AS store_name
            FROM flyer_deals d
            JOIN flyer_batches b ON b.id = d.flyer_id
            LEFT JOIN stores s ON s.id = d.store_id
            WHERE
                b.status = 'active'
                AND b.valid_from IS NOT NULL AND b.valid_to IS NOT NULL
                AND TRIM(b.valid_from) <> '' AND TRIM(b.valid_to) <> ''
                AND date(b.valid_from) <= date(?)
                AND date(b.valid_to) >= date(?)
        """
        args: List[Any] = [day, day]

        if store_id is not None:
            sql += " AND d.store_id = ?"
            args.append(int(store_id))

        if store_ids:
            placeholders = ",".join("?" for _ in store_ids)
            sql += f" AND d.store_id IN ({placeholders})"
            args.extend(int(x) for x in store_ids)

        sql += """
            ORDER BY
                COALESCE(d.norm_unit_price, d.unit_price, d.deal_total) ASC,
                d.created_at DESC
            LIMIT ?
        """
        args.append(int(limit))

        with get_connection() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(sql, args).fetchall()

        deals = [self._row_to_deal_dict(r) for r in rows]
        if not deals:
            return []

        if apply_preferences:
            eff = self._try_get_effective_preferences()
            if eff is not None:
                deals = self._apply_preferences_to_deals(
                    deals,
                    eff,
                    filter_disallowed_oils=bool(filter_disallowed_oils),
                )

        if not include_soft_excluded:
            deals = [d for d in deals if not bool(d.get("pref_soft_excluded"))]

        return deals

    # -------------------------------------------------------------------------
    # Row mapping
    # -------------------------------------------------------------------------

    def _row_to_deal_dict(self, r: sqlite3.Row) -> Dict[str, Any]:
        """
        Normalize DB row -> dict for UI/services.

        Also provides compatibility keys used by DealFeedWindow:
          item_name, price, valid_from, valid_to
        """
        # Base fields
        d: Dict[str, Any] = dict(r)

        # Ensure common empty defaults
        d["title"] = (d.get("title") or "").strip()
        d["description"] = (d.get("description") or "").strip()
        d["unit"] = (d.get("unit") or "").strip()
        d["norm_unit"] = (d.get("norm_unit") or "").strip()
        d["norm_note"] = (d.get("norm_note") or "").strip()

        # Compatibility fields expected by older UI
        d.setdefault("item_name", "")  # this schema doesn't store item_name separately

        # A display price for UI: prefer deal_total then unit_price then norm_unit_price
        disp_price = d.get("deal_total", None)
        if disp_price is None:
            disp_price = d.get("unit_price", None)
        if disp_price is None:
            disp_price = d.get("norm_unit_price", None)
        d["price"] = disp_price

        # valid_from/to are pulled from batch join aliases (already in dict when selected)
        d["valid_from"] = (d.get("valid_from") or "").strip()
        d["valid_to"] = (d.get("valid_to") or "").strip()

        # store name (optional)
        d["store_name"] = (d.get("store_name") or "").strip()

        return d

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
        filter_disallowed_oils: bool = False,
    ) -> List[Dict[str, Any]]:
        """
        Applies household preference rules to raw flyer deals and annotates fields used by the UI.

        Adds fields:
          pref_soft_excluded: bool
          pref_soft_excluders_all: [names]
          pref_soft_excluders_secondary: [names]
          pref_hit: ingredient phrase matched (best hit)
          pref_hit_text: snippet around the hit
          pref_soft_strength_count: int
          pref_soft_strength_label: str|None
          pref_oil_allowed: bool|None
          pref_oil_hit: str|None

        Filters out:
          - hard excluded deals always
          - disallowed oils if filter_disallowed_oils=True
        """
        if not deals:
            return deals

        # ---- pull effective prefs in a robust way ----
        hard_excludes: List[str] = []
        try:
            hard_excludes = list(getattr(eff, "hard_excludes", []) or [])
        except Exception:
            hard_excludes = []
        hard_excludes = sorted({_norm(x) for x in hard_excludes if _norm(x)}, key=len, reverse=True)

        # Soft excludes can come as:
        # 1) eff.soft_excludes: ingredient -> [members]
        # 2) eff.soft_excludes_by_member: member -> [ingredients]
        soft_ingredient_to_members: Dict[str, List[str]] = {}
        soft_by_member = getattr(eff, "soft_excludes_by_member", None)
        if isinstance(soft_by_member, dict):
            for member_name, items in soft_by_member.items():
                for item in (items or []):
                    it = _norm(item)
                    if not it:
                        continue
                    soft_ingredient_to_members.setdefault(it, [])
                    mn = str(member_name).strip()
                    if mn and mn not in soft_ingredient_to_members[it]:
                        soft_ingredient_to_members[it].append(mn)
        else:
            soft_map = getattr(eff, "soft_excludes", None)
            if isinstance(soft_map, dict):
                for ingredient, members in soft_map.items():
                    ing = _norm(ingredient)
                    if not ing:
                        continue
                    mlst: List[str] = []
                    if isinstance(members, list):
                        mlst = [str(x).strip() for x in members if str(x).strip()]
                    soft_ingredient_to_members[ing] = mlst

        soft_phrases = sorted(set(soft_ingredient_to_members.keys()), key=len, reverse=True)

        # Oils catalog (and don't-touch phrases)
        oils_catalog: List[str] = []
        try:
            from Grocery_Sense.services import preferences_service

            oils_catalog = [str(x) for x in getattr(preferences_service, "OILS", []) or []]
        except Exception:
            oils_catalog = ["olive oil", "avocado oil", "vegetable oil", "canola oil", "coconut oil"]

        protected_phrases = sorted(set(_norm(x) for x in (oils_catalog or []) if _norm(x)) | set(self.DONT_TOUCH_PHRASES))

        # total members (for strong soft exclude label)
        total_members: Optional[int] = None
        try:
            from Grocery_Sense import config_store

            cfg = config_store.load_config()
            total_members = len(getattr(cfg.household, "members", []) or [])
        except Exception:
            total_members = None

        # master name to exclude from "secondary" list
        master_name_norm: Optional[str] = None
        try:
            from Grocery_Sense import config_store

            master = next((m for m in config_store.list_members() if m.role == config_store.ROLE_MASTER), None)
            if master:
                master_name_norm = _norm(master.name)
        except Exception:
            master_name_norm = None

        out: List[Dict[str, Any]] = []
        for d in deals:
            # Build a text blob for matching (use original text where possible)
            title = str(d.get("title") or "")
            desc = str(d.get("description") or "")
            price_text = str(d.get("price_text") or "")
            text = " ".join(x for x in [title, desc, price_text] if x).strip()

            prot_spans = _find_spans(text, protected_phrases)

            # 1) HARD EXCLUDES => DROP
            hard_hit = _best_phrase_hit(text, hard_excludes, prot_spans)
            if hard_hit:
                # annotate for debugging (even though removed)
                ph, _span = hard_hit
                d["pref_hard_excluded"] = True
                d["pref_hard_excluded_hits"] = [ph]
                continue
            d["pref_hard_excluded"] = False
            d["pref_hard_excluded_hits"] = []

            # 2) OILS FILTER / ANNOTATE
            oil_hit = None
            oil_allowed: Optional[bool] = None
            # Only treat as oil if there's a phrase hit from oils catalog or the word "oil" appears near the title.
            maybe_oil_text = f"{title} {desc}".strip()
            if "oil" in maybe_oil_text.lower():
                # find best oil phrase hit
                oil_phrases = sorted({_norm(x) for x in oils_catalog if _norm(x)}, key=len, reverse=True)
                oh = _best_phrase_hit(maybe_oil_text, oil_phrases, protected_spans=[])
                if oh:
                    oil_hit = oh[0]

            if oil_hit:
                # prefer eff.is_oil_allowed if available
                try:
                    fn = getattr(eff, "is_oil_allowed", None)
                    if callable(fn):
                        oil_allowed = bool(fn(oil_hit))
                    else:
                        oils_allowed = {_norm(x) for x in (getattr(eff, "oils_allowed", []) or []) if _norm(x)}
                        oil_allowed = True if not oils_allowed else (oil_hit in oils_allowed)
                except Exception:
                    oil_allowed = True

                d["pref_oil_hit"] = oil_hit
                d["pref_oil_allowed"] = oil_allowed

                if filter_disallowed_oils and oil_allowed is False:
                    continue
            else:
                d["pref_oil_hit"] = None
                d["pref_oil_allowed"] = None

            # 3) SOFT EXCLUDES => KEEP, DE-RANK + ANNOTATE
            soft_hit = _best_phrase_hit(text, soft_phrases, prot_spans)
            if soft_hit:
                ph, span = soft_hit
                excluders_all = list(soft_ingredient_to_members.get(ph, []) or [])

                excluders_secondary = list(excluders_all)
                if master_name_norm:
                    excluders_secondary = [n for n in excluders_all if _norm(n) != master_name_norm]

                d["pref_soft_excluded"] = True
                d["pref_soft_excluders_all"] = excluders_all
                d["pref_soft_excluders_secondary"] = excluders_secondary
                d["pref_hit"] = ph
                d["pref_hit_text"] = _extract_text_window(text, span)

                cnt = len({_norm(x) for x in excluders_all if _norm(x)})
                d["pref_soft_strength_count"] = cnt

                label = "Soft excluded"
                if total_members and total_members > 0:
                    if cnt >= max(3, int(round(0.6 * total_members))):
                        label = "Strong soft exclude"
                d["pref_soft_strength_label"] = label
            else:
                d["pref_soft_excluded"] = False
                d["pref_soft_excluders_all"] = []
                d["pref_soft_excluders_secondary"] = []
                d["pref_hit"] = None
                d["pref_hit_text"] = None
                d["pref_soft_strength_count"] = 0
                d["pref_soft_strength_label"] = None

            # Ranking key:
            #   - non-soft first
            #   - then soft excluded (de-ranked)
            #   - optionally de-rank disallowed oils (if not filtered out)
            pref_rank = 0
            if d.get("pref_soft_excluded"):
                pref_rank += 10
            if d.get("pref_oil_allowed") is False:
                pref_rank += 6

            d["_pref_rank"] = pref_rank
            out.append(d)

        # Sort: by preference rank then by normalized price-ish
        def _price_key(row: Dict[str, Any]) -> float:
            for k in ("norm_unit_price", "unit_price", "deal_total", "price"):
                v = row.get(k)
                if isinstance(v, (int, float)):
                    return float(v)
            return 9e9

        out.sort(key=lambda r: (int(r.get("_pref_rank", 0)), _price_key(r), str(r.get("store_name") or ""), str(r.get("title") or "")))
        for r in out:
            r.pop("_pref_rank", None)

        return out

