"""
Smoke tests for the items_repo module (function-based API).
"""

from Grocery_Sense.data.schema import initialize_database
from Grocery_Sense.data.repositories import items_repo


def main():
    print("=== items_repo smoke test ===")
    initialize_database()

    item = items_repo.create_item(
        canonical_name="test chicken thighs",
        category="meat",
        default_unit="kg",
    )
    assert item.id is not None
    assert item.canonical_name == "test chicken thighs"
    print(f"Created: {item}")

    fetched = items_repo.get_item_by_id(item.id)
    assert fetched is not None
    assert fetched.canonical_name == "test chicken thighs"
    print(f"Fetched by id: {fetched}")

    by_name = items_repo.get_item_by_name("Test Chicken Thighs")
    assert by_name is not None
    assert by_name.id == item.id
    print(f"Fetched by name (case-insensitive): {by_name}")

    names = items_repo.list_all_item_names()
    assert any(iid == item.id for iid, _ in names)
    print(f"list_all_item_names: {len(names)} entries")

    all_items = items_repo.list_items()
    assert any(i.id == item.id for i in all_items)
    print(f"list_items: {len(all_items)} items")

    print("=== items_repo smoke test complete ===")


def test_items_repo_smoke():
    main()


if __name__ == "__main__":
    main()
