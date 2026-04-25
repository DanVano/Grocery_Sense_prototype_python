"""
M1 — items_admin_repo

Covers:
  - ensure_schema is idempotent + additive
  - search_items with and without query, stats (price_points, last_price_date)
  - get_item returns dict or None
  - toggle_tracked flips the bit; raises for missing item
  - set_default_unit (valid/invalid/empty)
  - rename_item (non-empty required)
  - merge_items: references move, default_unit promoted, source deleted,
    source name added as alias
"""

from __future__ import annotations

import pytest

from Grocery_Sense.data.connection import get_connection
from Grocery_Sense.data.repositories.items_admin_repo import ItemsAdminRepo
from Grocery_Sense.data.repositories.items_repo import create_item
from Grocery_Sense.data.repositories.prices_repo import add_price_point
from Grocery_Sense.data.repositories.stores_repo import create_store


@pytest.fixture
def admin(isolated_db) -> ItemsAdminRepo:
    repo = ItemsAdminRepo()
    repo.ensure_schema()
    return repo


# ---------------------------------------------------------------------------
# ensure_schema
# ---------------------------------------------------------------------------


class TestEnsureSchema:
    def test_adds_default_unit_column_if_missing(self, isolated_db):
        # schema.py already creates default_unit; running ensure_schema should be safe.
        repo = ItemsAdminRepo()
        repo.ensure_schema()
        repo.ensure_schema()  # idempotent
        with get_connection() as c:
            cols = {r[1] for r in c.execute("PRAGMA table_info(items)").fetchall()}
        assert {"default_unit", "is_tracked"} <= cols


# ---------------------------------------------------------------------------
# search_items
# ---------------------------------------------------------------------------


class TestSearchItems:
    def test_lists_all_when_query_empty(self, admin):
        create_item(canonical_name="apples")
        create_item(canonical_name="beef")
        rows = admin.search_items()
        assert {r.canonical_name for r in rows} == {"apples", "beef"}

    def test_like_filter(self, admin):
        create_item(canonical_name="chicken thighs")
        create_item(canonical_name="chicken breast")
        create_item(canonical_name="beef")
        rows = admin.search_items(query="chicken")
        assert {r.canonical_name for r in rows} == {"chicken thighs", "chicken breast"}

    def test_limit(self, admin):
        for name in ["a", "b", "c", "d"]:
            create_item(canonical_name=name)
        rows = admin.search_items(limit=2)
        assert len(rows) == 2

    def test_stats_include_price_points_and_last_date(self, admin):
        store = create_store(name="Mart")
        item = create_item(canonical_name="x")
        add_price_point(
            item_id=item.id, store_id=store.id, unit_price=1.0, unit="each",
            source="receipt", date="2026-04-22",
        )
        add_price_point(
            item_id=item.id, store_id=store.id, unit_price=2.0, unit="each",
            source="receipt", date="2026-04-20",
        )
        row = next(r for r in admin.search_items() if r.canonical_name == "x")
        assert row.price_points == 2
        assert row.last_price_date == "2026-04-22"

    def test_tracked_items_ranked_first(self, admin):
        create_item(canonical_name="b", is_tracked=False)
        create_item(canonical_name="a", is_tracked=True)
        names = [r.canonical_name for r in admin.search_items()]
        assert names.index("a") < names.index("b")


# ---------------------------------------------------------------------------
# get_item
# ---------------------------------------------------------------------------


class TestGetItem:
    def test_returns_dict(self, admin):
        item = create_item(canonical_name="rice", default_unit="kg", is_tracked=True)
        data = admin.get_item(item.id)
        assert data is not None
        assert data["canonical_name"] == "rice"
        assert data["default_unit"] == "kg"
        assert data["is_tracked"] == 1

    def test_missing_returns_none(self, admin):
        assert admin.get_item(9999) is None


# ---------------------------------------------------------------------------
# toggle_tracked
# ---------------------------------------------------------------------------


class TestToggleTracked:
    def test_flips_1_to_0(self, admin):
        item = create_item(canonical_name="x", is_tracked=True)
        new_val = admin.toggle_tracked(item.id)
        assert new_val == 0
        assert admin.get_item(item.id)["is_tracked"] == 0

    def test_flips_0_to_1(self, admin):
        item = create_item(canonical_name="x", is_tracked=False)
        new_val = admin.toggle_tracked(item.id)
        assert new_val == 1

    def test_missing_raises(self, admin):
        with pytest.raises(ValueError):
            admin.toggle_tracked(9999)


# ---------------------------------------------------------------------------
# set_default_unit
# ---------------------------------------------------------------------------


class TestSetDefaultUnit:
    def test_sets_valid_unit(self, admin):
        item = create_item(canonical_name="x")
        admin.set_default_unit(item.id, "kg")
        assert admin.get_item(item.id)["default_unit"] == "kg"

    def test_lowercases(self, admin):
        item = create_item(canonical_name="x")
        admin.set_default_unit(item.id, "KG")
        assert admin.get_item(item.id)["default_unit"] == "kg"

    def test_empty_clears(self, admin):
        item = create_item(canonical_name="x", default_unit="kg")
        admin.set_default_unit(item.id, "")
        assert admin.get_item(item.id)["default_unit"] is None

    def test_invalid_raises(self, admin):
        item = create_item(canonical_name="x")
        with pytest.raises(ValueError):
            admin.set_default_unit(item.id, "L")  # 'L' not in VALID_UNITS


# ---------------------------------------------------------------------------
# rename_item
# ---------------------------------------------------------------------------


class TestRenameItem:
    def test_updates_canonical_name(self, admin):
        item = create_item(canonical_name="old name")
        admin.rename_item(item.id, "new name")
        assert admin.get_item(item.id)["canonical_name"] == "new name"

    def test_strips_whitespace(self, admin):
        item = create_item(canonical_name="x")
        admin.rename_item(item.id, "   trimmed   ")
        assert admin.get_item(item.id)["canonical_name"] == "trimmed"

    def test_empty_raises(self, admin):
        item = create_item(canonical_name="x")
        with pytest.raises(ValueError):
            admin.rename_item(item.id, "")
        with pytest.raises(ValueError):
            admin.rename_item(item.id, "   ")


# ---------------------------------------------------------------------------
# merge_items
# ---------------------------------------------------------------------------


class TestMergeItems:
    def test_moves_prices_to_target(self, admin):
        store = create_store(name="Mart")
        src = create_item(canonical_name="chicken")
        tgt = create_item(canonical_name="chicken thighs")

        add_price_point(
            item_id=src.id, store_id=store.id, unit_price=5.0, unit="each",
            source="receipt", date="2026-04-22",
        )

        admin.merge_items(target_item_id=tgt.id, source_item_id=src.id)

        # Source gone, prices now point at target.
        assert admin.get_item(src.id) is None
        with get_connection() as c:
            rows = c.execute(
                "SELECT COUNT(*) FROM prices WHERE item_id = ?", (tgt.id,)
            ).fetchone()
        assert rows[0] == 1

    def test_promotes_tracked_from_source(self, admin):
        src = create_item(canonical_name="src", is_tracked=True)
        tgt = create_item(canonical_name="tgt", is_tracked=False)
        admin.merge_items(target_item_id=tgt.id, source_item_id=src.id)
        assert admin.get_item(tgt.id)["is_tracked"] == 1

    def test_promotes_default_unit_from_source_when_target_has_none(self, admin):
        src = create_item(canonical_name="src", default_unit="kg")
        tgt = create_item(canonical_name="tgt")  # no default_unit
        admin.merge_items(target_item_id=tgt.id, source_item_id=src.id)
        assert admin.get_item(tgt.id)["default_unit"] == "kg"

    def test_keeps_target_default_unit_when_both_set(self, admin):
        src = create_item(canonical_name="src", default_unit="g")
        tgt = create_item(canonical_name="tgt", default_unit="kg")
        admin.merge_items(target_item_id=tgt.id, source_item_id=src.id)
        assert admin.get_item(tgt.id)["default_unit"] == "kg"

    def test_adds_source_name_as_alias(self, admin):
        src = create_item(canonical_name="chx")
        tgt = create_item(canonical_name="chicken")
        admin.merge_items(
            target_item_id=tgt.id, source_item_id=src.id, keep_source_as_alias=True
        )
        with get_connection() as c:
            rows = c.execute(
                "SELECT alias_text, item_id FROM item_aliases WHERE alias_text='chx'"
            ).fetchone()
        assert rows is not None
        assert rows[0] == "chx"
        assert rows[1] == tgt.id

    def test_does_not_add_alias_when_disabled(self, admin):
        src = create_item(canonical_name="chx")
        tgt = create_item(canonical_name="chicken")
        admin.merge_items(
            target_item_id=tgt.id, source_item_id=src.id, keep_source_as_alias=False
        )
        with get_connection() as c:
            row = c.execute(
                "SELECT 1 FROM item_aliases WHERE alias_text='chx'"
            ).fetchone()
        assert row is None

    def test_same_target_and_source_raises(self, admin):
        item = create_item(canonical_name="x")
        with pytest.raises(ValueError):
            admin.merge_items(target_item_id=item.id, source_item_id=item.id)

    def test_missing_target_or_source_raises(self, admin):
        src = create_item(canonical_name="x")
        with pytest.raises(ValueError):
            admin.merge_items(target_item_id=9999, source_item_id=src.id)
        with pytest.raises(ValueError):
            admin.merge_items(target_item_id=src.id, source_item_id=9999)
