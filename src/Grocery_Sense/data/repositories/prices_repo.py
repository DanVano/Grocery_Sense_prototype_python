"""
Grocery_Sense.data.repositories.prices_repo

SQLite-backed persistence for PricePoint objects and basic price statistics.
"""

from __future__ import annotations

from typing import List, Optional, Tuple
from contextlib import closing
from datetime import datetime, timedelta

from Grocery_Sense.data.connection import get_connection
from Grocery_Sense.domain.models import PricePoint


# ---------- Row mapping helpers ----------

def _row_to_price_point(row) -> PricePoint:
    """
    Convert a SQLite row tuple into a PricePoint dataclass.
    Ordering must match the SELECTs below.
    """
    (
        price_id,
        item_id,
        store_id,
        receipt_id,
        flyer_source_id,
        source,
        date,
        unit_price,
        unit,
        quantity,
        total_price,
        raw_name,
        confidence,
        created_at,
    ) = row

    return PricePoint(
        id=price_id,
        item_id=item_id,
        store_id=store_id,
        source=source,
        date=date,
        unit_price=unit_price,
        unit=unit,
        quantity=quantity,
        total_price=total_price,
        receipt_id=receipt_id,
        flyer_source_id=flyer_source_id,
        raw_name=raw_name,
        confidence=confidence,
    )


# ---------- Insert operations ----------

def add_price_point(
    item_id: int,
    store_id: int,
    source: str,
    date: str,
    unit_price: float,
    unit: str,
    quantity: Optional[float] = None,
    total_price: Optional[float] = None,
    receipt_id: Optional[int] = None,
    flyer_source_id: Optional[int] = None,
    raw_name: Optional[str] = None,
    confidence: Optional[int] = None,
) -> PricePoint:
    """
    Insert a new price history entry and return the PricePoint.

    `source` should be one of: 'receipt', 'flyer', 'manual'.
    `date` is 'YYYY-MM-DD'.
    `unit_price` should be normalized (e.g. per kg).
    """
    now = datetime.utcnow().isoformat(timespec="seconds")

    with get_connection() as conn, closing(conn.cursor()) as cur:
        cur.execute(
            """
            INSERT INTO prices (
                item_id,
                store_id,
                receipt_id,
                flyer_source_id,
                source,
                date,
                unit_price,
                unit,
                quantity,
                total_price,
                raw_name,
                confidence,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                item_id,
                store_id,
                receipt_id,
                flyer_source_id,
                source,
                date,
                unit_price,
                unit,
                quantity,
                total_price,
                raw_name,
                confidence,
                now,
            ),
        )
        new_id = cur.lastrowid

        cur.execute(
            """
            SELECT
                id,
                item_id,
                store_id,
                receipt_id,
                flyer_source_id,
                source,
                date,
                unit_price,
                unit,
                quantity,
                total_price,
                raw_name,
                confidence,
                created_at
            FROM prices
            WHERE id = ?
            """,
            (new_id,),
        )
        row = cur.fetchone()

    return _row_to_price_point(row)


# ---------- Query helpers ----------

def get_prices_for_item(
    item_id: int,
    days_back: Optional[int] = None,
    store_id: Optional[int] = None,
    limit: Optional[int] = None,
) -> List[PricePoint]:
    """
    Fetch price history for a given item.

    - Optionally restrict to a store.
    - Optionally restrict to the last `days_back` days.
    - Optionally limit number of records (most recent first).
    """
    clauses = ["item_id = ?"]
    params: list = [item_id]

    if store_id is not None:
        clauses.append("store_id = ?")
        params.append(store_id)

    if days_back is not None and days_back > 0:
        cutoff_date = (datetime.utcnow() - timedelta(days=days_back)).date().isoformat()
        clauses.append("date >= ?")
        params.append(cutoff_date)

    where_sql = " AND ".join(clauses)
    limit_sql = f"LIMIT {int(limit)}" if limit is not None else ""

    query = f"""
        SELECT
            id,
            item_id,
            store_id,
            receipt_id,
            flyer_source_id,
            source,
            date,
            unit_price,
            unit,
            quantity,
            total_price,
            raw_name,
            confidence,
            created_at
        FROM prices
        WHERE {where_sql}
        ORDER BY date DESC, id DESC
        {limit_sql}
    """

    with get_connection() as conn, closing(conn.cursor()) as cur:
        cur.execute(query, params)
        rows = cur.fetchall()

    return [_row_to_price_point(r) for r in rows]


def get_most_recent_price(
    item_id: int,
    store_id: Optional[int] = None,
) -> Optional[PricePoint]:
    """
    Get the single most recent price entry for an item, optionally for one store.
    """
    pts = get_prices_for_item(
        item_id=item_id,
        days_back=None,
        store_id=store_id,
        limit=1,
    )
    return pts[0] if pts else None


def get_price_stats_for_item(
    item_id: int,
    window_days: int = 180,
) -> Optional[Tuple[float, float, float, int]]:
    """
    Compute basic statistics (avg, min, max, count) for an item over a window.

    Returns:
        (avg_unit_price, min_unit_price, max_unit_price, sample_count)
    or None if there are no data points.
    """
    cutoff_date = (datetime.utcnow() - timedelta(days=window_days)).date().isoformat()

    with get_connection() as conn, closing(conn.cursor()) as cur:
        cur.execute(
            """
            SELECT
                AVG(unit_price) AS avg_price,
                MIN(unit_price) AS min_price,
                MAX(unit_price) AS max_price,
                COUNT(*)        AS sample_count
            FROM prices
            WHERE item_id = ?
              AND date >= ?
            """,
            (item_id, cutoff_date),
        )
        row = cur.fetchone()

    if not row:
        return None

    avg_price, min_price, max_price, count = row
    if count == 0 or avg_price is None:
        return None

    return float(avg_price), float(min_price), float(max_price), int(count)

# ---------------------------------------------------------------------------
# NEW: Query helpers for price-drop alerts (usual price + 6-month low + staples)
# ---------------------------------------------------------------------------

from datetime import date, timedelta
from typing import Any


def _date_str_days_ago(days: int, *, as_of: Optional[date] = None) -> str:
    """Return YYYY-MM-DD for (as_of - days)."""
    base = as_of or date.today()
    return (base - timedelta(days=int(days))).isoformat()


def list_active_flyer_deal_quotes(*, as_of: Optional[date] = None) -> List[dict]:
    """Return mapped, active flyer deals with unit pricing.

    This reads from flyer_batches + flyer_deals (created/managed in flyers_repo.py).

    Output keys (best-effort):
      - deal_id, batch_id
      - store_id, store_name
      - item_id, item_name
      - unit_price, unit
      - valid_from, valid_to
      - raw_title
    """
    as_of = as_of or date.today()
    with closing(get_connection()) as con:
        cur = con.cursor()
        cur.execute(
            """
            SELECT
                d.id                 AS deal_id,
                d.batch_id           AS batch_id,
                d.store_id           AS store_id,
                COALESCE(s.name, '') AS store_name,
                d.item_id            AS item_id,
                COALESCE(i.canonical_name, d.raw_title, '') AS item_name,
                COALESCE(d.norm_unit_price, d.unit_price)   AS unit_price,
                COALESCE(d.norm_unit, d.unit, '')           AS unit,
                b.valid_from         AS valid_from,
                b.valid_to           AS valid_to,
                d.raw_title          AS raw_title
            FROM flyer_deals d
            JOIN flyer_batches b ON b.id = d.batch_id
            LEFT JOIN items i    ON i.id = d.item_id
            LEFT JOIN stores s   ON s.id = d.store_id
            WHERE d.item_id IS NOT NULL
              AND COALESCE(d.norm_unit_price, d.unit_price) IS NOT NULL
              AND date(?) >= date(b.valid_from)
              AND date(?) <= date(COALESCE(b.valid_to, b.valid_from))
            ORDER BY d.store_id, i.canonical_name
            """,
            (as_of.isoformat(), as_of.isoformat()),
        )
        rows = cur.fetchall() or []

    out: List[dict] = []
    for r in rows:
        try:
            (
                deal_id,
                batch_id,
                store_id,
                store_name,
                item_id,
                item_name,
                unit_price,
                unit,
                valid_from,
                valid_to,
                raw_title,
            ) = r
        except Exception:
            continue

        try:
            unit_price_f = float(unit_price)
        except Exception:
            continue

        out.append(
            {
                "deal_id": int(deal_id),
                "batch_id": int(batch_id),
                "store_id": int(store_id) if store_id is not None else None,
                "store_name": str(store_name or ""),
                "item_id": int(item_id) if item_id is not None else None,
                "item_name": str(item_name or raw_title or "").strip(),
                "unit_price": unit_price_f,
                "unit": str(unit or "").strip().lower(),
                "valid_from": valid_from,
                "valid_to": valid_to,
                "raw_title": str(raw_title or ""),
            }
        )
    return out


def get_receipt_unit_price_samples(
    item_id: int,
    store_id: int,
    *,
    days: int = 180,
    as_of: Optional[date] = None,
    limit: int = 2000,
) -> List[float]:
    """Fetch recent receipt unit_price samples for (item, store)."""
    since = _date_str_days_ago(days, as_of=as_of)
    with closing(get_connection()) as con:
        cur = con.cursor()
        cur.execute(
            """
            SELECT unit_price
            FROM prices
            WHERE item_id = ?
              AND store_id = ?
              AND source = 'receipt'
              AND unit_price IS NOT NULL
              AND date(date) >= date(?)
            ORDER BY date(date) DESC
            LIMIT ?
            """,
            (int(item_id), int(store_id), since, int(limit)),
        )
        rows = cur.fetchall() or []

    out: List[float] = []
    for (p,) in rows:
        try:
            out.append(float(p))
        except Exception:
            continue
    return out


def get_any_source_unit_price_samples(
    item_id: int,
    store_id: int,
    *,
    days: int = 180,
    as_of: Optional[date] = None,
    limit: int = 2000,
) -> List[float]:
    """Fetch recent unit_price samples for (item, store) across ANY source."""
    since = _date_str_days_ago(days, as_of=as_of)
    with closing(get_connection()) as con:
        cur = con.cursor()
        cur.execute(
            """
            SELECT unit_price
            FROM prices
            WHERE item_id = ?
              AND store_id = ?
              AND unit_price IS NOT NULL
              AND date(date) >= date(?)
            ORDER BY date(date) DESC
            LIMIT ?
            """,
            (int(item_id), int(store_id), since, int(limit)),
        )
        rows = cur.fetchall() or []

    out: List[float] = []
    for (p,) in rows:
        try:
            out.append(float(p))
        except Exception:
            continue
    return out


def _median(values: List[float]) -> Optional[float]:
    if not values:
        return None
    vals = sorted([v for v in values if v is not None])
    if not vals:
        return None
    n = len(vals)
    mid = n // 2
    if n % 2 == 1:
        return float(vals[mid])
    return float((vals[mid - 1] + vals[mid]) / 2.0)


def get_usual_unit_price_for_store(
    item_id: int,
    store_id: int,
    *,
    days: int = 180,
    as_of: Optional[date] = None,
    min_receipt_samples: int = 3,
) -> dict:
    """Compute "usual" price primarily from receipts, with optional fallback.

    Returns dict:
      - usual_price: Optional[float]
      - usual_source: 'receipt' | 'estimated' | 'unknown'
      - receipt_samples: int
      - estimated_samples: int
    """
    receipt_samples = get_receipt_unit_price_samples(item_id, store_id, days=days, as_of=as_of)
    receipt_med = _median(receipt_samples)

    if receipt_med is not None and len(receipt_samples) >= int(min_receipt_samples):
        return {
            "usual_price": float(receipt_med),
            "usual_source": "receipt",
            "receipt_samples": int(len(receipt_samples)),
            "estimated_samples": 0,
        }

    # Fallback: use any-source recent history if receipts are sparse.
    any_samples = get_any_source_unit_price_samples(item_id, store_id, days=days, as_of=as_of)
    any_med = _median(any_samples)

    if any_med is not None and len(any_samples) > 0:
        return {
            "usual_price": float(any_med),
            "usual_source": "estimated",
            "receipt_samples": int(len(receipt_samples)),
            "estimated_samples": int(len(any_samples)),
        }

    return {
        "usual_price": None,
        "usual_source": "unknown",
        "receipt_samples": int(len(receipt_samples)),
        "estimated_samples": 0,
    }


def get_six_month_low_for_store(
    item_id: int,
    store_id: int,
    *,
    days: int = 180,
    as_of: Optional[date] = None,
) -> Optional[float]:
    """Return the lowest unit_price recorded in the last N days for this store."""
    since = _date_str_days_ago(days, as_of=as_of)
    with closing(get_connection()) as con:
        cur = con.cursor()
        cur.execute(
            """
            SELECT MIN(unit_price)
            FROM prices
            WHERE item_id = ?
              AND store_id = ?
              AND unit_price IS NOT NULL
              AND date(date) >= date(?)
            """,
            (int(item_id), int(store_id), since),
        )
        row = cur.fetchone()

    if not row:
        return None
    try:
        return float(row[0]) if row[0] is not None else None
    except Exception:
        return None


def get_six_month_low_global(
    item_id: int,
    *,
    days: int = 180,
    as_of: Optional[date] = None,
) -> Optional[float]:
    """Return the lowest unit_price recorded in the last N days across ALL stores."""
    since = _date_str_days_ago(days, as_of=as_of)
    with closing(get_connection()) as con:
        cur = con.cursor()
        cur.execute(
            """
            SELECT MIN(unit_price)
            FROM prices
            WHERE item_id = ?
              AND unit_price IS NOT NULL
              AND date(date) >= date(?)
            """,
            (int(item_id), since),
        )
        row = cur.fetchone()

    if not row:
        return None
    try:
        return float(row[0]) if row[0] is not None else None
    except Exception:
        return None


def get_month_low_for_store(
    item_id: int,
    store_id: int,
    *,
    days: int = 30,
    as_of: Optional[date] = None,
) -> Optional[float]:
    """Return the lowest unit_price recorded in the last ~month for this store."""
    since = _date_str_days_ago(days, as_of=as_of)
    with closing(get_connection()) as con:
        cur = con.cursor()
        cur.execute(
            """
            SELECT MIN(unit_price)
            FROM prices
            WHERE item_id = ?
              AND store_id = ?
              AND unit_price IS NOT NULL
              AND date(date) >= date(?)
            """,
            (int(item_id), int(store_id), since),
        )
        row = cur.fetchone()

    if not row:
        return None
    try:
        return float(row[0]) if row[0] is not None else None
    except Exception:
        return None


def get_receipt_purchase_count(
    item_id: int,
    *,
    days: int = 90,
    as_of: Optional[date] = None,
    store_id: Optional[int] = None,
) -> int:
    """How often this item appears on receipts (approx) over the last N days.

    Uses COUNT(DISTINCT receipt_id) when possible; falls back to COUNT(*) if
    receipt_id is missing.
    """
    since = _date_str_days_ago(days, as_of=as_of)

    where_store = "" if store_id is None else " AND store_id = ? "
    params: List[Any] = [int(item_id), since]
    if store_id is not None:
        params.append(int(store_id))

    with closing(get_connection()) as con:
        cur = con.cursor()
        cur.execute(
            f"""
            SELECT
                CASE
                    WHEN SUM(CASE WHEN receipt_id IS NOT NULL THEN 1 ELSE 0 END) > 0
                    THEN COUNT(DISTINCT receipt_id)
                    ELSE COUNT(*)
                END AS purchase_count
            FROM prices
            WHERE item_id = ?
              AND source = 'receipt'
              AND unit_price IS NOT NULL
              AND date(date) >= date(?)
              {where_store}
            """,
            tuple(params),
        )
        row = cur.fetchone()

    try:
        return int(row[0] or 0) if row else 0
    except Exception:
        return 0
