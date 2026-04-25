"""
M5 — ShoppingListService

Folds the original tests/test_shopping_list_service.py smoke (add/check/clear)
and the legacy tests/test_backend.py repo-level smoke into proper assertions.

Covers:
  - add_items_from_text parsing + planned_store_id assignment
  - add_single_item quantity/unit passthrough
  - summarize_list_for_display on empty and populated lists
  - check_off_item + clear_all_checked_off (soft-delete behaviour)
  - Module-level function surface mirrors the class
  - apply_optimizer_plan_to_active_list translates a plan into planned_store_ids,
    including hard-exclusion handling
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from Grocery_Sense.data.repositories import shopping_list_repo
from Grocery_Sense.data.repositories.stores_repo import create_store
from Grocery_Sense.services import shopping_list_service as sl_mod
from Grocery_Sense.services.shopping_list_service import ShoppingListService


@pytest.fixture
def store(isolated_db):
    return create_store(name="Test Mart", is_favorite=True, priority=1)


@pytest.fixture
def svc() -> ShoppingListService:
    return ShoppingListService()


# ---------------------------------------------------------------------------
# add_items_from_text — comma-split parsing
# ---------------------------------------------------------------------------


class TestAddItemsFromText:
    def test_parses_comma_separated(self, svc, store):
        created = svc.add_items_from_text(
            "apples, bread, 2L milk",
            planned_store_id=store.id,
            added_by="test",
        )
        assert len(created) == 3
        names = {r.display_name for r in created}
        assert names == {"apples", "bread", "2L milk"}

    def test_assigns_planned_store(self, svc, store):
        created = svc.add_items_from_text("eggs", planned_store_id=store.id)
        assert len(created) == 1
        assert created[0].planned_store_id == store.id

    def test_no_store_leaves_planned_store_null(self, svc, isolated_db):
        created = svc.add_items_from_text("eggs")
        assert len(created) == 1
        assert created[0].planned_store_id is None

    def test_ignores_empty_tokens(self, svc, store):
        created = svc.add_items_from_text(
            ",, ,apples,, ,",
            planned_store_id=store.id,
        )
        assert len(created) == 1
        assert created[0].display_name == "apples"

    def test_empty_string_returns_empty(self, svc, store):
        assert svc.add_items_from_text("", planned_store_id=store.id) == []


# ---------------------------------------------------------------------------
# add_single_item
# ---------------------------------------------------------------------------


class TestAddSingleItem:
    def test_adds_with_quantity_and_unit(self, svc, store):
        row_id = svc.add_single_item(
            name="Apples",
            quantity=6,
            unit="each",
            planned_store_id=store.id,
            notes="Red Delicious",
        )
        assert row_id > 0
        items = svc.get_active_items()
        item = next(i for i in items if i.id == row_id)
        assert item.display_name == "Apples"
        assert item.quantity == 6.0
        assert item.unit == "each"
        assert item.notes == "Red Delicious"
        assert item.planned_store_id == store.id

    def test_defaults_quantity_to_one(self, svc, isolated_db):
        row_id = svc.add_single_item(name="milk")
        items = svc.get_active_items()
        item = next(i for i in items if i.id == row_id)
        assert item.quantity == 1.0


# ---------------------------------------------------------------------------
# summarize_list_for_display
# ---------------------------------------------------------------------------


class TestSummarize:
    def test_empty_list(self, svc, isolated_db):
        assert svc.summarize_list_for_display() == "(empty)"

    def test_includes_check_marker(self, svc, store):
        row_id = svc.add_single_item(name="bread", planned_store_id=store.id)
        svc.check_off_item(row_id, checked=True)

        summary = svc.summarize_list_for_display(include_checked_off=True)
        assert "[x]" in summary
        assert "bread" in summary

    def test_hides_checked_off_by_default(self, svc, store):
        row_id = svc.add_single_item(name="bread", planned_store_id=store.id)
        svc.check_off_item(row_id, checked=True)

        summary = svc.summarize_list_for_display(include_checked_off=False)
        assert "bread" not in summary


# ---------------------------------------------------------------------------
# check_off + clear_all_checked_off (soft-delete)
# ---------------------------------------------------------------------------


class TestCheckOffAndClear:
    def test_check_off_persists(self, svc, store):
        row_id = svc.add_single_item(name="bread", planned_store_id=store.id)
        svc.check_off_item(row_id, checked=True)

        item = next(
            i for i in svc.get_active_items(include_checked_off=True) if i.id == row_id
        )
        assert item.is_checked_off is True

    def test_clear_all_checked_off_soft_deletes(self, svc, store):
        a = svc.add_single_item(name="bread", planned_store_id=store.id)
        b = svc.add_single_item(name="milk", planned_store_id=store.id)
        svc.check_off_item(a, checked=True)

        svc.clear_all_checked_off()

        remaining_ids = {i.id for i in svc.get_active_items()}
        assert b in remaining_ids
        assert a not in remaining_ids  # soft-deleted

    def test_uncheck_restores(self, svc, store):
        row_id = svc.add_single_item(name="bread", planned_store_id=store.id)
        svc.check_off_item(row_id, checked=True)
        svc.check_off_item(row_id, checked=False)

        item = next(i for i in svc.get_active_items() if i.id == row_id)
        assert item.is_checked_off is False


# ---------------------------------------------------------------------------
# Module-level function surface
# ---------------------------------------------------------------------------


class TestModuleFunctions:
    def test_add_item_and_list(self, store):
        row_id = sl_mod.add_item(display_name="milk", quantity=1.0, unit="each")
        items = sl_mod.get_active_items()
        assert any(i.id == row_id for i in items)

    def test_set_checked_off_and_clear(self, store):
        row_id = sl_mod.add_item(display_name="bread")
        sl_mod.set_checked_off(row_id, True)
        sl_mod.clear_all_checked_off()
        assert all(i.id != row_id for i in sl_mod.get_active_items())


# ---------------------------------------------------------------------------
# apply_optimizer_plan_to_active_list — translate optimizer result → store plan
# ---------------------------------------------------------------------------


def _make_plan(mode: str, store_groups: dict, *, warnings=None):
    """
    Build a SimpleNamespace mimicking BasketOptimizationResult.

    store_groups: {store_id: [(item_id, hard_excluded), ...]}
    """
    stores = []
    for store_id, items in store_groups.items():
        store_ns = SimpleNamespace(
            store_id=store_id,
            items=[
                SimpleNamespace(item_id=item_id, hard_excluded=hard)
                for item_id, hard in items
            ],
        )
        stores.append(store_ns)
    return SimpleNamespace(mode=mode, stores=stores, warnings=warnings or [])


class TestApplyOptimizerPlan:
    def test_rejects_empty_plan(self, svc, store):
        result = sl_mod.apply_optimizer_plan_to_active_list(
            _make_plan(mode="one_store", store_groups={})
        )
        assert result["ok"] is False
        assert "error" in result

    def test_plan_applies_when_row_links_to_canonical_item(self, svc, store):
        """
        Post-fix: apply_optimizer_plan_to_active_list routes through
        bulk_set_planned_store_ids_by_item_id, matching shopping_list rows
        by their linked canonical item_id. Optimizer plans now land
        correctly even when shopping_list.id ≠ canonical item_id.
        """
        from Grocery_Sense.data.repositories.items_repo import create_item

        # Seed padding items so the canonical id diverges from the row id.
        for pad in ("filler_a", "filler_b", "filler_c"):
            create_item(canonical_name=pad)
        canonical = create_item(canonical_name="bread")
        row_id = svc.add_single_item(name="bread", item_id=canonical.id)
        assert row_id != canonical.id, "row id should diverge from canonical item id"

        plan = _make_plan(
            mode="one_store",
            store_groups={store.id: [(canonical.id, False)]},
        )
        result = sl_mod.apply_optimizer_plan_to_active_list(plan)
        assert result["ok"] is True
        assert result["assigned"] == 1
        assert result["updated"] == 1

        items = svc.get_active_items()
        bread = next(i for i in items if i.id == row_id)
        assert bread.planned_store_id == store.id

    def test_plan_silently_ignores_rows_that_dont_match_item_id(self, svc, store):
        """
        If a plan references an item_id that no active shopping_list row is
        linked to, the UPDATE matches nothing — no rows modified, no error.
        `attempted` counts the intent but `updated` reflects reality.
        """
        from Grocery_Sense.data.repositories.items_repo import create_item

        # Canonical item exists but no shopping_list row links to it.
        ghost = create_item(canonical_name="ghost")
        svc.add_single_item(name="something else")  # no item_id link

        plan = _make_plan(
            mode="one_store",
            store_groups={store.id: [(ghost.id, False)]},
        )
        result = sl_mod.apply_optimizer_plan_to_active_list(plan)
        assert result["ok"] is True
        assert result["attempted"] == 1
        assert result["updated"] == 0

    def test_hard_excluded_items_left_unassigned(self, svc, store):
        from Grocery_Sense.data.repositories.items_repo import create_item

        peanuts = create_item(canonical_name="peanuts")
        svc.add_single_item(name="peanuts", item_id=peanuts.id)

        plan = _make_plan(
            mode="one_store",
            store_groups={store.id: [(peanuts.id, True)]},  # hard-excluded
        )
        result = sl_mod.apply_optimizer_plan_to_active_list(plan)

        assert result["ok"] is True
        assert result["unassigned_hard_excluded"] == 1
        # The row is left with planned_store_id = NULL so the UI can flag it.
        items = svc.get_active_items()
        peanut_row = next(i for i in items if i.display_name == "peanuts")
        assert peanut_row.planned_store_id is None
        assert any("hard-excluded" in w for w in result["warnings"])

    def test_items_without_item_id_are_skipped(self, svc, store):
        plan = _make_plan(
            mode="one_store",
            store_groups={store.id: [(None, False), (42, False)]},
        )
        result = sl_mod.apply_optimizer_plan_to_active_list(plan)
        assert result["skipped_no_id"] == 1
        # The one with item_id=42 is attempted even if nothing matches in the list.
        assert result["attempted"] == 1
