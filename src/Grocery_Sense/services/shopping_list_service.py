from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from Grocery_Sense.data.repositories import shopping_list_repo
from Grocery_Sense.data.repositories.shopping_list_repo import ShoppingListRow


class ShoppingListService:
    """
    Object-oriented wrapper around the shopping_list module-level functions.
    Provides the interface expected by tests and UI code that instantiates this class.
    """

    def add_items_from_text(
        self,
        text: str,
        *,
        planned_store_id: Optional[int] = None,
        added_by: Optional[str] = None,
    ) -> List[ShoppingListRow]:
        """
        Parse a comma-separated string of item names and add each to the list.
        Returns the newly created ShoppingListRow objects.
        """
        member_id: Optional[int] = None
        created: List[ShoppingListRow] = []
        for raw in text.split(","):
            name = raw.strip()
            if not name:
                continue
            row_id = shopping_list_repo.add_item(
                display_name=name,
                notes=added_by or "",
                added_by_member_id=member_id,
            )
            if planned_store_id is not None:
                shopping_list_repo.set_planned_store_id(row_id, planned_store_id)
            rows = shopping_list_repo.list_active_items()
            match = next((r for r in rows if r.id == row_id), None)
            if match:
                created.append(match)
        return created

    def summarize_list_for_display(self, *, include_checked_off: bool = False) -> str:
        """Return a plain-text summary of the current shopping list."""
        if include_checked_off:
            items = shopping_list_repo.list_all_items()
        else:
            items = shopping_list_repo.list_active_items()
        if not items:
            return "(empty)"
        lines = []
        for it in items:
            status = "[x]" if it.is_checked_off else "[ ]"
            qty = f" x{it.quantity}" if it.quantity and it.quantity != 1.0 else ""
            unit = f" {it.unit}" if it.unit else ""
            lines.append(f"  {status} {it.display_name}{qty}{unit}")
        return "\n".join(lines)

    def check_off_item(self, item_id: int, *, checked: bool = True) -> None:
        shopping_list_repo.set_checked_off(item_id, checked)

    def clear_all_checked_off(self) -> None:
        """Mark all checked-off active items as deleted."""
        items = shopping_list_repo.list_all_items()
        for it in items:
            if it.is_checked_off and it.is_active:
                shopping_list_repo.delete_item(it.id)


def get_active_items(*, store_id: Optional[int] = None):
    return shopping_list_repo.list_active_items(store_id=store_id)


def get_all_items():
    return shopping_list_repo.list_all_items()


def add_item(
    display_name: str,
    *,
    quantity: float = 1.0,
    unit: str = "",
    category: str = "",
    notes: str = "",
    added_by_member_id: Optional[int] = None,
) -> int:
    return shopping_list_repo.add_item(
        display_name=display_name,
        quantity=quantity,
        unit=unit,
        category=category,
        notes=notes,
        added_by_member_id=added_by_member_id,
    )


def set_checked_off(item_id: int, checked: bool) -> None:
    shopping_list_repo.set_checked_off(item_id, checked)


def delete_item(item_id: int) -> None:
    shopping_list_repo.delete_item(item_id)


def clear_all_items() -> None:
    shopping_list_repo.clear_all_items()


# ---------------------------------------------------------------------------
# NEW: Apply basket optimizer plan back into shopping list (planned_store_id)
# ---------------------------------------------------------------------------

def clear_planned_stores_for_active_list(*, include_checked_off: bool = False) -> int:
    return shopping_list_repo.clear_planned_store_ids_for_active_items(include_checked_off=include_checked_off)


def apply_optimizer_plan_to_active_list(
    optimizer_result: Any,
    *,
    mode: str = "fast",
    clear_first: bool = True,
) -> Dict[str, Any]:
    """
    Takes BasketOptimizationResult from basket_optimizer_service and assigns planned_store_id
    onto the ACTIVE shopping list items.

    mode:
      - "fast": use best_single_store_plan
      - "savings": use best_two_store_plan

    Behavior:
      - clears existing planned_store_id for active items (optional)
      - assigns planned_store_id per planned line
      - if a line is HARD EXCLUDED, it will be left unassigned (planned_store_id = NULL)
        so it stands out as “unplanned” and you can show a warning.

    Returns a summary dict for UI.
    """
    mode_key = (mode or "fast").strip().lower()
    if mode_key in {"fast", "single", "one", "one_store"}:
        plan = getattr(optimizer_result, "best_single_store_plan", None)
        plan_label = "Fast trip (one store)"
    elif mode_key in {"savings", "two", "two_store", "split"}:
        plan = getattr(optimizer_result, "best_two_store_plan", None)
        plan_label = "Savings (up to two stores)"
    else:
        plan = getattr(optimizer_result, "best_single_store_plan", None)
        plan_label = "Fast trip (one store)"

    if not plan:
        return {
            "ok": False,
            "error": "No plan available to apply.",
            "mode": mode_key,
            "assigned": 0,
            "unassigned": 0,
            "cleared": 0,
        }

    if clear_first:
        cleared = clear_planned_stores_for_active_list(include_checked_off=False)
    else:
        cleared = 0

    assignments: List[Tuple[int, Optional[int]]] = []
    unassigned_hard_excluded = 0
    skipped_no_id = 0

    lines = list(getattr(plan, "lines", []) or [])
    for line in lines:
        item = getattr(line, "item", None)
        item_id = getattr(item, "id", None)
        if item_id is None:
            skipped_no_id += 1
            continue

        is_hard = bool(getattr(line, "is_hard_excluded", False))
        store_id = None if is_hard else getattr(line, "store_id", None)

        if store_id is None:
            if is_hard:
                unassigned_hard_excluded += 1

        assignments.append((int(item_id), int(store_id) if store_id is not None else None))

    updated = shopping_list_repo.bulk_set_planned_store_ids(assignments)

    warnings = list(getattr(optimizer_result, "warnings", []) or [])
    if unassigned_hard_excluded > 0:
        warnings = warnings + [f"{unassigned_hard_excluded} item(s) were hard-excluded by household preferences and were left unplanned."]

    return {
        "ok": True,
        "mode": mode_key,
        "plan_label": plan_label,
        "cleared": cleared,
        "attempted": len(assignments),
        "updated": updated,
        "assigned": sum(1 for _id, sid in assignments if sid is not None),
        "unassigned": sum(1 for _id, sid in assignments if sid is None),
        "unassigned_hard_excluded": unassigned_hard_excluded,
        "skipped_no_id": skipped_no_id,
        "warnings": warnings,
    }
