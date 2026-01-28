from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from datetime import date
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from Grocery_Sense.data.db import get_connection


@dataclass
class FlyerDeal:
    """A deal pulled from a flyer import batch.

    NOTE: Some callers work with dict rows returned by repo methods.
    This dataclass is kept for backward compatibility and optional typed usage.
    """

    id: int
    batch_id: int
    store_id: int
    item_name: str
    category: str
    title: str
    description: str
    price: float
    unit_price: Optional[float]
    unit: str
    image_url: str
    valid_from: str
    valid_to: str
    created_at: str


def _norm(s: Any) -> str:
    return re.sub(r"\s+", " ", str(s or "").strip().lower()).strip()


def _split_words(phrase: str) -> List[str]:
    phrase = _norm(phrase)
    if not phrase:
        return []
    return [w for w in re.split(r"\s+", phrase) if w]


def _compile_phrase_regex(phrase: str) -> Optional[re.Pattern]:
    """
    Phrase-safe matcher:
      - enforces word boundaries
      - allows common separators between words (space, hyphen, slash, punctuation)
    """
    words = _split_words(phrase)
    if not words:
        return None

    # Allow separators between multi-word phrases
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


def _extract_text_window(text: str, span: Tuple[int, int], *, pad: int = 24) -> str:
    """Return a short snippet around the match span (for tooltips/debug)."""
    s, e = span
    lo = max(0, s - pad)
    hi = min(len(text), e + pad)
    return text[lo:hi].strip()


class FlyersRepo:
    def __init__(self) -> None:
        self.ensure_schema()

    def ensure_schema(self) -> None:
        with get_connection() as conn:
            cur = conn.cursor()

            # Import batches (one per flyer import run)
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS flyer_batches (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source TEXT NOT NULL,          -- e.g. 'flipp', 'manual_pdf'
                    store_id INTEGER NOT NULL,
                    flyer_name TEXT,
                    valid_from TEXT,
                    valid_to TEXT,
                    status TEXT DEFAULT 'active',  -- active, archived
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
                """
            )

            # Track assets (pdf/image) if needed
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS flyer_assets (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    batch_id INTEGER NOT NULL,
                    asset_type TEXT NOT NULL,      -- 'pdf', 'image'
                    path TEXT NOT NULL,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY(batch_id) REFERENCES flyer_batches(id) ON DELETE CASCADE
                )
                """
            )

            # Optional: store raw json from import
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS flyer_raw_json (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    batch_id INTEGER NOT NULL,
                    json TEXT NOT NULL,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY(batch_id) REFERENCES flyer_batches(id) ON DELETE CASCADE
                )
                """
            )

            # Deals extracted from flyers
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS flyer_deals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    batch_id INTEGER NOT NULL,
                    store_id INTEGER NOT NULL,
                    item_name TEXT,
                    category TEXT,
                    title TEXT,
                    description TEXT,
                    price REAL,
                    unit_price REAL,
                    unit TEXT,
                    image_url TEXT,
                    valid_from TEXT,
                    valid_to TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY(batch_id) REFERENCES flyer_batches(id) ON DELETE CASCADE
                )
                """
            )

            conn.commit()

    # ---------------- batch ops ----------------

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
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO flyer_batches (source, store_id, flyer_name, valid_from, valid_to, status)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (source, store_id, flyer_name, valid_from, valid_to, status),
            )
            conn.commit()
            return int(cur.lastrowid)

    def set_batch_status(self, batch_id: int, status: str) -> None:
        with get_connection() as conn:
            conn.execute(
                "UPDATE flyer_batches SET status = ? WHERE id = ?",
                (status, batch_id),
            )
            conn.commit()

    def list_batches(self, *, store_id: Optional[int] = None, limit: int = 200) -> List[Dict[str, Any]]:
        sql = "SELECT * FROM flyer_batches"
        args: List[Any] = []
        if store_id is not None:
            sql += " WHERE store_id = ?"
            args.append(store_id)
        sql += " ORDER BY created_at DESC LIMIT ?"
        args.append(limit)

        with get_connection() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(sql, args).fetchall()
            return [dict(r) for r in rows]

    # ---------------- deal ops ----------------

    def insert_deals(self, batch_id: int, store_id: int, deals: List[Dict[str, Any]]) -> int:
        """
        Insert normalized deals (from import/parse step). Each deal dict can include:
          item_name, category, title, description, price, unit_price, unit, image_url, valid_from, valid_to
        """
        if not deals:
            return 0

        with get_connection() as conn:
            cur = conn.cursor()
            count = 0
            for d in deals:
                cur.execute(
                    """
                    INSERT INTO flyer_deals (
                        batch_id, store_id, item_name, category, title, description,
                        price, unit_price, unit, image_url, valid_from, valid_to
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        batch_id,
                        store_id,
                        d.get("item_name", ""),
                        d.get("category", ""),
                        d.get("title", ""),
                        d.get("description", ""),
                        d.get("price", None),
                        d.get("unit_price", None),
                        d.get("unit", ""),
                        d.get("image_url", ""),
                        d.get("valid_from", ""),
                        d.get("valid_to", ""),
                    ),
                )
                count += 1

            conn.commit()
            return count

    def list_deals_for_batch(self, batch_id: int) -> List[Dict[str, Any]]:
        with get_connection() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT * FROM flyer_deals
                WHERE batch_id = ?
                ORDER BY price ASC
                """,
                (batch_id,),
            ).fetchall()
            return [dict(r) for r in rows]

    # ---------------- Milestone 1: preference-aware deal feed ----------------

    def list_active_deals(
        self,
        *,
        on_date: Optional[str] = None,
        store_ids: Optional[List[int]] = None,
        limit: int = 5000,
        preferences_aware: bool = True,
        filter_disallowed_oils: bool = False,
    ) -> List[Dict[str, Any]]:
        """
        Return ONLY deals that belong to an ACTIVE batch AND are valid for `on_date`.

        - Hard excluded items are removed (if preferences_aware + prefs available)
        - Soft excluded items are kept but de-ranked + annotated with pref_* fields
        - Disallowed oils can be filtered (filter_disallowed_oils=True) or annotated
        """
        day = (on_date or date.today().isoformat()).strip()

        sql = """
            SELECT
                d.*,
                b.valid_from AS batch_valid_from,
                b.valid_to   AS batch_valid_to,
                b.flyer_name AS batch_flyer_name
            FROM flyer_deals d
            JOIN flyer_batches b ON b.id = d.batch_id
            WHERE b.status = 'active'
              AND (
                    (b.valid_from IS NULL OR b.valid_from = '' OR date(?) >= date(b.valid_from))
                AND (b.valid_to   IS NULL OR b.valid_to   = '' OR date(?) <= date(b.valid_to))
              )
        """
        args: List[Any] = [day, day]

        if store_ids:
            placeholders = ",".join("?" for _ in store_ids)
            sql += f" AND d.store_id IN ({placeholders})"
            args.extend(store_ids)

        sql += " ORDER BY d.price ASC LIMIT ?"
        args.append(limit)

        with get_connection() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(sql, args).fetchall()
            deals = [dict(r) for r in rows]

        if not preferences_aware:
            return deals

        # Preferences are optional/fail-safe: if not available, return raw deals.
        try:
            from Grocery_Sense.services import preferences_service  # local import to avoid cycles

            eff = preferences_service.compute_effective_preferences()
            return self._apply_preferences_to_deals(
                deals,
                eff,
                filter_disallowed_oils=filter_disallowed_oils,
                oils_catalog=list(getattr(preferences_service, "OILS", [])),
            )
        except Exception:
            return deals

    def _apply_preferences_to_deals(
        self,
        deals: List[Dict[str, Any]],
        eff: Any,
        *,
        filter_disallowed_oils: bool,
        oils_catalog: List[str],
    ) -> List[Dict[str, Any]]:
        """
        Adds fields used by the UI:
          pref_soft_excluded: bool
          pref_soft_excluders_all: [names]
          pref_soft_excluders_secondary: [names] (best-effort)
          pref_soft_strength_count / label
          pref_hit: the ingredient phrase that matched
          pref_hit_text: a snippet showing where it matched
          pref_oil_allowed: bool|None
          pref_oil_hit: oil phrase hit (if any)

        Returns filtered + sorted list.
        """

        # Pull rules from effective preferences with defensive defaults.
        hard_excludes: List[str] = sorted(set(getattr(eff, "hard_excludes", []) or []), key=len, reverse=True)

        soft_map: Dict[str, List[str]] = getattr(eff, "soft_excludes", {}) or {}
        soft_phrases: List[str] = sorted(set(soft_map.keys()), key=len, reverse=True)

        # Protected phrases: if a match happens inside these, ignore it unless the phrase itself matches.
        # This is where you add "ingredients that won't be touched".
        protected_phrases = sorted(
            {
                *_norm(o) for o in (oils_catalog or [])
                if _norm(o)
            }
            | {
                # common oil phrases that should not trip generic ingredient matches:
                "olive oil",
                "extra virgin olive oil",
                "avocado oil",
                "canola oil",
                "vegetable oil",
                "sunflower oil",
                "grapeseed oil",
            }
        )

        # For stronger soft-exclude labeling (e.g., 3/5 members): best-effort.
        total_members = None
        try:
            from Grocery_Sense import config_store

            cfg = config_store.load_config()
            total_members = len(cfg.household.members or [])
        except Exception:
            total_members = None

        out: List[Dict[str, Any]] = []
        for d in deals:
            item_name = _norm(d.get("item_name", ""))
            title = _norm(d.get("title", ""))
            desc = _norm(d.get("description", ""))

            # We search in a combined text blob (but we keep title+item_name prominent).
            text = " ".join(x for x in [item_name, title, desc] if x).strip()

            # Build spans for protected phrases in this deal text.
            prot_spans = _find_spans(text, protected_phrases)

            # 1) HARD EXCLUDES => REMOVE
            hard_hit = _best_phrase_hit(text, hard_excludes, prot_spans)
            if hard_hit:
                # Hard excluded: drop entirely
                continue

            # 2) OILS FILTER / ANNOTATE
            oil_hit = None
            oil_allowed = None
            if oils_catalog:
                # Only consider oils if the deal is plausibly an oil item (contains 'oil') OR hits a known oil phrase.
                maybe_oil_text = f"{item_name} {title}".strip()
                maybe_oil_spans = _find_spans(maybe_oil_text, oils_catalog)
                if maybe_oil_spans:
                    # pick the longest oil phrase hit
                    maybe_oil_spans.sort(key=lambda t: -(t[1] - t[0]))
                    oil_hit = maybe_oil_spans[0][2]  # already normalized
                    try:
                        oil_allowed = bool(getattr(eff, "is_oil_allowed")(oil_hit))
                    except Exception:
                        # fallback: if oils_allowed is missing, treat as allowed
                        oil_allowed = True

            if oil_hit and oil_allowed is False:
                if filter_disallowed_oils:
                    continue
                d["pref_oil_allowed"] = False
                d["pref_oil_hit"] = oil_hit
            else:
                d["pref_oil_allowed"] = None
                d["pref_oil_hit"] = None

            # 3) SOFT EXCLUDES => KEEP, DE-RANK + ANNOTATE
            soft_hit = _best_phrase_hit(text, soft_phrases, prot_spans)
            if soft_hit:
                ph, span = soft_hit
                excluders_all = soft_map.get(ph, []) or []

                # Best-effort to separate secondary vs master names.
                excluders_secondary = excluders_all
                try:
                    from Grocery_Sense import config_store

                    master = next((m for m in config_store.list_members() if m.role == config_store.ROLE_MASTER), None)
                    if master:
                        excluders_secondary = [n for n in excluders_all if _norm(n) != _norm(master.name)]
                except Exception:
                    pass

                d["pref_soft_excluded"] = True
                d["pref_soft_excluders_all"] = excluders_all
                d["pref_soft_excluders_secondary"] = excluders_secondary
                d["pref_hit"] = ph
                d["pref_hit_text"] = _extract_text_window(text, span)

                cnt = len(set(_norm(x) for x in excluders_all if _norm(x)))
                d["pref_soft_strength_count"] = cnt

                label = "Soft excluded"
                if total_members and total_members > 0:
                    # Example rule: 3/5 -> strong soft exclude
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

            # Ranking key for UI sorting:
            #   - non-soft first
            #   - then soft excluded (de-ranked)
            #   - then price
            pref_rank = 0
            if d.get("pref_soft_excluded"):
                pref_rank += 10
            if d.get("pref_oil_allowed") is False:
                pref_rank += 6

            d["_pref_rank"] = pref_rank
            out.append(d)

        out.sort(key=lambda r: (int(r.get("_pref_rank", 0)), float(r.get("price") or 999999)))
        for r in out:
            r.pop("_pref_rank", None)
        return out
