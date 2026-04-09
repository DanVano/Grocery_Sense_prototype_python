"""
Grocery_Sense.main

Simple smoke-test harness for the Grocery Sense backend.

Run from project root with:
    python -m Grocery_Sense.main
"""

from pprint import pprint

from Grocery_Sense.data.schema import initialize_database
from Grocery_Sense.data.repositories.stores_repo import (
    create_store,
    list_stores,
)
from Grocery_Sense.data.repositories.shopping_list_repo import (
    add_item,
    list_active_items,
    set_checked_off,
    clear_checked_off_items,
)


def run_smoke_test() -> None:
    print("=== Grocery Sense smoke test starting ===")

    # 1) Ensure DB & tables exist
    print("[1] Initializing database schema...")
    initialize_database()
    print("    ✔ Schema initialization complete.\n")

    # 2) Create a sample store
    print("[2] Creating a sample store...")
    store = create_store(
        name="Test Grocery",
        address="123 Test Street",
        city="Coquitlam",
        postal_code="V3J 0P6",
        flipp_store_id="TEST_STORE_001",
        is_favorite=True,
        priority=10,
        notes="Sample store created by smoke test.",
    )
    print("    ✔ Created store:")
    pprint(store)
    print()

    # 3) List all stores
    print("[3] Listing all stores...")
    stores = list_stores()
    for s in stores:
        print(f" - [{s.id}] {s.name} (favorite={s.is_favorite}, priority={s.priority})")
    print()

    # 4) Add some shopping list items
    print("[4] Adding shopping list items...")
    milk = add_item(
        display_name="Milk 2L",
        quantity=1,
        unit="each",
        planned_store_id=store.id,
        added_by="smoke_test",
        notes="Test item: milk",
    )
    apples = add_item(
        display_name="Apples",
        quantity=6,
        unit="each",
        planned_store_id=store.id,
        added_by="smoke_test",
        notes="Test item: apples",
    )
    print("    ✔ Added items:")
    pprint(milk)
    pprint(apples)
    print()

    # 5) List active shopping list items
    print("[5] Listing active shopping list items...")
    active_items = list_active_items()
    for item in active_items:
        status = "✓" if item.is_checked_off else " "
        print(f" [{status}] ({item.id}) {item.display_name} x{item.quantity or ''} {item.unit or ''}")
    print()

    # 6) Mark one item as checked off
    if active_items:
        first_id = active_items[0].id
        print(f"[6] Marking item {first_id} as checked off...")
        set_checked_off(first_id, checked=True)

        active_items_after = list_active_items(include_checked_off=True)
        print("    Items after check-off (including checked):")
        for item in active_items_after:
            status = "✓" if item.is_checked_off else " "
            print(f" [{status}] ({item.id}) {item.display_name}")
        print()

    # 7) Clear checked-off items from active list
    print("[7] Clearing checked-off items from active list...")
    clear_checked_off_items()
    remaining = list_active_items(include_checked_off=True)
    print("    Remaining items (active or inactive, include_checked_off=True):")
    for item in remaining:
        status = "✓" if item.is_checked_off else " "
        active = "active" if item.is_active else "inactive"
        print(f" [{status}] ({item.id}) {item.display_name} [{active}]")
    print()

    print("=== Smoke test complete ===")


if __name__ == "__main__":
    run_smoke_test()
