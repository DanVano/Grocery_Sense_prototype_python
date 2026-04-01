from __future__ import annotations

from contextlib import closing
from datetime import datetime, timedelta
from typing import List, Optional, Tuple, Dict, Any

from Grocery_Sense.data.connection import get_connection
from Grocery_Sense.domain.models import PricePoint, PriceStats


def add_price_point(
    item_id: int,
    store_id: int,
    unit_price: float,
    unit: str,
    quantity: Optional[float] = None,
    total_price: Optional[float] = None,
    raw_name: Optional[str] = None,
    confidence: Optional[int] = None,
    source: str = "manual",
    date: Optional[str] = None,
    receipt_id: Optional[int] = None,
    flyer_source_id: Optional[int] = None,
) -> int:
    """
    Inserts a new price point into the prices table.
    Returns inserted row id.
    """
    if date is None:
        date = datetime.now().strftime("%Y-%m-%d")

    with closing(get_connection()) as conn:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO prices (
                item_id, store_id, receipt_id, flyer_source_id, source, date,
                unit_price, unit, quantity, total_price, raw_name, confidence
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            ),
        )
        conn.commit()
        return int(cur.lastrowid)


def get_prices_for_item(
    item_id: int,
    store_id: Optional[int] = None,
    since_days: int = 365,
) -> List[PricePoint]:
    """
    Returns price points for an item, optionally filtered by store.
    """
    cutoff = (datetime.now() - timedelta(days=since_days)).strftime("%Y-%m-%d")

    sql = """
        SELECT id, item_id, store_id, receipt_id, flyer_source_id, source, date,
               unit_price, unit, quantity, total_price, raw_name, confidence
        FROM prices
        WHERE item_id = ? AND date >= ?
    """
    params: List[Any] = [item_id, cutoff]

    if store_id is not None:
        sql += " AND store_id = ?"
        params.append(store_id)

    sql += " ORDER BY date ASC"

    out: List[PricePoint] = []
    with closing(get_connection()) as conn:
        cur = conn.execute(sql, params)
        rows = cur.fetchall()
        for r in rows:
            out.append(
                PricePoint(
                    id=r[0],
                    item_id=r[1],
                    store_id=r[2],
                    receipt_id=r[3],
                    flyer_source_id=r[4],
                    source=r[5],
                    date=r[6],
                    unit_price=r[7],
                    unit=r[8],
                    quantity=r[9],
                    total_price=r[10],
                    raw_name=r[11],
                    confidence=r[12],
                )
            )
    return out


def get_most_recent_price(item_id: int, store_id: Optional[int] = None) -> Optional[PricePoint]:
    """
    Returns the most recent price point for item (optionally by store).
    """
    sql = """
        SELECT id, item_id, store_id, receipt_id, flyer_source_id, source, date,
               unit_price, unit, quantity, total_price, raw_name, confidence
        FROM prices
        WHERE item_id = ?
    """
    params: List[Any] = [item_id]

    if store_id is not None:
        sql += " AND store_id = ?"
        params.append(store_id)

    sql += " ORDER BY date DESC, id DESC LIMIT 1"

    with closing(get_connection()) as conn:
        row = conn.execute(sql, params).fetchone()
        if not row:
            return None
        return PricePoint(
            id=row[0],
            item_id=row[1],
            store_id=row[2],
            receipt_id=row[3],
            flyer_source_id=row[4],
            source=row[5],
            date=row[6],
            unit_price=row[7],
            unit=row[8],
            quantity=row[9],
            total_price=row[10],
            raw_name=row[11],
            confidence=row[12],
        )


def get_price_stats_for_item(item_id: int, store_id: Optional[int] = None, since_days: int = 365) -> PriceStats:
    """
    Returns basic stats for an item's price history.
    """
    points = get_prices_for_item(item_id, store_id=store_id, since_days=since_days)
    if not points:
        return PriceStats(item_id=item_id, store_id=store_id, min_price=None, max_price=None, avg_price=None, count=0)

    prices = [p.unit_price for p in points if p.unit_price is not None]
    if not prices:
        return PriceStats(item_id=item_id, store_id=store_id, min_price=None, max_price=None, avg_price=None, count=0)

    return PriceStats(
        item_id=item_id,
        store_id=store_id,
        min_price=min(prices),
        max_price=max(prices),
        avg_price=sum(prices) / len(prices),
        count=len(prices),
    )


# ---------- Advanced query helpers (Milestone 2: usual price + 6-mo low + staples) ----------

def _median(values: List[float]) -> Optional[float]:
    """Return the median of a list of floats (None if empty)."""
    if not values:
        return None
    vals = sorted(v for v in values if v is not None)
    if not vals:
        return None
    n = len(vals)
    mid = n // 2
    if n % 2 == 1:
        return float(vals[mid])
    return float((vals[mid - 1] + vals[mid]) / 2.0)


def _since_clause(days: int) -> str:
    # SQLite date modifier like '-180 day'
    days = int(max(1, days))
    return f"-{days} day"


def list_unit_prices(
    item_id: int,
    *,
    store_id: Optional[int] = None,
    since_days: int = 180,
    sources: Optional[List[str]] = None,
    receipt_only: bool = False,
    limit: Optional[int] = None,
) -> List[float]:
    """Return unit_price history for an item.

    Notes:
      - Uses COALESCE(date, created_at) for time filtering.
      - If receipt_only=True, filters to rows that look like receipt line-items.
    """
    sql = [
        "SELECT unit_price",
        "FROM prices",
        "WHERE item_id = ?",
        "  AND unit_price IS NOT NULL",
        "  AND date(COALESCE(date, created_at)) >= date('now', ?)",
    ]
    params: List[Any] = [int(item_id), _since_clause(since_days)]

    if store_id is not None:
        sql.append("  AND store_id = ?")
        params.append(int(store_id))

    if receipt_only:
        sql.append("  AND (source = 'receipt' OR receipt_id IS NOT NULL)")
    elif sources:
        placeholders = ",".join(["?"] * len(sources))
        sql.append(f"  AND source IN ({placeholders})")
        params.extend([str(s) for s in sources])

    sql.append("ORDER BY COALESCE(date, created_at) DESC")
    if limit:
        sql.append("LIMIT ?")
        params.append(int(limit))

    with closing(get_connection()) as conn:
        cur = conn.execute("\n".join(sql), params)
        return [float(r[0]) for r in cur.fetchall() if r and r[0] is not None]


def get_usual_unit_price(
    item_id: int,
    *,
    store_id: Optional[int] = None,
    receipt_only: bool = True,
    min_samples: int = 4,
    since_days: int = 180,
) -> Tuple[Optional[float], int, str]:
    """Compute a 'usual' unit price.

    Returns: (usual_price, sample_count, basis)
      basis: 'receipt_median' | 'estimated_median' | 'unknown'
    """
    prices = list_unit_prices(
        item_id,
        store_id=store_id,
        since_days=since_days,
        receipt_only=receipt_only,
    )
    if len(prices) >= int(min_samples):
        med = _median(prices)
        return (med, len(prices), "receipt_median" if receipt_only else "estimated_median")

    if receipt_only:
        fallback = list_unit_prices(
            item_id,
            store_id=store_id,
            since_days=since_days,
            receipt_only=False,
        )
        if fallback:
            return (_median(fallback), len(fallback), "estimated_median")

    return (None, len(prices), "unknown")


def get_six_month_low_unit_price(
    item_id: int,
    *,
    store_id: Optional[int] = None,
    since_days: int = 183,
) -> Tuple[Optional[float], Optional[str]]:
    """Return (lowest_unit_price, when_iso) within the lookback window."""
    sql = [
        "SELECT unit_price, COALESCE(date, created_at) AS when_iso",
        "FROM prices",
        "WHERE item_id = ?",
        "  AND unit_price IS NOT NULL",
        "  AND date(COALESCE(date, created_at)) >= date('now', ?)",
    ]
    params: List[Any] = [int(item_id), _since_clause(since_days)]

    if store_id is not None:
        sql.append("  AND store_id = ?")
        params.append(int(store_id))

    sql.append("ORDER BY unit_price ASC, when_iso ASC")
    sql.append("LIMIT 1")

    with closing(get_connection()) as conn:
        cur = conn.execute("\n".join(sql), params)
        row = cur.fetchone()
        if not row:
            return (None, None)
        return (float(row[0]), str(row[1]) if row[1] else None)


def get_last_seen_at_or_below(
    item_id: int,
    *,
    store_id: Optional[int] = None,
    price_ceiling: float,
    since_days: int = 183,
) -> Optional[str]:
    """Most recent date we saw unit_price <= price_ceiling (within lookback)."""
    sql = [
        "SELECT COALESCE(date, created_at) AS when_iso",
        "FROM prices",
        "WHERE item_id = ?",
        "  AND unit_price IS NOT NULL",
        "  AND unit_price <= ?",
        "  AND date(COALESCE(date, created_at)) >= date('now', ?)",
    ]
    params: List[Any] = [int(item_id), float(price_ceiling), _since_clause(since_days)]
    if store_id is not None:
        sql.append("  AND store_id = ?")
        params.append(int(store_id))
    sql.append("ORDER BY when_iso DESC")
    sql.append("LIMIT 1")

    with closing(get_connection()) as conn:
        cur = conn.execute("\n".join(sql), params)
        row = cur.fetchone()
        return str(row[0]) if row and row[0] else None


def get_active_flyer_unit_price(
    item_id: int,
    store_id: int,
) -> Optional[float]:
    """Return the active flyer unit price if we can resolve it.

    Priority:
      1) Join with flyer_sources when present.
      2) Fallback: any 'flyer' price recorded in the last ~3 weeks.
    """
    with closing(get_connection()) as conn:
        cur = conn.cursor()

        # 1) Try flyer_sources join (table/column may not exist in early prototypes)
        try:
            cur.execute(
                """
                SELECT p.unit_price
                FROM prices p
                JOIN flyer_sources fs ON fs.id = p.flyer_source_id
                WHERE p.item_id = ?
                  AND p.store_id = ?
                  AND p.unit_price IS NOT NULL
                  AND p.source = 'flyer'
                  AND date(fs.valid_from) <= date('now')
                  AND date(fs.valid_to) >= date('now')
                ORDER BY p.unit_price ASC
                LIMIT 1
                """,
                (int(item_id), int(store_id)),
            )
            row = cur.fetchone()
            if row and row[0] is not None:
                return float(row[0])
        except Exception:
            pass

        # 2) Fallback: recent flyer rows
        try:
            cur.execute(
                """
                SELECT unit_price
                FROM prices
                WHERE item_id = ?
                  AND store_id = ?
                  AND unit_price IS NOT NULL
                  AND source = 'flyer'
                  AND date(COALESCE(date, created_at)) >= date('now', '-21 day')
                ORDER BY unit_price ASC
                LIMIT 1
                """,
                (int(item_id), int(store_id)),
            )
            row = cur.fetchone()
            if row and row[0] is not None:
                return float(row[0])
        except Exception:
            pass

    return None


def list_staple_item_ids(
    *,
    since_days: int = 90,
    min_distinct_receipts: int = 3,
    min_line_items: int = 4,
) -> List[Tuple[int, int, int]]:
    """Return likely staple items based on receipt history.

    Returns list of tuples:
      (item_id, line_count, distinct_receipt_count)
    """
    sql = """
    SELECT
        item_id,
        COUNT(*) AS line_count,
        COUNT(DISTINCT receipt_id) AS receipt_count
    FROM prices
    WHERE item_id IS NOT NULL
      AND unit_price IS NOT NULL
      AND (source = 'receipt' OR receipt_id IS NOT NULL)
      AND date(COALESCE(date, created_at)) >= date('now', ?)
    GROUP BY item_id
    HAVING line_count >= ? OR receipt_count >= ?
    ORDER BY receipt_count DESC, line_count DESC
    """

    with closing(get_connection()) as conn:
        cur = conn.execute(sql, (_since_clause(since_days), int(min_line_items), int(min_distinct_receipts)))
        return [(int(r[0]), int(r[1]), int(r[2])) for r in cur.fetchall()]


def get_best_current_quote_for_item_store(
    item_id: int,
    store_id: int,
) -> Optional[Dict[str, Any]]:
    """Best-effort current quote for an item/store.

    Preference order:
      flyer (active) -> most recent store price (any source)
    """
    flyer = get_active_flyer_unit_price(item_id, store_id)
    if flyer is not None:
        return {"unit_price": float(flyer), "source": "flyer"}

    latest = get_most_recent_price(item_id, store_id=store_id)
    if latest and latest.unit_price is not None:
        return {"unit_price": float(latest.unit_price), "source": latest.source or "latest"}

    return None


# ---------------------------------------------------------------------------
# Batch query helpers — replace N+1 loops with single SQL round-trips
# ---------------------------------------------------------------------------

def _price_cols() -> str:
    """Column list used by all batch SELECT statements (matches PricePoint field order)."""
    return (
        "id, item_id, store_id, receipt_id, flyer_source_id, source, date, "
        "unit_price, unit, quantity, total_price, raw_name, confidence"
    )


def _row_to_price_point(r) -> PricePoint:
    return PricePoint(
        id=r[0], item_id=r[1], store_id=r[2], receipt_id=r[3],
        flyer_source_id=r[4], source=r[5], date=r[6],
        unit_price=r[7], unit=r[8], quantity=r[9],
        total_price=r[10], raw_name=r[11], confidence=r[12],
    )


def get_most_recent_prices_by_store_batch(
    item_ids: List[int],
    store_ids: List[int],
) -> Dict[Tuple[int, int], PricePoint]:
    """Return the most recent PricePoint per (item_id, store_id) in a single query.

    Replaces N×M calls to get_most_recent_price(item_id, store_id=store_id).
    Returns {(item_id, store_id): PricePoint}.
    """
    if not item_ids or not store_ids:
        return {}

    item_csv = ",".join(str(int(x)) for x in item_ids)
    store_csv = ",".join(str(int(x)) for x in store_ids)

    sql = f"""
        SELECT {_price_cols()}
        FROM (
            SELECT {_price_cols()},
                   ROW_NUMBER() OVER (
                       PARTITION BY item_id, store_id
                       ORDER BY date DESC, id DESC
                   ) AS rn
            FROM prices
            WHERE item_id  IN ({item_csv})
              AND store_id IN ({store_csv})
              AND unit_price IS NOT NULL
        ) WHERE rn = 1
    """

    out: Dict[Tuple[int, int], PricePoint] = {}
    with closing(get_connection()) as conn:
        for r in conn.execute(sql).fetchall():
            pp = _row_to_price_point(r)
            out[(pp.item_id, pp.store_id)] = pp
    return out


def get_most_recent_prices_global_batch(
    item_ids: List[int],
) -> Dict[int, PricePoint]:
    """Return the most recent PricePoint per item_id (across all stores) in a single query.

    Replaces N calls to get_most_recent_price(item_id, store_id=None).
    Returns {item_id: PricePoint}.
    """
    if not item_ids:
        return {}

    item_csv = ",".join(str(int(x)) for x in item_ids)

    sql = f"""
        SELECT {_price_cols()}
        FROM (
            SELECT {_price_cols()},
                   ROW_NUMBER() OVER (
                       PARTITION BY item_id
                       ORDER BY date DESC, id DESC
                   ) AS rn
            FROM prices
            WHERE item_id IN ({item_csv})
              AND unit_price IS NOT NULL
        ) WHERE rn = 1
    """

    out: Dict[int, PricePoint] = {}
    with closing(get_connection()) as conn:
        for r in conn.execute(sql).fetchall():
            pp = _row_to_price_point(r)
            out[pp.item_id] = pp
    return out


def get_active_flyer_prices_batch(
    item_ids: List[int],
    store_ids: List[int],
) -> Dict[Tuple[int, int], Dict[str, Any]]:
    """Return the lowest active flyer unit_price per (item_id, store_id) in a single query.

    Replaces N×M calls to get_active_flyer_unit_price(item_id, store_id).
    Returns {(item_id, store_id): {"unit_price": float, "source": "flyer"}}.
    """
    if not item_ids or not store_ids:
        return {}

    item_csv = ",".join(str(int(x)) for x in item_ids)
    store_csv = ",".join(str(int(x)) for x in store_ids)

    sql = f"""
        SELECT p.item_id, p.store_id, MIN(p.unit_price) AS unit_price
        FROM prices p
        JOIN flyer_sources fs ON fs.id = p.flyer_source_id
        WHERE p.source      = 'flyer'
          AND p.item_id  IN ({item_csv})
          AND p.store_id IN ({store_csv})
          AND p.unit_price IS NOT NULL
          AND date(fs.valid_from) <= date('now')
          AND date(fs.valid_to)   >= date('now')
        GROUP BY p.item_id, p.store_id
    """

    out: Dict[Tuple[int, int], Dict[str, Any]] = {}
    try:
        with closing(get_connection()) as conn:
            for r in conn.execute(sql).fetchall():
                item_id = int(r[0])
                store_id = int(r[1])
                unit_price = float(r[2])
                out[(item_id, store_id)] = {"unit_price": unit_price, "source": "flyer"}
    except Exception:
        pass
    return out


def get_price_stats_batch(
    item_ids: List[int],
    since_days: int = 180,
) -> Dict[int, PriceStats]:
    """Return PriceStats per item_id in a single query.

    Replaces N calls to get_price_stats_for_item(item_id, since_days=...).
    Returns {item_id: PriceStats}. Items with no history are omitted.
    """
    if not item_ids:
        return {}

    item_csv = ",".join(str(int(x)) for x in item_ids)

    sql = f"""
        SELECT
            item_id,
            MIN(unit_price) AS min_price,
            MAX(unit_price) AS max_price,
            AVG(unit_price) AS avg_price,
            COUNT(*)        AS cnt
        FROM prices
        WHERE item_id IN ({item_csv})
          AND unit_price IS NOT NULL
          AND date(COALESCE(date, created_at)) >= date('now', '{_since_clause(since_days)}')
        GROUP BY item_id
    """

    out: Dict[int, PriceStats] = {}
    with closing(get_connection()) as conn:
        for r in conn.execute(sql).fetchall():
            item_id = int(r[0])
            out[item_id] = PriceStats(
                item_id=item_id,
                store_id=None,
                min_price=float(r[1]),
                max_price=float(r[2]),
                avg_price=float(r[3]),
                count=int(r[4]),
            )
    return out
