"""
M1 — Foreign-key behaviour

FIX APPLIED: connection.get_connection now runs `PRAGMA foreign_keys = ON`
on every connection. All ON DELETE CASCADE / SET NULL clauses in schema.py
are now enforced.

Also covered:
  - items.canonical_name UNIQUE is case-sensitive whitespace exact
  - stores.flipp_store_id has NO UNIQUE constraint (double-count risk)
  - item_aliases has no ON DELETE clause — deleting an item now raises
    IntegrityError rather than cascading.
"""

from __future__ import annotations

import sqlite3

import pytest

from Grocery_Sense.data import connection as _conn
from Grocery_Sense.data.repositories.items_repo import create_item
from Grocery_Sense.data.repositories.stores_repo import create_store


# ---------------------------------------------------------------------------
# FK pragma is ON by default (post-fix)
# ---------------------------------------------------------------------------


class TestForeignKeysPragmaDefault:
    def test_default_connection_has_fks_on(self, isolated_db):
        with _conn.get_connection() as c:
            fk = c.execute("PRAGMA foreign_keys").fetchone()[0]
        assert fk == 1


# ---------------------------------------------------------------------------
# Cascade: stores → receipts/prices
# ---------------------------------------------------------------------------


class TestCascadeOnStoreDelete:
    def test_cascade_fires_on_default_connection(self, isolated_db):
        """
        Schema declares receipts(store_id) ON DELETE CASCADE.
        With FKs enforced, deleting a store removes its receipts.
        """
        store = create_store(name="Orphan Mart")
        with _conn.get_connection() as c:
            c.execute(
                "INSERT INTO receipts (store_id, purchase_date, source) VALUES (?, ?, ?)",
                (store.id, "2026-04-22", "receipt"),
            )
            c.commit()

            c.execute("DELETE FROM stores WHERE id = ?", (store.id,))
            c.commit()

            orphans = c.execute(
                "SELECT COUNT(*) FROM receipts WHERE store_id = ?", (store.id,)
            ).fetchone()[0]
        assert orphans == 0


# ---------------------------------------------------------------------------
# SET NULL: shopping_list.planned_store_id on store delete
# ---------------------------------------------------------------------------


class TestSetNullOnStoreDelete:
    def test_set_null_fires_on_default_connection(self, isolated_db):
        """
        shopping_list.planned_store_id FK is ON DELETE SET NULL.
        With FKs enforced, deleting the store nulls the reference.
        """
        store = create_store(name="Ghost")
        with _conn.get_connection() as c:
            c.execute(
                "INSERT INTO shopping_list (display_name, planned_store_id) "
                "VALUES (?, ?)",
                ("bread", store.id),
            )
            c.commit()

            c.execute("DELETE FROM stores WHERE id = ?", (store.id,))
            c.commit()

            stale = c.execute(
                "SELECT planned_store_id FROM shopping_list WHERE display_name = 'bread'"
            ).fetchone()[0]
        assert stale is None


# ---------------------------------------------------------------------------
# item_aliases: no ON DELETE clause → FK violation on parent delete
# ---------------------------------------------------------------------------


class TestItemAliasesHasNoCascade:
    def test_delete_item_with_alias_blocked_by_fk(self, isolated_db):
        """
        FINDING still present: item_aliases.item_id only has
        FOREIGN KEY(item_id) REFERENCES items(id) — no ON DELETE clause.
        With FKs now enforced, deleting an item that has aliases raises
        IntegrityError. Callers must delete aliases first, or the schema
        should add ON DELETE CASCADE to item_aliases.
        """
        item = create_item(canonical_name="doomed")
        with _conn.get_connection() as c:
            c.execute(
                "INSERT INTO item_aliases (alias_text, item_id, confidence, source) "
                "VALUES (?, ?, 1.0, 'manual')",
                ("doomed-alias", item.id),
            )
            c.commit()
            with pytest.raises(sqlite3.IntegrityError):
                c.execute("DELETE FROM items WHERE id = ?", (item.id,))
                c.commit()

    def test_item_with_no_aliases_can_be_deleted(self, isolated_db):
        item = create_item(canonical_name="lonely")
        with _conn.get_connection() as c:
            c.execute("DELETE FROM items WHERE id = ?", (item.id,))
            c.commit()
        from Grocery_Sense.data.repositories.items_repo import get_item_by_id
        assert get_item_by_id(item.id) is None


# ---------------------------------------------------------------------------
# items.canonical_name UNIQUE — whitespace trap (cross-cutting with M4)
# ---------------------------------------------------------------------------


class TestCanonicalNameUnique:
    def test_case_variants_create_separate_rows(self, isolated_db):
        """
        FINDING (open): items.canonical_name UNIQUE is case-sensitive at the
        SQL level (default TEXT collation is BINARY), so 'milk' and 'MILK'
        both land as separate rows.
        """
        a = create_item(canonical_name="milk")
        b = create_item(canonical_name="MILK")
        assert a.id != b.id

        from Grocery_Sense.data.repositories.items_repo import get_item_by_name
        found = get_item_by_name("milk")
        assert found is not None
        assert found.id in {a.id, b.id}

    def test_internal_whitespace_is_not_deduped(self, isolated_db):
        """
        FINDING (open): UNIQUE constraint is literal-string.
        'Milk 2L' and 'Milk 2 L' both slip through.
        """
        a = create_item(canonical_name="Milk 2L")
        b = create_item(canonical_name="Milk 2 L")
        assert a.id != b.id

    def test_raw_duplicate_insert_raises(self, isolated_db):
        create_item(canonical_name="water")
        with _conn.get_connection() as c:
            with pytest.raises(sqlite3.IntegrityError):
                c.execute(
                    "INSERT INTO items (canonical_name) VALUES (?)", ("water",)
                )


# ---------------------------------------------------------------------------
# stores.flipp_store_id — NO unique constraint
# ---------------------------------------------------------------------------


class TestFlippStoreIdNotUnique:
    def test_duplicate_flipp_store_id_allowed(self, isolated_db):
        """
        FINDING (open): schema.py declares flipp_store_id TEXT with no UNIQUE.
        Two stores can share the same external id, which would double-count
        prices in the basket optimizer and planner.
        """
        create_store(name="A", flipp_store_id="DUP_1")
        create_store(name="B", flipp_store_id="DUP_1")

        with _conn.get_connection() as c:
            rows = c.execute(
                "SELECT COUNT(*) FROM stores WHERE flipp_store_id = 'DUP_1'"
            ).fetchone()
        assert rows[0] == 2

    def test_upsert_store_from_flipp_updates_first_match(self, isolated_db):
        from Grocery_Sense.data.repositories.stores_repo import upsert_store_from_flipp

        create_store(name="A old", flipp_store_id="DUP_2")
        create_store(name="B old", flipp_store_id="DUP_2")

        updated = upsert_store_from_flipp(
            name="renamed", flipp_store_id="DUP_2"
        )

        with _conn.get_connection() as c:
            names = [
                r[0] for r in c.execute(
                    "SELECT name FROM stores WHERE flipp_store_id = 'DUP_2' ORDER BY id"
                ).fetchall()
            ]
        assert "renamed" in names
        assert len(names) == 2
        assert updated.name == "renamed"
