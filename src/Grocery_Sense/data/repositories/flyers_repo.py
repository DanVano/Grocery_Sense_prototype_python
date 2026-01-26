from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from Grocery_Sense.data.connection import get_connection

# Preferences integration is optional at runtime. If preferences_service (or
# config_store) is not available, this repo will return raw deals unchanged.
try:
    from Grocery_Sense import config_store
    from Grocery_Sense.services import preferences_service
except Exception:  # pragma: no cover
    config_store = None  # type: ignore
    preferences_service = None  # type: ignore


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def compute_sha256(file_path: str | Path, chunk_size: int = 1024 * 1024) -> str:
    p = Path(file_path)
    h = hashlib.sha256()
    with p.open("rb") as f:
        while True:
            b = f.read(chunk_size)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


@dataclass(frozen=True)
class StoreRow:
    id: int
    name: str


class FlyersRepo:
    """
    DB layer for Flyers / Flyer Assets / Raw JSON / Deals.

    Stores:
      - flyer_batches: one import session (store + date range + source)
      - flyer_assets: each PDF/image file attached to a batch
      - flyer_raw_json: Azure Layout raw result per asset
      - flyer_deals: extracted DealRecords per batch

    NOTE: We keep this minimal & robust so you can swap the extractor later.
    """

    def ensure_schema(self) -> None:
        with get_connection() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS flyer_batches (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    store_id INTEGER,
                    valid_from TEXT,
                    valid_to TEXT,
                    source_type TEXT NOT NULL,     -- manual_upload | retailer_web | aggregator_partner
                    source_ref TEXT,              -- folder path, URL, etc
                    note TEXT,
                    created_at TEXT NOT NULL DEFAULT (datetime('now'))
                );
                """
            )

            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS flyer_assets (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    flyer_id INTEGER NOT NULL,
                    asset_path TEXT NOT NULL,
                    asset_type TEXT NOT NULL,      -- pdf | image
                    page_index INTEGER,
                    sha256 TEXT,
                    created_at TEXT NOT NULL DEFAULT (datetime('now')),
                    FOREIGN KEY (flyer_id) REFERENCES flyer_batches(id) ON DELETE CASCADE
                );
                """
            )

            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS flyer_raw_json (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    flyer_id INTEGER NOT NULL,
                    asset_id INTEGER NOT NULL,
                    operation_id TEXT,
                    model_id TEXT NOT NULL DEFAULT 'prebuilt-layout',
                    json_path TEXT,
                    raw_json TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT (datetime('now')),
                    FOREIGN KEY (flyer_id) REFERENCES flyer_batches(id) ON DELETE CASCADE,
                    FOREIGN KEY (asset_id) REFERENCES flyer_assets(id) ON DELETE CASCADE
                );
                """
            )

            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS flyer_deals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    flyer_id INTEGER NOT NULL,
                    store_id INTEGER,
                    asset_id INTEGER,
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

                    item_id INTEGER,
                    mapping_confidence REAL,

                    confidence REAL,
                    created_at TEXT NOT NULL DEFAULT (datetime('now')),

                    FOREIGN KEY (flyer_id) REFERENCES flyer_batches(id) ON DELETE CASCADE,
                    FOREIGN KEY (asset_id) REFERENCES flyer_assets(id) ON DELETE SET NULL
                );
                """
            )

            conn.commit()

    # -------------------------
    # Stores helper (UI)
    # -------------------------

    def list_stores(self) -> List[StoreRow]:
        """
        Minimal store listing without depending on stores_repo signatures.
        """
        with get_connection() as conn:
            rows = conn.execute(
                """
                SELECT id, name
                FROM stores
                ORDER BY COALESCE(priority, 9999) ASC, COALESCE(is_favorite, 0) DESC, name ASC;
                """
            ).fetchall()

        return [StoreRow(id=int(r[0]), name=str(r[1] or "")) for r in rows]

    # -------------------------
    # Flyer batch
    # -------------------------

    def create_flyer_batch(
        self,
        *,
        store_id: Optional[int],
        valid_from: Optional[str],
        valid_to: Optional[str],
        source_type: str,
        source_ref: Optional[str],
        note: Optional[str] = None,
    ) -> int:
        self.ensure_schema()
        with get_connection() as conn:
            cur = conn.execute(
                """
                INSERT INTO flyer_batches (store_id, valid_from, valid_to, source_type, source_ref, note, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    int(store_id) if store_id is not None else None,
                    valid_from,
                    valid_to,
                    source_type,
                    source_ref,
                    note,
                    _now_utc_iso(),
                ),
            )
            fid = int(cur.lastrowid)
            conn.commit()
            return fid

    def add_asset(
        self,
        *,
        flyer_id: int,
        asset_path: str,
        asset_type: str,
        page_index: Optional[int] = None,
        sha256: Optional[str] = None,
    ) -> int:
        self.ensure_schema()
        with get_connection() as conn:
            cur = conn.execute(
                """
                INSERT INTO flyer_assets (flyer_id, asset_path, asset_type, page_index, sha256, created_at)
                VALUES (?, ?, ?, ?, ?, ?);
                """,
                (int(flyer_id), asset_path, asset_type, page_index, sha256, _now_utc_iso()),
            )
            aid = int(cur.lastrowid)
            conn.commit()
            return aid

    def add_raw_json(
        self,
        *,
        flyer_id: int,
        asset_id: int,
        operation_id: str,
        json_path: Optional[str],
        raw_json_dict: Dict[str, Any],
        model_id: str = "prebuilt-layout",
    ) -> int:
        self.ensure_schema()
        raw_text = json.dumps(raw_json_dict, ensure_ascii=False)
        with get_connection() as conn:
            cur = conn.execute(
                """
                INSERT INTO flyer_raw_json (flyer_id, asset_id, operation_id, model_id, json_path, raw_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?);
                """,
                (int(flyer_id), int(asset_id), operation_id, model_id, json_path, raw_text, _now_utc_iso()),
            )
            rid = int(cur.lastrowid)
            conn.commit()
            return rid

    def add_deal(
        self,
        *,
        flyer_id: int,
        store_id: Optional[int],
        asset_id: Optional[int],
        page_index: Optional[int],

        title: str,
        description: str,
        price_text: Optional[str],

        deal_qty: Optional[float],
        deal_total: Optional[float],
        unit_price: Optional[float],
        unit: Optional[str],

        norm_unit_price: Optional[float],
        norm_unit: Optional[str],
        norm_note: Optional[str],

        item_id: Optional[int],
        mapping_confidence: Optional[float],
        confidence: Optional[float],
    ) -> int:
        self.ensure_schema()
        with get_connection() as conn:
            cur = conn.execute(
                """
                INSERT INTO flyer_deals (
                    flyer_id, store_id, asset_id, page_index,
                    title, description, price_text,
                    deal_qty, deal_total, unit_price, unit,
                    norm_unit_price, norm_unit, norm_note,
                    item_id, mapping_confidence, confidence,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    int(flyer_id),
                    int(store_id) if store_id is not None else None,
                    int(asset_id) if asset_id is not None else None,
                    int(page_index) if page_index is not None else None,

                    title,
                    description,
                    price_text,

                    float(deal_qty) if deal_qty is not None else None,
                    float(deal_total) if deal_total is not None else None,
                    float(unit_price) if unit_price is not None else None,
                    unit,

                    float(norm_unit_price) if norm_unit_price is not None else None,
                    norm_unit,
                    norm_note,

                    int(item_id) if item_id is not None else None,
                    float(mapping_confidence) if mapping_confidence is not None else None,
                    float(confidence) if confidence is not None else None,

                    _now_utc_iso(),
                ),
            )
            did = int(cur.lastrowid)
            conn.commit()
            return did

    def list_deals_for_flyer(
        self,
        flyer_id: int,
        limit: int = 500,
        *,
        apply_preferences: bool = True,
        annotate_soft_excludes: bool = True,
        filter_disallowed_oils: bool = True,
    ) -> List[Dict[str, Any]]:
        self.ensure_schema()
        with get_connection() as conn:
            rows = conn.execute(
                """
                SELECT
                    d.id, d.flyer_id, d.store_id,
                    COALESCE(s.name, '') as store_name,
                    d.page_index,
                    d.title, d.description, d.price_text,
                    d.deal_qty, d.deal_total, d.unit_price, d.unit,
                    d.norm_unit_price, d.norm_unit, d.norm_note,
                    d.item_id, COALESCE(i.canonical_name, '') as item_name,
                    d.mapping_confidence, d.confidence, d.created_at
                FROM flyer_deals d
                LEFT JOIN stores s ON s.id = d.store_id
                LEFT JOIN items i ON i.id = d.item_id
                WHERE d.flyer_id = ?
                ORDER BY d.id DESC
                LIMIT ?;
                """,
                (int(flyer_id), int(limit)),
            ).fetchall()

        out: List[Dict[str, Any]] = []
        for r in rows:
            out.append(
                {
                    "id": int(r[0]),
                    "flyer_id": int(r[1]),
                    "store_id": r[2],
                    "store_name": r[3],
                    "page_index": r[4],
                    "title": r[5],
                    "description": r[6],
                    "price_text": r[7],
                    "deal_qty": r[8],
                    "deal_total": r[9],
                    "unit_price": r[10],
                    "unit": r[11],
                    "norm_unit_price": r[12],
                    "norm_unit": r[13],
                    "norm_note": r[14],
                    "item_id": r[15],
                    "item_name": r[16],
                    "mapping_confidence": r[17],
                    "confidence": r[18],
                    "created_at": r[19],
                }
            )

        if apply_preferences:
            out = self._apply_preferences_to_deals(
                out,
                annotate_soft_excludes=annotate_soft_excludes,
                filter_disallowed_oils=filter_disallowed_oils,
            )

        return out

    # ---------------------------------------------------------------------
    # Preferences -> deal filtering
    # ---------------------------------------------------------------------

    def _try_get_effective_preferences(self) -> Optional[Tuple[Any, str, int]]:
        """Returns (eff, master_name, household_member_count) or None."""
        if config_store is None or preferences_service is None:
            return None
        try:
            eff = preferences_service.compute_effective_preferences()
            master = config_store.get_master_member()
            master_name = getattr(master, "name", "Master") or "Master"
            cfg = config_store.load_config()
            members = getattr(getattr(cfg, "household", None), "members", []) or []
            count = len(members) if isinstance(members, list) else 1
            if count <= 0:
                count = 1
            return eff, master_name, count
        except Exception:
            return None

    def _apply_preferences_to_deals(
        self,
        deals: List[Dict[str, Any]],
        *,
        annotate_soft_excludes: bool = True,
        filter_disallowed_oils: bool = True,
    ) -> List[Dict[str, Any]]:
        """
        Apply household preferences to flyer deals:

        - Remove deals that match household hard excludes (allergies + master hard excludes)
        - Remove deals that match baseline (master) excluded proteins
        - Optionally remove oil deals that are not allowed by household baseline oils_allowed
        - De-rank deals that match any secondary soft excludes (and annotate with '*' or '**')

        This function is fail-safe: if preferences aren't available, it returns deals unchanged.
        """
        pref = self._try_get_effective_preferences()
        if not pref:
            return deals

        eff, master_name, member_count = pref

        def _norm(s: Any) -> str:
            return str(s or "").strip().lower()

        def _blob(d: Dict[str, Any]) -> str:
            return " ".join([
                _norm(d.get("item_name")),
                _norm(d.get("title")),
                _norm(d.get("description")),
            ]).strip()

        def _compile_token(token: str) -> re.Pattern:
            t = _norm(token)
            # Use non-word boundaries so multi-word tokens work reliably.
            return re.compile(r"(?<!\\w)" + re.escape(t) + r"(?!\\w)")

        def _dedupe(seq: List[str]) -> List[str]:
            out: List[str] = []
            for x in seq:
                if x not in out:
                    out.append(x)
            return out

        hard_tokens: List[str] = sorted({ _norm(x) for x in getattr(eff, "hard_excludes", set()) if _norm(x) })
        hard_patterns: List[Tuple[str, re.Pattern]] = [(t, _compile_token(t)) for t in hard_tokens]

        hard_proteins: Set[str] = { _norm(x) for x in getattr(eff, "excluded_proteins_hard", set()) if _norm(x) }
        hard_protein_patterns: List[Tuple[str, re.Pattern]] = [(p, _compile_token(p)) for p in sorted(hard_proteins)]

        soft_map: Dict[str, List[str]] = getattr(eff, "soft_excludes", {}) or {}
        soft_tokens: List[str] = [t for t in (_norm(k) for k in soft_map.keys()) if t and t not in hard_tokens]
        soft_patterns: List[Tuple[str, re.Pattern]] = [(t, _compile_token(t)) for t in soft_tokens]

        soft_protein_map: Dict[str, List[str]] = getattr(eff, "excluded_proteins_soft", {}) or {}
        soft_protein_tokens: List[str] = [t for t in (_norm(k) for k in soft_protein_map.keys()) if t and t not in hard_proteins]
        soft_protein_patterns: List[Tuple[str, re.Pattern]] = [(t, _compile_token(t)) for t in soft_protein_tokens]

        # Oil detection: map aliases (e.g., butter/ghee -> butter, ghee) -> canonical.
        oil_alias_to_canon: Dict[str, str] = {}
        oils: List[str] = [ _norm(o) for o in getattr(preferences_service, "OILS", []) if _norm(o) ]
        for canon in oils:
            oil_alias_to_canon[canon] = canon
            if "/" in canon:
                for part in [p.strip() for p in canon.split("/") if p.strip()]:
                    oil_alias_to_canon[_norm(part)] = canon

        oil_alias_patterns: List[Tuple[str, str, re.Pattern]] = []
        for alias, canon in oil_alias_to_canon.items():
            oil_alias_patterns.append((alias, canon, _compile_token(alias)))

        def _detect_oil(text: str) -> Optional[str]:
            for _alias, canon, pat in oil_alias_patterns:
                if pat.search(text):
                    return canon
            return None

        kept: List[Dict[str, Any]] = []
        for d in deals:
            text = _blob(d)
            if not text:
                kept.append(d)
                continue

            # 1) Oil filtering (if this deal appears to be an oil product)
            oil_canon = _detect_oil(text)
            if oil_canon and filter_disallowed_oils:
                try:
                    if not eff.is_oil_allowed(oil_canon):
                        continue
                except Exception:
                    # If preference object doesn't support oils, keep.
                    pass

            # 2) Hard ingredient excludes (allergies + master hard excludes)
            hard_hit = None
            for tok, pat in hard_patterns:
                if pat.search(text):
                    hard_hit = tok
                    break
            if hard_hit:
                continue

            # 3) Hard protein excludes (baseline)
            hard_protein_hit = None
            for tok, pat in hard_protein_patterns:
                if pat.search(text):
                    hard_protein_hit = tok
                    break
            if hard_protein_hit:
                continue

            # 4) Soft excludes (secondary members) -> de-rank + annotate
            secondary_excluders: List[str] = []

            for tok, pat in soft_patterns:
                if pat.search(text):
                    ex = soft_map.get(tok, soft_map.get(tok.title(), [])) or []
                    for n in ex:
                        name = str(n or "").strip()
                        if name and name != master_name:
                            secondary_excluders.append(name)

            for tok, pat in soft_protein_patterns:
                if pat.search(text):
                    ex = soft_protein_map.get(tok, []) or []
                    for n in ex:
                        name = str(n or "").strip()
                        if name and name != master_name:
                            secondary_excluders.append(name)

            secondary_excluders = _dedupe(secondary_excluders)
            d["pref_soft_excluders"] = secondary_excluders
            d["pref_is_oil_deal"] = bool(oil_canon)
            d["pref_oil"] = oil_canon or ""

            rank = 0
            marker = ""
            if secondary_excluders:
                share = float(len(secondary_excluders)) / float(max(member_count, 1))
                strong = (member_count >= 3) and (share >= 0.60)
                d["pref_soft_strength"] = "strong" if strong else "soft"
                rank = 2 if strong else 1
                marker = "**" if strong else "*"

                if annotate_soft_excludes:
                    for field in ("item_name", "title"):
                        val = d.get(field)
                        if isinstance(val, str) and val.strip() and not val.rstrip().endswith(marker):
                            d[field] = val.rstrip() + marker
            else:
                d["pref_soft_strength"] = ""

            d["pref_sort_rank"] = rank
            kept.append(d)

        # Stable sort: keep original order within each rank bucket.
        kept.sort(key=lambda x: int(x.get("pref_sort_rank", 0)))
        return kept
