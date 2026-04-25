"""
M1 — items_repo

Folds the legacy tests/test_items_repo.py smoke into proper assertions.

Covers:
  - create_item / get_item_by_id / get_item_by_name
  - UNIQUE retry path returns existing row
  - list_items (tracked vs include_untracked)
  - list_all_item_names
  - set_item_tracked
  - get_items_by_ids (batch)
  - update_item_notes
  - Whitespace-only canonical name rejected
"""

from __future__ import annotations

import pytest

from Grocery_Sense.data.repositories.items_repo import (
    create_item,
    get_item_by_id,
    get_item_by_name,
    get_items_by_ids,
    list_all_item_names,
    list_items,
    set_item_tracked,
    update_item_notes,
)


# ---------------------------------------------------------------------------
# create_item
# ---------------------------------------------------------------------------


class TestCreateItem:
    def test_minimal(self, isolated_db):
        item = create_item(canonical_name="chicken thighs")
        assert item.id > 0
        assert item.canonical_name == "chicken thighs"
        assert item.is_tracked is True  # schema default=1

    def test_full_fields(self, isolated_db):
        item = create_item(
            canonical_name="milk 2L",
            category="dairy",
            default_unit="each",
            typical_package_size=2.0,
            typical_package_unit="L",
            is_tracked=False,
            notes="whole milk",
        )
        assert item.category == "dairy"
        assert item.default_unit == "each"
        assert item.typical_package_size == 2.0
        assert item.typical_package_unit == "L"
        assert item.is_tracked is False
        assert item.notes == "whole milk"

    def test_strips_whitespace(self, isolated_db):
        item = create_item(canonical_name="  eggs  ")
        assert item.canonical_name == "eggs"

    def test_empty_name_raises(self, isolated_db):
        with pytest.raises(ValueError):
            create_item(canonical_name="")

    def test_whitespace_only_name_raises(self, isolated_db):
        with pytest.raises(ValueError):
            create_item(canonical_name="   ")


# ---------------------------------------------------------------------------
# UNIQUE retry path
# ---------------------------------------------------------------------------


class TestUniqueRetry:
    def test_duplicate_canonical_returns_existing(self, isolated_db):
        first = create_item(canonical_name="bread")
        second = create_item(canonical_name="bread")
        assert first.id == second.id


# ---------------------------------------------------------------------------
# get_item_by_id / get_item_by_name
# ---------------------------------------------------------------------------


class TestGetters:
    def test_get_by_id(self, isolated_db):
        created = create_item(canonical_name="apples")
        found = get_item_by_id(created.id)
        assert found is not None
        assert found.canonical_name == "apples"

    def test_missing_id_returns_none(self, isolated_db):
        assert get_item_by_id(9999) is None

    def test_get_by_name_case_insensitive(self, isolated_db):
        created = create_item(canonical_name="Chicken Thighs")
        assert get_item_by_name("chicken thighs").id == created.id
        assert get_item_by_name("CHICKEN THIGHS").id == created.id
        assert get_item_by_name("  Chicken Thighs  ").id == created.id

    def test_get_by_name_empty_returns_none(self, isolated_db):
        assert get_item_by_name("") is None
        assert get_item_by_name("   ") is None


# ---------------------------------------------------------------------------
# list_items / list_all_item_names
# ---------------------------------------------------------------------------


class TestListItems:
    def test_tracked_only_by_default(self, isolated_db):
        create_item(canonical_name="a", is_tracked=True)
        create_item(canonical_name="b", is_tracked=False)
        names = [i.canonical_name for i in list_items()]
        assert names == ["a"]

    def test_include_untracked(self, isolated_db):
        create_item(canonical_name="a", is_tracked=True)
        create_item(canonical_name="b", is_tracked=False)
        names = sorted(i.canonical_name for i in list_items(include_untracked=True))
        assert names == ["a", "b"]

    def test_ordering_alphabetical(self, isolated_db):
        create_item(canonical_name="cherry")
        create_item(canonical_name="apple")
        create_item(canonical_name="banana")
        names = [i.canonical_name for i in list_items()]
        assert names == ["apple", "banana", "cherry"]


class TestListAllItemNames:
    def test_returns_id_name_tuples(self, isolated_db):
        a = create_item(canonical_name="a")
        b = create_item(canonical_name="b")
        pairs = list_all_item_names()
        assert (a.id, "a") in pairs
        assert (b.id, "b") in pairs

    def test_empty_when_no_items(self, isolated_db):
        assert list_all_item_names() == []


# ---------------------------------------------------------------------------
# set_item_tracked
# ---------------------------------------------------------------------------


class TestSetItemTracked:
    def test_turns_tracked_off(self, isolated_db):
        item = create_item(canonical_name="x", is_tracked=True)
        set_item_tracked(item.id, False)
        refreshed = get_item_by_id(item.id)
        assert refreshed.is_tracked is False

    def test_turns_tracked_on(self, isolated_db):
        item = create_item(canonical_name="x", is_tracked=False)
        set_item_tracked(item.id, True)
        refreshed = get_item_by_id(item.id)
        assert refreshed.is_tracked is True


# ---------------------------------------------------------------------------
# get_items_by_ids (batch)
# ---------------------------------------------------------------------------


class TestGetItemsByIds:
    def test_returns_map(self, isolated_db):
        a = create_item(canonical_name="a")
        b = create_item(canonical_name="b")
        c = create_item(canonical_name="c")
        m = get_items_by_ids([a.id, b.id, c.id])
        assert set(m.keys()) == {a.id, b.id, c.id}
        assert m[a.id].canonical_name == "a"

    def test_missing_ids_silently_omitted(self, isolated_db):
        a = create_item(canonical_name="a")
        m = get_items_by_ids([a.id, 9999])
        assert set(m.keys()) == {a.id}

    def test_empty_input_returns_empty(self, isolated_db):
        assert get_items_by_ids([]) == {}


# ---------------------------------------------------------------------------
# update_item_notes
# ---------------------------------------------------------------------------


class TestUpdateItemNotes:
    def test_sets_notes(self, isolated_db):
        item = create_item(canonical_name="x")
        update_item_notes(item.id, "new notes")
        assert get_item_by_id(item.id).notes == "new notes"

    def test_clears_notes_with_none(self, isolated_db):
        item = create_item(canonical_name="x", notes="had notes")
        update_item_notes(item.id, None)
        assert get_item_by_id(item.id).notes is None
