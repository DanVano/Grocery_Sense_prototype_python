"""
M1 — shopping_list_repo (repo-level coverage)

Extends M5's service-level tests with direct repo coverage of edge cases:
  - add_item defaults (quantity=1.0, empty unit/category/notes)
  - list_active_items: store filter, include_checked_off
  - list_all_items includes checked_off but excludes is_deleted
  - set_checked_off round-trip
  - delete_item = soft-delete (is_deleted flag)
  - clear_all_items flips every row's is_deleted
  - clear_checked_off_items only clears checked + active rows
  - set_planned_store_id / clear_planned_store_ids_for_active_items
  - bulk_set_planned_store_ids
"""

from __future__ import annotations

import pytest

from Grocery_Sense.data.connection import get_connection
from Grocery_Sense.data.repositories.items_repo import create_item
from Grocery_Sense.data.repositories.shopping_list_repo import (
    add_item,
    bulk_set_planned_store_ids,
    bulk_set_planned_store_ids_by_item_id,
    clear_all_items,
    clear_checked_off_items,
    clear_planned_store_ids_for_active_items,
    delete_item,
    list_active_items,
    list_all_items,
    set_checked_off,
    set_planned_store_id,
)
from Grocery_Sense.data.repositories.stores_repo import create_store


# ---------------------------------------------------------------------------
# add_item
# ---------------------------------------------------------------------------


class TestAddItem:
    def test_defaults(self, isolated_db):
        rid = add_item(display_name="eggs")
        rows = list_active_items()
        assert len(rows) == 1
        row = rows[0]
        assert row.id == rid
        assert row.display_name == "eggs"
        assert row.quantity == 1.0
        assert row.unit == ""
        assert row.category == ""
        assert row.notes == ""
        assert row.is_checked_off is False
        assert row.is_active is True

    def test_all_fields(self, isolated_db):
        from Grocery_Sense.data.repositories.items_repo import create_item

        store = create_store(name="A")
        item = create_item(canonical_name="milk")
        rid = add_item(
            display_name="milk",
            quantity=2.0,
            unit="L",
            category="dairy",
            notes="whole",
            added_by="tester",
            added_by_member_id=7,
            planned_store_id=store.id,
            item_id=item.id,
        )
        row = next(r for r in list_active_items() if r.id == rid)
        assert row.quantity == 2.0
        assert row.unit == "L"
        assert row.category == "dairy"
        assert row.notes == "whole"
        assert row.added_by_member_id == 7
        assert row.planned_store_id == store.id
        assert row.item_id == item.id

    def test_strips_whitespace_in_display_name(self, isolated_db):
        add_item(display_name="   milk   ")
        assert list_active_items()[0].display_name == "milk"


# ---------------------------------------------------------------------------
# list_active_items
# ---------------------------------------------------------------------------


class TestListActiveItems:
    def test_excludes_checked_by_default(self, isolated_db):
        a = add_item(display_name="a")
        b = add_item(display_name="b")
        set_checked_off(a, True)
        names = {r.display_name for r in list_active_items()}
        assert names == {"b"}

    def test_include_checked_off(self, isolated_db):
        a = add_item(display_name="a")
        add_item(display_name="b")
        set_checked_off(a, True)
        names = {r.display_name for r in list_active_items(include_checked_off=True)}
        assert names == {"a", "b"}

    def test_store_filter(self, isolated_db):
        a_store = create_store(name="A")
        b_store = create_store(name="B")
        add_item(display_name="in A", planned_store_id=a_store.id)
        add_item(display_name="in B", planned_store_id=b_store.id)
        add_item(display_name="unplanned")

        assert [r.display_name for r in list_active_items(store_id=a_store.id)] == ["in A"]
        assert [r.display_name for r in list_active_items(store_id=b_store.id)] == ["in B"]

    def test_excludes_deleted(self, isolated_db):
        a = add_item(display_name="doomed")
        delete_item(a)
        assert list_active_items() == []


# ---------------------------------------------------------------------------
# list_all_items
# ---------------------------------------------------------------------------


class TestListAllItems:
    def test_includes_checked(self, isolated_db):
        a = add_item(display_name="a")
        set_checked_off(a, True)
        assert len(list_all_items()) == 1

    def test_excludes_soft_deleted(self, isolated_db):
        a = add_item(display_name="a")
        delete_item(a)
        assert list_all_items() == []


# ---------------------------------------------------------------------------
# set_checked_off
# ---------------------------------------------------------------------------


class TestSetCheckedOff:
    def test_round_trip(self, isolated_db):
        rid = add_item(display_name="a")
        set_checked_off(rid, True)
        row = next(r for r in list_active_items(include_checked_off=True) if r.id == rid)
        assert row.is_checked_off is True
        set_checked_off(rid, False)
        row = next(r for r in list_active_items() if r.id == rid)
        assert row.is_checked_off is False


# ---------------------------------------------------------------------------
# delete_item / clear_all_items
# ---------------------------------------------------------------------------


class TestDeleteAndClear:
    def test_delete_is_soft(self, isolated_db):
        rid = add_item(display_name="a")
        delete_item(rid)
        with get_connection() as c:
            row = c.execute(
                "SELECT is_deleted FROM shopping_list WHERE id = ?", (rid,)
            ).fetchone()
        assert row[0] == 1
        assert list_active_items() == []

    def test_clear_all_items_marks_every_row(self, isolated_db):
        add_item(display_name="a")
        add_item(display_name="b")
        clear_all_items()
        with get_connection() as c:
            row = c.execute(
                "SELECT COUNT(*) FROM shopping_list WHERE is_deleted = 1"
            ).fetchone()
        assert row[0] == 2

    def test_clear_checked_off_items_only_touches_checked_active(self, isolated_db):
        checked_active = add_item(display_name="checked active")
        set_checked_off(checked_active, True)

        unchecked = add_item(display_name="unchecked")
        checked_then_deleted = add_item(display_name="checked then deleted")
        set_checked_off(checked_then_deleted, True)
        delete_item(checked_then_deleted)

        clear_checked_off_items()

        # 'checked active' is now soft-deleted; unchecked remains active.
        remaining_active = {r.display_name for r in list_active_items(include_checked_off=True)}
        assert remaining_active == {"unchecked"}


# ---------------------------------------------------------------------------
# Planned store assignment
# ---------------------------------------------------------------------------


class TestPlannedStoreAssignment:
    def test_set_planned_store_id(self, isolated_db):
        store = create_store(name="A")
        rid = add_item(display_name="bread")
        set_planned_store_id(rid, store.id)
        assert next(r for r in list_active_items() if r.id == rid).planned_store_id == store.id

    def test_clear_to_none(self, isolated_db):
        store = create_store(name="A")
        rid = add_item(display_name="bread", planned_store_id=store.id)
        set_planned_store_id(rid, None)
        assert next(r for r in list_active_items() if r.id == rid).planned_store_id is None

    def test_clear_planned_store_ids_for_active_items(self, isolated_db):
        store = create_store(name="A")
        a = add_item(display_name="a", planned_store_id=store.id)
        b = add_item(display_name="b", planned_store_id=store.id)
        set_checked_off(b, True)

        # By default skips checked-off.
        n = clear_planned_store_ids_for_active_items()
        assert n == 1
        assert next(r for r in list_active_items() if r.id == a).planned_store_id is None
        # b was skipped because it's checked-off.
        with get_connection() as c:
            row = c.execute(
                "SELECT planned_store_id FROM shopping_list WHERE id = ?", (b,)
            ).fetchone()
        assert row[0] == store.id

    def test_clear_planned_store_ids_include_checked(self, isolated_db):
        store = create_store(name="A")
        a = add_item(display_name="a", planned_store_id=store.id)
        b = add_item(display_name="b", planned_store_id=store.id)
        set_checked_off(b, True)

        n = clear_planned_store_ids_for_active_items(include_checked_off=True)
        assert n == 2

    def test_bulk_set_planned_store_ids_returns_count(self, isolated_db):
        store = create_store(name="A")
        a = add_item(display_name="a")
        b = add_item(display_name="b")

        n = bulk_set_planned_store_ids([(a, store.id), (b, store.id)])
        assert n == 2

        names_by_store = [r.display_name for r in list_active_items(store_id=store.id)]
        assert set(names_by_store) == {"a", "b"}

    def test_bulk_empty_returns_zero(self, isolated_db):
        assert bulk_set_planned_store_ids([]) == 0

    def test_bulk_none_clears(self, isolated_db):
        store = create_store(name="A")
        a = add_item(display_name="a", planned_store_id=store.id)
        bulk_set_planned_store_ids([(a, None)])
        assert next(r for r in list_active_items() if r.id == a).planned_store_id is None


# ---------------------------------------------------------------------------
# bulk_set_planned_store_ids_by_item_id (keyed on canonical item_id)
# ---------------------------------------------------------------------------


class TestBulkSetByItemId:
    def test_updates_rows_matching_item_id(self, isolated_db):
        """
        Looks up shopping_list rows via their linked canonical items.id, not
        shopping_list.id. This is the API optimizer plans should target.
        """
        store = create_store(name="A")
        bread_item = create_item(canonical_name="bread")
        milk_item = create_item(canonical_name="milk")

        bread_row = add_item(display_name="bread", item_id=bread_item.id)
        milk_row = add_item(display_name="milk", item_id=milk_item.id)

        updated = bulk_set_planned_store_ids_by_item_id(
            [(bread_item.id, store.id), (milk_item.id, store.id)]
        )
        assert updated == 2

        by_name = {r.display_name: r for r in list_active_items()}
        assert by_name["bread"].planned_store_id == store.id
        assert by_name["milk"].planned_store_id == store.id

    def test_returns_zero_when_item_id_not_linked(self, isolated_db):
        """
        A canonical item_id with no matching shopping_list row → no rows
        updated. Does not raise.
        """
        store = create_store(name="A")
        orphan = create_item(canonical_name="orphan")
        add_item(display_name="something else")  # no item_id link

        updated = bulk_set_planned_store_ids_by_item_id([(orphan.id, store.id)])
        assert updated == 0

    def test_empty_assignments_returns_zero(self, isolated_db):
        assert bulk_set_planned_store_ids_by_item_id([]) == 0

    def test_none_store_id_clears_planned_store(self, isolated_db):
        store = create_store(name="A")
        item = create_item(canonical_name="bread")
        row = add_item(display_name="bread", item_id=item.id, planned_store_id=store.id)

        updated = bulk_set_planned_store_ids_by_item_id([(item.id, None)])
        assert updated == 1
        assert next(r for r in list_active_items() if r.id == row).planned_store_id is None

    def test_active_only_skips_soft_deleted(self, isolated_db):
        """
        Default `active_only=True` means soft-deleted rows are left alone —
        prevents clearing the list from mutating archived history.
        """
        store = create_store(name="A")
        item = create_item(canonical_name="bread")

        live = add_item(display_name="bread", item_id=item.id)
        archived = add_item(display_name="bread archived", item_id=item.id)
        delete_item(archived)  # soft delete

        updated = bulk_set_planned_store_ids_by_item_id([(item.id, store.id)])
        assert updated == 1  # only the live row

        # The archived row's planned_store_id was not changed.
        with get_connection() as c:
            row = c.execute(
                "SELECT planned_store_id FROM shopping_list WHERE id = ?", (archived,)
            ).fetchone()
        assert row[0] is None

    def test_active_only_false_updates_archived_rows_too(self, isolated_db):
        store = create_store(name="A")
        item = create_item(canonical_name="bread")

        live = add_item(display_name="bread", item_id=item.id)
        archived = add_item(display_name="bread archived", item_id=item.id)
        delete_item(archived)

        updated = bulk_set_planned_store_ids_by_item_id(
            [(item.id, store.id)], active_only=False
        )
        assert updated == 2
