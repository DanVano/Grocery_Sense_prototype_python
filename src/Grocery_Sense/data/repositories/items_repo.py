"""
Grocery_Sense.data.repositories.items_repo

SQLite-backed persistence for Item objects.

This repository is intentionally **function-based** (not class-based) to match
the rest of the v1 backend. It provides the exact APIs used by services:

- create_item(...)
- get_item_by_id(...)
- get_item_by_name(...)         (case-insensitive exact match)
- list_all_item_names()         -> List[Tuple[int, str]] (id, canonical_name)

Table (from schema):
items(
  id INTEGER PK,
  canonical_name TEXT UNIQUE NOT NULL,
  category TEXT,
  default_unit TEXT,
  typical_package_size REAL,
  typical_package_unit TEXT,
  is_tracked INTEGER NOT NULL DEFAULT 1,
  notes TEXT,
  created_at TEXT NOT NULL DEFAULT datetime('now')
)
"""

from __future__ import annotations

from contextlib import closing
from typing import Dict, List, Optional, Tuple

from Grocery_Sense.data.connection import get_connection
from Grocery_Sense.domain.models import Item


# ---------------------------------------------------------------------------
# Row mapping
# ---------------------------------------------------------------------------


def _row_to_item(row) -> Item:
    """
    Accepts sqlite3.Row (dict-like) or tuple.
    We ignore created_at because the domain Item dataclass doesn't store it.
    """
    try:
        # sqlite3.Row
        return Item(
            id=int(row["id"]),
            canonical_name=str(row["canonical_name"]),
            category=row["category"],
            default_unit=row["default_unit"],
            typical_package_size=row["typical_package_size"],
            typical_package_unit=row["typical_package_unit"],
            is_tracked=bool(row["is_tracked"]),
            notes=row["notes"],
        )
    except Exception:
        # tuple fallback
        (
            item_id,
            canonical_name,
            category,
            default_unit,
            typical_package_size,
            typical_package_unit,
            is_tracked,
            notes,
            _created_at,
        ) = row
        return Item(
            id=int(item_id),
            canonical_name=str(canonical_name),
            category=category,
            default_unit=default_unit,
            typical_package_size=typical_package_size,
            typical_package_unit=typical_package_unit,
            is_tracked=bool(is_tracked),
            notes=notes,
        )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def create_item(
    canonical_name: str,
    category: Optional[str] = None,
    default_unit: Optional[str] = None,
    typical_package_size: Optional[float] = None,
    typical_package_unit: Optional[str] = None,
    is_tracked: bool = True,
    notes: Optional[str] = None,
) -> Item:
    """
    Insert a new item and return the created Item.

    If an item with the same canonical_name already exists (unique constraint),
    we return the existing item instead of raising.
    """
    name_clean = (canonical_name or "").strip()
    if not name_clean:
        raise ValueError("canonical_name cannot be empty")

    with get_connection() as conn, closing(conn.cursor()) as cur:
        try:
            cur.execute(
                """
                INSERT INTO items (
                    canonical_name,
                    category,
                    default_unit,
                    typical_package_size,
                    typical_package_unit,
                    is_tracked,
                    notes
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    name_clean,
                    category,
                    default_unit,
                    typical_package_size,
                    typical_package_unit,
                    1 if is_tracked else 0,
                    notes,
                ),
            )
            item_id = int(cur.lastrowid)
            conn.commit()
        except Exception:
            # Most likely UNIQUE constraint; return existing if present.
            existing = get_item_by_name(name_clean)
            if existing:
                return existing
            raise

    # Re-fetch (ensures we return normalized values from DB)
    created = get_item_by_id(item_id)
    if not created:
        # Extremely unlikely, but keep errors clear
        raise RuntimeError("create_item succeeded but could not re-fetch item")
    return created


def get_item_by_id(item_id: int) -> Optional[Item]:
    """
    Fetch an Item by id.
    """
    with get_connection() as conn, closing(conn.cursor()) as cur:
        cur.execute(
            """
            SELECT
                id,
                canonical_name,
                category,
                default_unit,
                typical_package_size,
                typical_package_unit,
                is_tracked,
                notes,
                created_at
            FROM items
            WHERE id = ?
            """,
            (int(item_id),),
        )
        row = cur.fetchone()
        return _row_to_item(row) if row else None


def get_item_by_name(canonical_name: str) -> Optional[Item]:
    """
    Case-insensitive exact match on canonical_name.

    This is intentionally strict (exact string match ignoring case) because
    fuzzy/alias logic belongs in IngredientMappingService + item_aliases_repo.
    """
    name_clean = (canonical_name or "").strip()
    if not name_clean:
        return None

    name_low = name_clean.lower()

    with get_connection() as conn, closing(conn.cursor()) as cur:
        cur.execute(
            """
            SELECT
                id,
                canonical_name,
                category,
                default_unit,
                typical_package_size,
                typical_package_unit,
                is_tracked,
                notes,
                created_at
            FROM items
            WHERE lower(canonical_name) = ?
            LIMIT 1
            """,
            (name_low,),
        )
        row = cur.fetchone()
        return _row_to_item(row) if row else None


def list_all_item_names() -> List[Tuple[int, str]]:
    """
    Return all items as (id, canonical_name), sorted A→Z by canonical_name.

    Used by IngredientMappingService for fuzzy matching.
    """
    with get_connection() as conn, closing(conn.cursor()) as cur:
        cur.execute(
            """
            SELECT id, canonical_name
            FROM items
            ORDER BY canonical_name ASC
            """
        )
        rows = cur.fetchall() or []
        out: List[Tuple[int, str]] = []
        for r in rows:
            try:
                out.append((int(r["id"]), str(r["canonical_name"])))
            except Exception:
                out.append((int(r[0]), str(r[1])))
        return out


# ---------------------------------------------------------------------------
# Optional helpers (not required by current services, but handy)
# ---------------------------------------------------------------------------


def list_items(include_untracked: bool = False) -> List[Item]:
    """
    List items, optionally including untracked ones.
    """
    with get_connection() as conn, closing(conn.cursor()) as cur:
        if include_untracked:
            cur.execute(
                """
                SELECT
                    id,
                    canonical_name,
                    category,
                    default_unit,
                    typical_package_size,
                    typical_package_unit,
                    is_tracked,
                    notes,
                    created_at
                FROM items
                ORDER BY canonical_name ASC
                """
            )
        else:
            cur.execute(
                """
                SELECT
                    id,
                    canonical_name,
                    category,
                    default_unit,
                    typical_package_size,
                    typical_package_unit,
                    is_tracked,
                    notes,
                    created_at
                FROM items
                WHERE is_tracked = 1
                ORDER BY canonical_name ASC
                """
            )

        rows = cur.fetchall() or []
        return [_row_to_item(r) for r in rows]


def set_item_tracked(item_id: int, is_tracked: bool) -> None:
    """
    Mark an item as tracked/untracked.
    """
    with get_connection() as conn, closing(conn.cursor()) as cur:
        cur.execute(
            """
            UPDATE items
            SET is_tracked = ?
            WHERE id = ?
            """,
            (1 if is_tracked else 0, int(item_id)),
        )
        conn.commit()


def get_items_by_ids(item_ids: List[int]) -> Dict[int, Item]:
    """Return a {item_id: Item} map for a list of ids in a single query.

    Replaces N calls to get_item_by_id(item_id) in loops.
    Missing ids are silently omitted from the result.
    """
    if not item_ids:
        return {}

    id_csv = ",".join(str(int(x)) for x in item_ids)
    sql = f"""
        SELECT id, canonical_name, category, default_unit,
               typical_package_size, typical_package_unit, is_tracked, notes, created_at
        FROM items
        WHERE id IN ({id_csv})
    """

    out: Dict[int, Item] = {}
    with closing(get_connection()) as conn:
        for row in conn.execute(sql).fetchall():
            item = _row_to_item(row)
            out[item.id] = item
    return out


def update_item_notes(item_id: int, notes: Optional[str]) -> None:
    """
    Update notes field.
    """
    with get_connection() as conn, closing(conn.cursor()) as cur:
        cur.execute(
            """
            UPDATE items
            SET notes = ?
            WHERE id = ?
            """,
            (notes, int(item_id)),
        )
        conn.commit()