from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Set, Tuple

from Grocery_Sense.data.connection import get_db_path as _get_db_path
from Grocery_Sense.data.repositories import stores_repo, prices_repo
from Grocery_Sense.data.repositories.items_repo import get_items_by_ids
from Grocery_Sense.data.repositories.prices_repo import (
    get_active_flyer_prices_batch,
    get_most_recent_prices_by_store_batch,
    get_most_recent_prices_global_batch,
    get_usual_unit_price_batch,
    get_six_month_low_batch,
    get_last_seen_at_or_below_batch,
)


@dataclass(frozen=True)
class AlertKey:
    item_id: int
    store_id: int
    alert_kind: str  # "below_usual" | "stock_up" | "both"


@dataclass
class PriceDropAlert:
    alert_type: str                     # "DROP_BELOW_USUAL" | "STOCK_UP" | "BOTH"
    item_name: str
    store_name: str
    current_unit_price: Optional[float]
    unit: str
    usual_unit_price: Optional[float]
    pct_below_usual: Optional[float]
    low_6mo_global: Optional[float]
    low_6mo_store: Optional[float]
    pct_above_low_6mo: Optional[float]
    is_staple: bool
    staple_purchases_90d: int
    usual_source: str                   # "receipt" | "estimated" | "unknown"
    receipt_samples: int
    valid_to: Optional[str]
    warnings: List[str]
    soft_excluded_by: List[str]
    soft_exclude_hit: Optional[str]

    @staticmethod
    def _from_dict(d: Dict[str, Any]) -> "PriceDropAlert":
        kind = str(d.get("alert_kind") or "")
        alert_type_map = {
            "below_usual": "DROP_BELOW_USUAL",
            "stock_up": "STOCK_UP",
            "both": "BOTH",
        }
        alert_type = alert_type_map.get(kind, kind.upper() if kind else "DROP_BELOW_USUAL")

        basis = str(d.get("basis") or "")
        if "receipt" in basis:
            usual_source = "receipt"
        elif "estimated" in basis:
            usual_source = "estimated"
        else:
            usual_source = "unknown"

        notes = d.get("notes") or ""
        warnings = [notes] if notes else []

        return PriceDropAlert(
            alert_type=alert_type,
            item_name=str(d.get("item_name") or ""),
            store_name=str(d.get("store_name") or ""),
            current_unit_price=float(d["current_price"]) if d.get("current_price") is not None else None,
            unit="",
            usual_unit_price=float(d["usual_price"]) if d.get("usual_price") is not None else None,
            pct_below_usual=float(d["pct_below_usual"]) / 100.0 if d.get("pct_below_usual") is not None else None,
            low_6mo_global=float(d["six_month_low"]) if d.get("six_month_low") is not None else None,
            low_6mo_store=None,
            pct_above_low_6mo=float(d["pct_above_low"]) / 100.0 if d.get("pct_above_low") is not None else None,
            is_staple=bool(d.get("is_staple", 0)),
            staple_purchases_90d=int(d.get("receipt_samples") or 0),
            usual_source=usual_source,
            receipt_samples=int(d.get("receipt_samples") or 0),
            valid_to=None,
            warnings=warnings,
            soft_excluded_by=[],
            soft_exclude_hit=None,
        )


class PriceDropAlertService:
    """
    Milestone 2 engine:
      - Learn "usual price" from receipts (median) with safe fallback when receipt history is sparse.
      - Compute 6-month low from the full price database.
      - Identify staples from receipt frequency.
      - Generate two alert types:
          A) Dropped X% below usual
          B) Stock-up suggestion when near a 6-month low (and last time at/under was >= ~30 days ago)
      - Persist alerts for UI display + dismissal.
    """

    # Tunables (keep conservative; tweak later)
    DROP_BELOW_USUAL_THRESHOLD_PCT = 15.0     # A)
    NEAR_SIX_MONTH_LOW_THRESHOLD_PCT = 5.0    # B)
    ALERT_SUPPRESSION_DAYS = 30               # dismissal suppression window
    STOCK_UP_COOLDOWN_DAYS = 30               # for "once-a-month low" logic

    USUAL_LOOKBACK_DAYS = 180
    LOW_LOOKBACK_DAYS = 183  # ~6 months
    STAPLE_LOOKBACK_DAYS = 90

    MIN_RECEIPT_SAMPLES_FOR_USUAL = 4

    def __init__(self, *, db_path: Optional[str] = None, log=None) -> None:
        self._db_path = db_path or str(_get_db_path())
        self._log = log
        self._ensure_tables()

    # ----------------------- public API -----------------------

    def refresh_engine_alerts(self, *, staples_only: bool = True) -> int:
        """
        Rebuild open "engine" alerts based on current price signals + household history.
        Returns number of alerts inserted.
        """
        alerts = self.compute_engine_alerts(staples_only=staples_only)
        return self._persist_engine_alerts(alerts)

    def compute_engine_alerts(self, *, staples_only: bool = True) -> List[Dict[str, Any]]:
        """
        Compute alerts without writing to DB (useful for embedding in other UIs).
        """
        stores = stores_repo.list_stores()
        if not stores:
            return []

        staple_rows = prices_repo.list_staple_item_ids(
            since_days=self.STAPLE_LOOKBACK_DAYS,
            min_distinct_receipts=3,
            min_line_items=4,
        )
        staple_item_ids: Dict[int, Tuple[int, int]] = {
            item_id: (line_count, receipt_count) for item_id, line_count, receipt_count in staple_rows
        }

        if staples_only and not staple_item_ids:
            return []

        # For now: staple-driven scope (keeps alerts high signal)
        item_ids = list(staple_item_ids.keys())
        store_ids = [s.id for s in stores]
        store_name_map: Dict[int, str] = {s.id: s.name for s in stores}

        # --- Batch-load all price data upfront (6 queries total, 0 per-item) ---
        items_map = get_items_by_ids(item_ids)
        flyer_quotes = get_active_flyer_prices_batch(item_ids, store_ids)
        store_quotes = get_most_recent_prices_by_store_batch(item_ids, store_ids)
        global_quotes = get_most_recent_prices_global_batch(item_ids)
        usual_map = get_usual_unit_price_batch(
            item_ids,
            receipt_only=True,
            min_samples=self.MIN_RECEIPT_SAMPLES_FOR_USUAL,
            since_days=self.USUAL_LOOKBACK_DAYS,
        )
        six_low_map = get_six_month_low_batch(item_ids, since_days=self.LOW_LOOKBACK_DAYS)

        # First pass: resolve best current price per item and identify near-low items
        # so we can batch the last-seen-at-or-below query.
        best_prices: Dict[int, Tuple[float, int, str, str]] = {}  # item_id -> (price, store_id, store_name, source)
        near_low_ceilings: Dict[int, float] = {}  # item_id -> best_unit (for batch query)

        for item_id in item_ids:
            best_store_id: Optional[int] = None
            best_store_name: str = ""
            best_unit: Optional[float] = None
            best_source: str = "unknown"

            for s in stores:
                q = flyer_quotes.get((item_id, s.id))
                if q is None:
                    pr = store_quotes.get((item_id, s.id))
                    if pr and pr.unit_price is not None and pr.unit_price > 0:
                        q = {"unit_price": float(pr.unit_price), "source": pr.source or "latest"}
                if not q:
                    continue
                unit = q.get("unit_price")
                if unit is None or float(unit) <= 0:
                    continue
                if best_unit is None or float(unit) < float(best_unit):
                    best_unit = float(unit)
                    best_store_id = int(s.id)
                    best_store_name = str(s.name)
                    best_source = str(q.get("source") or "latest")

            if best_unit is None:
                global_latest = global_quotes.get(item_id)
                if global_latest and global_latest.unit_price is not None and float(global_latest.unit_price) > 0:
                    best_unit = float(global_latest.unit_price)
                    best_store_id = int(global_latest.store_id or 0)
                    best_store_name = store_name_map.get(best_store_id, "Unknown")
                    best_source = str(global_latest.source or "global_latest")

            if best_unit is None or best_unit <= 0:
                continue

            best_prices[item_id] = (best_unit, best_store_id or 0, best_store_name, best_source)

            six_low, _ = six_low_map.get(item_id, (None, None))
            if six_low is not None and six_low > 0:
                near_threshold = six_low * (1.0 + self.NEAR_SIX_MONTH_LOW_THRESHOLD_PCT / 100.0)
                if best_unit <= near_threshold:
                    near_low_ceilings[item_id] = best_unit

        # Single batch query for all near-low cooldown checks
        last_seen_map = get_last_seen_at_or_below_batch(
            near_low_ceilings, since_days=self.LOW_LOOKBACK_DAYS
        )

        # Second pass: build alerts using fully pre-loaded data
        out: List[Dict[str, Any]] = []

        for item_id in item_ids:
            if item_id not in best_prices:
                continue
            item = items_map.get(item_id)
            if not item:
                continue

            best_unit, best_store_id, best_store_name, best_source = best_prices[item_id]
            usual_price, usual_samples, basis = usual_map.get(item_id, (None, 0, "unknown"))
            six_low, six_low_when = six_low_map.get(item_id, (None, None))

            pct_below_usual: Optional[float] = None
            if usual_price is not None and usual_price > 0:
                pct_below_usual = ((usual_price - best_unit) / usual_price) * 100.0

            pct_above_low: Optional[float] = None
            if six_low is not None and six_low > 0:
                pct_above_low = ((best_unit - six_low) / six_low) * 100.0

            last_seen_at_or_below = last_seen_map.get(item_id)
            stock_up_ok = False
            if item_id in near_low_ceilings:
                stock_up_ok = self._passes_stockup_cooldown(last_seen_at_or_below)

            dropped_ok = (
                pct_below_usual is not None
                and pct_below_usual >= self.DROP_BELOW_USUAL_THRESHOLD_PCT
            )

            if not dropped_ok and not stock_up_ok:
                continue

            if dropped_ok and stock_up_ok:
                alert_kind = "both"
            elif dropped_ok:
                alert_kind = "below_usual"
            else:
                alert_kind = "stock_up"

            line_count, receipt_count = staple_item_ids.get(item_id, (0, 0))
            is_staple = 1 if (receipt_count >= 3 or line_count >= 4) else 0

            notes = self._build_notes(
                item_name=str(item.canonical_name),
                store_name=best_store_name,
                current=best_unit,
                usual=usual_price,
                pct_below=pct_below_usual,
                low=six_low,
                pct_over_low=pct_above_low,
                kind=alert_kind,
                basis=basis,
                samples=usual_samples,
                last_seen=last_seen_at_or_below,
                low_when=six_low_when,
            )

            out.append(
                {
                    "item_id": int(item_id),
                    "item_name": str(item.canonical_name),
                    "store_id": int(best_store_id),
                    "store_name": best_store_name,
                    "current_price": float(best_unit),
                    "usual_price": float(usual_price) if usual_price is not None else None,
                    "pct_below_usual": float(pct_below_usual) if pct_below_usual is not None else None,
                    "six_month_low": float(six_low) if six_low is not None else None,
                    "pct_above_low": float(pct_above_low) if pct_above_low is not None else None,
                    "alert_kind": alert_kind,
                    "is_staple": int(is_staple),
                    "receipt_samples": int(usual_samples),
                    "basis": basis,
                    "source": best_source,
                    "last_seen_at_or_below": last_seen_at_or_below,
                    "notes": notes,
                }
            )

        # Sort: strongest savings first (below-usual), then near-low
        def _sort_key(a: Dict[str, Any]) -> Tuple[float, float]:
            below = a.get("pct_below_usual")
            above_low = a.get("pct_above_low")
            below = float(below) if below is not None else -1.0
            above_low = float(above_low) if above_low is not None else 9999.0
            return (-below, above_low)

        out.sort(key=_sort_key)
        return out

    def get_alerts(self, *, limit: int = 250) -> List[PriceDropAlert]:
        """Return open alerts as PriceDropAlert dataclass objects (UI-friendly)."""
        raw = self.get_open_alerts()
        alerts = [PriceDropAlert._from_dict(d) for d in raw]
        return alerts[:limit] if limit > 0 else alerts

    def get_open_alerts(self) -> List[Dict[str, Any]]:
        self._ensure_tables()
        with sqlite3.connect(self._db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT *
                FROM price_drop_alerts
                WHERE status = 'open'
                ORDER BY created_at DESC
                """,
            ).fetchall()
            return [dict(r) for r in rows]

    def dismiss_alert(self, alert_id: int) -> None:
        self._ensure_tables()
        with sqlite3.connect(self._db_path) as conn:
            conn.execute(
                """
                UPDATE price_drop_alerts
                SET status = 'dismissed', dismissed_at = datetime('now')
                WHERE id = ?
                """,
                (int(alert_id),),
            )
            conn.commit()

    def scan_recent_receipts(self, *, days: int = 21) -> int:
        """
        Optional helper:
          - Looks at receipt price lines (source='receipt') in the last N days
          - Compares to receipt median usual and creates open alerts when the paid price is far below usual.

        Returns number of alerts inserted.
        """
        self._ensure_tables()
        since = (datetime.now() - timedelta(days=int(max(1, days)))).strftime("%Y-%m-%d")

        inserted = 0
        with sqlite3.connect(self._db_path) as conn:
            conn.row_factory = sqlite3.Row

            rows = conn.execute(
                """
                SELECT p.item_id, p.store_id, p.unit_price AS paid_unit, COALESCE(p.date, p.created_at) AS when_iso
                FROM prices p
                WHERE (p.source = 'receipt' OR p.receipt_id IS NOT NULL)
                  AND p.item_id IS NOT NULL
                  AND p.unit_price IS NOT NULL
                  AND date(COALESCE(p.date, p.created_at)) >= date(?)
                ORDER BY when_iso DESC
                """,
                (since,),
            ).fetchall()

            dismissed_keys = self._load_recent_dismissed_keys(conn)

            # Batch-load all price stats upfront — 0 per-row queries
            unique_item_ids = list({int(r["item_id"]) for r in rows if r["item_id"] is not None})
            items_map = get_items_by_ids(unique_item_ids)
            all_stores = stores_repo.list_stores()
            store_name_map: Dict[int, str] = {s.id: s.name for s in all_stores}
            usual_map = get_usual_unit_price_batch(
                unique_item_ids,
                receipt_only=True,
                min_samples=self.MIN_RECEIPT_SAMPLES_FOR_USUAL,
                since_days=self.USUAL_LOOKBACK_DAYS,
            )
            six_low_map = get_six_month_low_batch(unique_item_ids, since_days=self.LOW_LOOKBACK_DAYS)

            for r in rows:
                item_id = int(r["item_id"])
                store_id = int(r["store_id"] or 0)
                paid = float(r["paid_unit"])
                if paid <= 0:
                    continue

                item = items_map.get(item_id)
                if not item:
                    continue

                store_name = store_name_map.get(store_id, "Unknown")

                usual, samples, basis = usual_map.get(item_id, (None, 0, "unknown"))
                if usual is None or usual <= 0:
                    continue

                pct_below = ((usual - paid) / usual) * 100.0
                if pct_below < self.DROP_BELOW_USUAL_THRESHOLD_PCT:
                    continue

                six_low, six_low_when = six_low_map.get(item_id, (None, None))
                pct_above_low = ((paid - six_low) / six_low) * 100.0 if (six_low and six_low > 0) else None

                kind = "below_usual"
                key = AlertKey(item_id=item_id, store_id=store_id, alert_kind=kind)
                if key in dismissed_keys:
                    continue

                notes = self._build_notes(
                    item_name=str(item.canonical_name),
                    store_name=store_name,
                    current=paid,
                    usual=usual,
                    pct_below=pct_below,
                    low=six_low,
                    pct_over_low=pct_above_low,
                    kind=kind,
                    basis=basis,
                    samples=samples,
                    last_seen=None,
                    low_when=six_low_when,
                )

                conn.execute(
                    """
                    INSERT INTO price_drop_alerts
                    (item_id, store_id, store_name, item_name, current_price, usual_price, pct_below_usual,
                     six_month_low, pct_above_low, alert_kind, is_staple, receipt_samples, basis, source,
                     last_seen_at_or_below, notes, created_at, status)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'), 'open')
                    """,
                    (
                        item_id,
                        store_id,
                        store_name,
                        str(item.canonical_name),
                        float(paid),
                        float(usual),
                        float(pct_below),
                        float(six_low) if six_low is not None else None,
                        float(pct_above_low) if pct_above_low is not None else None,
                        kind,
                        0,
                        int(samples),
                        basis,
                        "receipt",
                        None,
                        notes,
                    ),
                )
                inserted += 1

            conn.commit()

        return inserted

    # ----------------------- internals -----------------------

    def _ensure_tables(self) -> None:
        with sqlite3.connect(self._db_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS price_drop_alerts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    item_id INTEGER,
                    store_id INTEGER,
                    store_name TEXT,
                    item_name TEXT,
                    current_price REAL,
                    usual_price REAL,
                    pct_below_usual REAL,
                    six_month_low REAL,
                    pct_above_low REAL,
                    alert_kind TEXT,
                    is_staple INTEGER DEFAULT 0,
                    receipt_samples INTEGER DEFAULT 0,
                    basis TEXT,
                    source TEXT,
                    last_seen_at_or_below TEXT,
                    notes TEXT,
                    created_at TEXT NOT NULL DEFAULT (datetime('now')),
                    status TEXT NOT NULL DEFAULT 'open',
                    dismissed_at TEXT
                )
                """,
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_price_drop_alerts_status ON price_drop_alerts(status)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_price_drop_alerts_created_at ON price_drop_alerts(created_at)")
            conn.commit()

            # Lightweight "migration": add missing columns if older table exists
            cols = {row[1] for row in conn.execute("PRAGMA table_info(price_drop_alerts)").fetchall()}
            add_cols = {
                "item_id": "INTEGER",
                "store_id": "INTEGER",
                "current_price": "REAL",
                "pct_below_usual": "REAL",
                "six_month_low": "REAL",
                "pct_above_low": "REAL",
                "alert_kind": "TEXT",
                "is_staple": "INTEGER DEFAULT 0",
                "receipt_samples": "INTEGER DEFAULT 0",
                "basis": "TEXT",
                "source": "TEXT",
                "last_seen_at_or_below": "TEXT",
                "notes": "TEXT",
            }
            for name, ctype in add_cols.items():
                if name not in cols:
                    try:
                        conn.execute(f"ALTER TABLE price_drop_alerts ADD COLUMN {name} {ctype}")
                    except Exception:
                        pass
            conn.commit()

    def _persist_engine_alerts(self, alerts: List[Dict[str, Any]]) -> int:
        self._ensure_tables()

        with sqlite3.connect(self._db_path) as conn:
            conn.row_factory = sqlite3.Row
            dismissed_keys = self._load_recent_dismissed_keys(conn)

            # Replace open engine alerts
            conn.execute("DELETE FROM price_drop_alerts WHERE status='open' AND source='engine'")

            inserted = 0
            for a in alerts:
                key = AlertKey(item_id=int(a["item_id"]), store_id=int(a["store_id"]), alert_kind=str(a["alert_kind"]))
                if key in dismissed_keys:
                    continue

                conn.execute(
                    """
                    INSERT INTO price_drop_alerts
                    (item_id, store_id, store_name, item_name, current_price, usual_price, pct_below_usual,
                     six_month_low, pct_above_low, alert_kind, is_staple, receipt_samples, basis, source,
                     last_seen_at_or_below, notes, created_at, status)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'), 'open')
                    """,
                    (
                        int(a["item_id"]),
                        int(a["store_id"]),
                        str(a.get("store_name") or "Unknown"),
                        str(a.get("item_name") or ""),
                        float(a["current_price"]),
                        float(a["usual_price"]) if a.get("usual_price") is not None else None,
                        float(a["pct_below_usual"]) if a.get("pct_below_usual") is not None else None,
                        float(a["six_month_low"]) if a.get("six_month_low") is not None else None,
                        float(a["pct_above_low"]) if a.get("pct_above_low") is not None else None,
                        str(a.get("alert_kind") or ""),
                        int(a.get("is_staple") or 0),
                        int(a.get("receipt_samples") or 0),
                        str(a.get("basis") or ""),
                        "engine",
                        a.get("last_seen_at_or_below"),
                        a.get("notes"),
                    ),
                )
                inserted += 1

            conn.commit()
            return inserted

    def _load_recent_dismissed_keys(self, conn: sqlite3.Connection) -> Set[AlertKey]:
        rows = conn.execute(
            """
            SELECT item_id, store_id, alert_kind, dismissed_at
            FROM price_drop_alerts
            WHERE status = 'dismissed'
              AND dismissed_at IS NOT NULL
              AND date(dismissed_at) >= date('now', ?)
            """,
            (f"-{int(self.ALERT_SUPPRESSION_DAYS)} days",),
        ).fetchall()

        out: Set[AlertKey] = set()
        for r in rows:
            try:
                out.add(AlertKey(int(r[0] or 0), int(r[1] or 0), str(r[2] or "")))
            except Exception:
                pass
        return out

    def _passes_stockup_cooldown(self, last_seen_iso: Optional[str]) -> bool:
        # If we can't tell, allow (it will still be near-low)
        if not last_seen_iso:
            return True
        try:
            dt = datetime.fromisoformat(str(last_seen_iso).replace("Z", "+00:00"))
        except Exception:
            try:
                dt = datetime.strptime(str(last_seen_iso)[:19], "%Y-%m-%d %H:%M:%S")
            except Exception:
                return True
        return (datetime.now() - dt).days >= int(self.STOCK_UP_COOLDOWN_DAYS)

    def _build_notes(
        self,
        *,
        item_name: str,
        store_name: str,
        current: float,
        usual: Optional[float],
        pct_below: Optional[float],
        low: Optional[float],
        pct_over_low: Optional[float],
        kind: str,
        basis: str,
        samples: int,
        last_seen: Optional[str],
        low_when: Optional[str],
    ) -> str:
        parts: List[str] = []
        if kind in ("below_usual", "both") and usual is not None and pct_below is not None:
            parts.append(f"Dropped {pct_below:.0f}% below usual (${current:.2f} vs ${usual:.2f}).")
        if kind in ("stock_up", "both") and low is not None:
            if pct_over_low is not None and pct_over_low >= 0:
                parts.append(f"Near 6-month low (${current:.2f}; low ${low:.2f}, +{pct_over_low:.1f}%).")
            else:
                parts.append(f"Near 6-month low (${current:.2f}; low ${low:.2f}).")

        if basis == "receipt_median":
            parts.append(f"Usual is receipt median (samples: {samples}).")
        elif basis == "estimated_median":
            parts.append(f"Usual is estimated median (samples: {samples}).")
        else:
            parts.append("Usual price is unknown/insufficient history.")

        if low_when:
            parts.append(f"6-mo low seen on: {str(low_when)[:10]}.")
        if last_seen:
            parts.append(f"Last time at/under this price: {str(last_seen)[:10]}.")

        parts.append(f"Best current store: {store_name}.")

        return " ".join(parts).strip()


def get_price_drop_alert_service(*, log=None) -> PriceDropAlertService:
    return PriceDropAlertService(log=log)
