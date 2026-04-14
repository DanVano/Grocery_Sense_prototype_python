from pprint import pprint

from Grocery_Sense.data.schema import initialize_database
from Grocery_Sense.data.repositories.stores_repo import create_store, list_stores
from Grocery_Sense.services.shopping_list_service import ShoppingListService


def run_smoke_test() -> None:
    print("=== Grocery Sense service-level smoke test starting ===")

    # 1) Init DB
    initialize_database()

    # 2) Ensure at least one store
    stores = list_stores()
    if stores:
        store = stores[0]
        print(f"Using existing store: [{store.id}] {store.name}")
    else:
        store = create_store(
            name="Test Grocery",
            address="123 Test Street",
            city="Coquitlam",
            postal_code="V3J 0P6",
            flipp_store_id="TEST_STORE_001",
            is_favorite=True,
            priority=10,
        )
        print(f"Created store: [{store.id}] {store.name}")

    # 3) Use the service
    svc = ShoppingListService()

    print("\nAdding items from text: 'apples, 2L milk, chicken thighs'")
    created = svc.add_items_from_text(
        "apples, 2L milk, chicken thighs",
        planned_store_id=store.id,
        added_by="smoke_test",
    )
    for item in created:
        pprint(item)

    print("\nCurrent list:")
    print(svc.summarize_list_for_display())

    if created:
        first_id = created[0].id
        print(f"\nMarking item #{first_id} as checked off...")
        svc.check_off_item(first_id, checked=True)

        print("\nList including checked-off:")
        print(svc.summarize_list_for_display(include_checked_off=True))

        print("\nClearing all checked-off items from active list...")
        svc.clear_all_checked_off()

        print("\nList after clear:")
        print(svc.summarize_list_for_display(include_checked_off=True))

    print("\n=== Service-level smoke test complete ===")


def test_shopping_list_service_smoke():
    run_smoke_test()


if __name__ == "__main__":
    run_smoke_test()
