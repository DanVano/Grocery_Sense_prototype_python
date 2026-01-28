from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Tuple

from Grocery_Sense.data.db import get_connection


@dataclass
class ShoppingListRow:
    id: int
    display_name: str
    quantity: float
    unit: str
    category: str
    is_checked_off: bool
    notes: str
    added_by_member_id: Optional[int]
    is_active: bool
    planned_store_id: Optional[int]


def _row_to_obj(row) -> ShoppingListRow:
    return ShoppingListRow(
        id=int(row["id"]),
        display_name=str(row["display_name"] or ""),
        quantity=float(row["quantity"] or 1.0),
        unit=str(row["unit"] or ""),
        category=str(row["category"] or ""),
        is_checked_off=bool(row["is_checked_off"] or 0),
        notes=str(row["notes"] or ""),
        added_by_member_id=int(row["added_by_member_id"]) if row["added_by_member_id"] is not None else None,
        is_active=bool(row["is_active"] or 0),
        planned_store_id=int(row["planned_store_id"]) if row["planned_store_id"] is not None else None,
    )


def list_active_items(*, store_id: Optional[int] = None) -> List[ShoppingListRow]:
    """
    Active + not deleted + not checked off items.

    If store_id is provided, filters by planned_store_id == store_id.
    """
    with get_connection() as conn:
        if store_id is None:
            rows = conn.execute(
                """
                SELECT
                    id,
                    display_name,
                    quantity,
                    unit,
                    category,
                    is_checked_off,
                    notes,
                    added_by_member_id,
                    is_active,
                    planned_store_id
                FROM shopping_list
                WHERE is_active = 1 AND is_deleted = 0 AND is_checked_off = 0
                ORDER BY id DESC
                """
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT
                    id,
                    display_name,
                    quantity,
                    unit,
                    category,
                    is_checked_off,
                    notes,
                    added_by_member_id,
                    is_active,
                    planned_store_id
                FROM shopping_list
                WHERE is_active = 1 AND is_deleted = 0 AND is_checked_off = 0
                  AND planned_store_id = ?
                ORDER BY id DESC
                """,
                (int(store_id),),
            ).fetchall()

    return [_row_to_obj(r) for r in rows]


def list_all_items() -> List[ShoppingListRow]:
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT
                id,
                display_name,
                quantity,
                unit,
                category,
                is_checked_off,
                notes,
                added_by_member_id,
                is_active,
                planned_store_id
            FROM shopping_list
            WHERE is_deleted = 0
            ORDER BY id DESC
            """
        ).fetchall()
    return [_row_to_obj(r) for r in rows]


def add_item(
    *,
    display_name: str,
    quantity: float = 1.0,
    unit: str = "",
    category: str = "",
    notes: str = "",
    added_by_member_id: Optional[int] = None,
) -> int:
    with get_connection() as conn:
        cur = conn.execute(
            """
            INSERT INTO shopping_list (display_name, quantity, unit, category, notes, added_by_member_id)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                (display_name or "").strip(),
                float(quantity or 1.0),
                (unit or "").strip(),
                (category or "").strip(),
                (notes or "").strip(),
                int(added_by_member_id) if added_by_member_id is not None else None,
            ),
        )
        conn.commit()
        return int(cur.lastrowid)


def set_checked_off(item_id: int, checked: bool) -> None:
    with get_connection() as conn:
        conn.execute(
            "UPDATE shopping_list SET is_checked_off = ? WHERE id = ?",
            (1 if checked else 0, int(item_id)),
        )
        conn.commit()


def delete_item(item_id: int) -> None:
    with get_connection() as conn:
        conn.execute("UPDATE shopping_list SET is_deleted = 1 WHERE id = ?", (int(item_id),))
        conn.commit()


def clear_all_items() -> None:
    with get_connection() as conn:
        conn.execute("UPDATE shopping_list SET is_deleted = 1")
        conn.commit()


# ---------------------------------------------------------------------------
# NEW: Planned store assignment (Milestone: “Use this plan”)
# ---------------------------------------------------------------------------

def clear_planned_store_ids_for_active_items(*, include_checked_off: bool = False) -> int:
    """
    Clears planned_store_id for active items (optionally including checked-off ones).
    Returns the number of rows affected (best-effort; sqlite may return -1 in some cases).
    """
    where = "is_active = 1 AND is_deleted = 0"
    if not include_checked_off:
        where += " AND is_checked_off = 0"

    with get_connection() as conn:
        cur = conn.execute(f"UPDATE shopping_list SET planned_store_id = NULL WHERE {where}")
        conn.commit()
        return int(cur.rowcount or 0)


def set_planned_store_id(item_id: int, planned_store_id: Optional[int]) -> None:
    with get_connection() as conn:
        conn.execute(
            "UPDATE shopping_list SET planned_store_id = ? WHERE id = ?",
            (int(planned_store_id) if planned_store_id is not None else None, int(item_id)),
        )
        conn.commit()


def bulk_set_planned_store_ids(assignments: List[Tuple[int, Optional[int]]]) -> int:
    """
    assignments = [(item_id, planned_store_id_or_None), ...]
    Returns number of attempted updates.
    """
    if not assignments:
        return 0

    rows = [(int(item_id), int(store_id) if store_id is not None else None) for (item_id, store_id) in assignments]

    with get_connection() as conn:
        conn.executemany(
            "UPDATE shopping_list SET planned_store_id = ? WHERE id = ?",
            [(store_id, item_id) for (item_id, store_id) in rows],
        )
        conn.commit()

    return len(rows)
