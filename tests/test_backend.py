"""
Standalone “brain” smoke test for Grocery Sense.

Run from the project /src directory with:

    python -m Grocery_Sense.brain_smoke

This exercises:
- DB schema initialization
- Store creation + listing
- Shopping list service basic flow
"""

from __future__ import annotations

from Grocery_Sense.data.schema import initialize_database
from Grocery_Sense.data.repositories.stores_repo import create_store, list_stores
from Grocery_Sense.services import shopping_list_service as sl_svc


def run_smoke_test() -> None:
    print("=== Grocery Sense brain smoke test starting ===")

    # -------------------------------------------------
    # [1] Ensure schema exists
    # -------------------------------------------------
    print("[1] Initializing database schema...")
    initialize_database()
    print("    ✔ Schema initialization complete.\n")

    # -------------------------------------------------
    # [2] Create a sample store
    # -------------------------------------------------
    print("[2] Creating a sample store...")
    store = create_store(
        name="Test Grocery",
        address="123 Test Street",
        city="Coquitlam",
        postal_code="V3J 0P6",
        flipp_store_id="TEST_STORE_001",
        is_favorite=True,
        priority=10,
        notes="Sample store created by brain_smoke test.",
    )
    print("    ✔ Created store:")
    print(store)
    print()

    # -------------------------------------------------
    # [3] List all stores
    # -------------------------------------------------
    print("[3] Listing all stores...")
    stores = list_stores()
    for s in stores:
        print(f" - [{s.id}] {s.name} (favorite={s.is_favorite}, priority={s.priority})")
    print()

    # -------------------------------------------------
    # [4] Add some shopping list items
    # -------------------------------------------------
    print("[4] Adding shopping list items...")

    milk_id = sl_svc.add_item("Milk 2L", quantity=1.0, unit="each", notes="Test item: milk")
    apples_id = sl_svc.add_item("Apples", quantity=6.0, unit="each", notes="Test item: apples")

    print(f"    Added items with ids: {milk_id}, {apples_id}")
    print()

    # -------------------------------------------------
    # [5] List active shopping list items
    # -------------------------------------------------
    print("[5] Listing active shopping list items...")
    active_items = sl_svc.get_active_items()
    for item in active_items:
        status = "x" if item.is_checked_off else " "
        print(f" [{status}] ({item.id}) {item.display_name} x{item.quantity} {item.unit}")
    print()

    # -------------------------------------------------
    # [6] Mark first item as checked off
    # -------------------------------------------------
    if active_items:
        first_id = active_items[0].id
        print(f"[6] Marking item {first_id} as checked off...")
        sl_svc.set_checked_off(first_id, True)

        all_items = sl_svc.get_all_items()
        print("    Items after check-off (including checked):")
        for item in all_items:
            status = "x" if item.is_checked_off else " "
            print(f" [{status}] ({item.id}) {item.display_name}")
        print()
    else:
        print("[6] No active items to mark as checked.\n")

    # -------------------------------------------------
    # [7] Clear checked-off items from active list
    # -------------------------------------------------
    print("[7] Clearing checked-off items from active list...")
    sl_svc.clear_all_items()

    remaining = sl_svc.get_all_items()
    print("    Remaining items:")
    for item in remaining:
        status = "x" if item.is_checked_off else " "
        active_flag = "active" if item.is_active else "inactive"
        print(f" [{status}] ({item.id}) {item.display_name} [{active_flag}]")

    print("\n=== Brain smoke test complete ===")


def main() -> None:
    run_smoke_test()


if __name__ == "__main__":
    main()
