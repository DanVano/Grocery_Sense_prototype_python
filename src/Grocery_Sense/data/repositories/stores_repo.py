"""
Grocery_Sense.data.repositories.stores_repo

SQLite-backed persistence for Store objects.
"""

from __future__ import annotations

from typing import List, Optional, Iterable
from contextlib import closing
from datetime import datetime

from Grocery_Sense.data.connection import get_connection
from Grocery_Sense.domain.models import Store


# ---------- Row mapping helpers ----------

def _row_to_store(row) -> Store:
    """
    Convert a SQLite row tuple into a Store dataclass.
    Expects the SELECT ordering used below.
    """
    (
        store_id,
        name,
        address,
        city,
        postal_code,
        flipp_store_id,
        is_favorite,
        priority,
        notes,
        created_at,
    ) = row

    return Store(
        id=store_id,
        name=name,
        address=address,
        city=city,
        postal_code=postal_code,
        flipp_store_id=flipp_store_id,
        is_favorite=bool(is_favorite),
        priority=priority or 0,
        notes=notes,
    )


# ---------- CRUD operations ----------

def create_store(
    name: str,
    address: Optional[str] = None,
    city: Optional[str] = None,
    postal_code: Optional[str] = None,
    flipp_store_id: Optional[str] = None,
    is_favorite: bool = False,
    priority: int = 0,
    notes: Optional[str] = None,
) -> Store:
    """
    Insert a new store and return the Store object.
    """
    with get_connection() as conn, closing(conn.cursor()) as cur:
        cur.execute(
            """
            INSERT INTO stores (
                name,
                address,
                city,
                postal_code,
                flipp_store_id,
                is_favorite,
                priority,
                notes,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                name,
                address,
                city,
                postal_code,
                flipp_store_id,
                1 if is_favorite else 0,
                priority,
                notes,
                datetime.utcnow().isoformat(timespec="seconds"),
            ),
        )
        store_id = cur.lastrowid

        cur.execute(
            """
            SELECT
                id, name, address, city, postal_code,
                flipp_store_id, is_favorite, priority, notes, created_at
            FROM stores
            WHERE id = ?
            """,
            (store_id,),
        )
        row = cur.fetchone()

    return _row_to_store(row)


def get_store_by_id(store_id: int) -> Optional[Store]:
    """
    Fetch a single store by ID.
    """
    with get_connection() as conn, closing(conn.cursor()) as cur:
        cur.execute(
            """
            SELECT
                id, name, address, city, postal_code,
                flipp_store_id, is_favorite, priority, notes, created_at
            FROM stores
            WHERE id = ?
            """,
            (store_id,),
        )
        row = cur.fetchone()

    return _row_to_store(row) if row else None


def list_stores(
    only_favorites: bool = False,
    order_by_priority: bool = True,
) -> List[Store]:
    """
    Return all stores, optionally only favorites, ordered by priority then name.
    """
    where_clause = "WHERE is_favorite = 1" if only_favorites else ""
    order_clause = "ORDER BY priority DESC, name ASC" if order_by_priority else "ORDER BY name ASC"

    query = f"""
        SELECT
            id, name, address, city, postal_code,
            flipp_store_id, is_favorite, priority, notes, created_at
        FROM stores
        {where_clause}
        {order_clause}
    """

    with get_connection() as conn, closing(conn.cursor()) as cur:
        cur.execute(query)
        rows = cur.fetchall()

    return [_row_to_store(r) for r in rows]


def set_store_favorite(store_id: int, is_favorite: bool, priority: Optional[int] = None) -> None:
    """
    Mark a store as favorite / not favorite and optionally update its priority.
    """
    with get_connection() as conn, closing(conn.cursor()) as cur:
        if priority is not None:
            cur.execute(
                """
                UPDATE stores
                SET is_favorite = ?, priority = ?
                WHERE id = ?
                """,
                (1 if is_favorite else 0, priority, store_id),
            )
        else:
            cur.execute(
                """
                UPDATE stores
                SET is_favorite = ?
                WHERE id = ?
                """,
                (1 if is_favorite else 0, store_id),
            )
        conn.commit()


def update_store_address(
    store_id: int,
    address: Optional[str] = None,
    city: Optional[str] = None,
    postal_code: Optional[str] = None,
) -> None:
    """
    Update address-related fields for a store.
    """
    with get_connection() as conn, closing(conn.cursor()) as cur:
        cur.execute(
            """
            UPDATE stores
            SET address = ?, city = ?, postal_code = ?
            WHERE id = ?
            """,
            (address, city, postal_code, store_id),
        )
        conn.commit()


def delete_store(store_id: int) -> None:
    """
    Hard delete a store. In the future we might prefer soft-delete.
    """
    with get_connection() as conn, closing(conn.cursor()) as cur:
        cur.execute("DELETE FROM stores WHERE id = ?", (store_id,))
        conn.commit()


# ---------- Flipp / external helpers ----------

def upsert_store_from_flipp(
    name: str,
    flipp_store_id: str,
    address: Optional[str] = None,
    city: Optional[str] = None,
    postal_code: Optional[str] = None,
) -> Store:
    """
    Ensure there is a Store row for a given Flipp store ID.
    If it exists, update basic info; otherwise, create it.
    """
    with get_connection() as conn, closing(conn.cursor()) as cur:
        cur.execute(
            """
            SELECT
                id, name, address, city, postal_code,
                flipp_store_id, is_favorite, priority, notes, created_at
            FROM stores
            WHERE flipp_store_id = ?
            """,
            (flipp_store_id,),
        )
        row = cur.fetchone()

        if row:
            # Update name/address if changed
            store = _row_to_store(row)
            if (
                store.name != name
                or store.address != address
                or store.city != city
                or store.postal_code != postal_code
            ):
                cur.execute(
                    """
                    UPDATE stores
                    SET name = ?, address = ?, city = ?, postal_code = ?
                    WHERE id = ?
                    """,
                    (name, address, city, postal_code, store.id),
                )
                conn.commit()
            return Store(
                id=store.id,
                name=name,
                address=address,
                city=city,
                postal_code=postal_code,
                flipp_store_id=flipp_store_id,
                is_favorite=store.is_favorite,
                priority=store.priority,
                notes=store.notes,
            )

        # Not found → create
        cur.execute(
            """
            INSERT INTO stores (
                name, address, city, postal_code,
                flipp_store_id, is_favorite, priority, notes, created_at
            )
            VALUES (?, ?, ?, ?, ?, 0, 0, NULL, ?)
            """,
            (
                name,
                address,
                city,
                postal_code,
                flipp_store_id,
                datetime.utcnow().isoformat(timespec="seconds"),
            ),
        )
        new_id = cur.lastrowid

        cur.execute(
            """
            SELECT
                id, name, address, city, postal_code,
                flipp_store_id, is_favorite, priority, notes, created_at
            FROM stores
            WHERE id = ?
            """,
            (new_id,),
        )
        new_row = cur.fetchone()

    return _row_to_store(new_row)
