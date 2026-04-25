"""
M2 — azure_docint_client dedupe layer

Covers the DB-backed dedupe tables and helpers:
  - _ensure_dedupe_tables is idempotent + creates both tables
  - file-hash round-trip (link + find)
  - signature round-trip (link + find)
  - INSERT OR REPLACE semantics on re-link
  - _delete_receipt_cascade removes rows from every child table
"""

from __future__ import annotations

import pytest

from Grocery_Sense.data.connection import get_connection
from Grocery_Sense.data.repositories.items_repo import create_item
from Grocery_Sense.data.repositories.prices_repo import add_price_point
from Grocery_Sense.data.repositories.stores_repo import create_store
from Grocery_Sense.integrations.azure_docint_client import (
    _delete_receipt_cascade,
    _ensure_dedupe_tables,
    _ensure_ingest_tables,
    _find_receipt_by_file_hash,
    _find_receipt_by_signature,
    _link_hash_to_receipt,
    _link_signature_to_receipt,
)


def _insert_receipt(store_id: int) -> int:
    with get_connection() as c:
        cur = c.execute(
            "INSERT INTO receipts (store_id, purchase_date, source) VALUES (?, ?, ?)",
            (store_id, "2026-04-22", "receipt"),
        )
        c.commit()
        return int(cur.lastrowid)


# ---------------------------------------------------------------------------
# _ensure_dedupe_tables
# ---------------------------------------------------------------------------


class TestEnsureDedupeTables:
    def test_creates_both_tables(self, isolated_db):
        _ensure_dedupe_tables()
        with get_connection() as c:
            rows = c.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        names = {r[0] for r in rows}
        assert "receipt_file_hashes" in names
        assert "receipt_signatures" in names

    def test_is_idempotent(self, isolated_db):
        _ensure_dedupe_tables()
        _ensure_dedupe_tables()  # must not raise


# ---------------------------------------------------------------------------
# File-hash round-trip
# ---------------------------------------------------------------------------


class TestFileHashDedupe:
    def test_find_returns_none_when_unseen(self, isolated_db):
        assert _find_receipt_by_file_hash("deadbeef") is None

    def test_link_then_find(self, isolated_db):
        store = create_store(name="Mart")
        rid = _insert_receipt(store.id)
        _link_hash_to_receipt("aaa111", rid, file_path="/tmp/r.jpg")
        assert _find_receipt_by_file_hash("aaa111") == rid

    def test_insert_or_replace_updates_receipt_binding(self, isolated_db):
        store = create_store(name="Mart")
        r1 = _insert_receipt(store.id)
        r2 = _insert_receipt(store.id)

        _link_hash_to_receipt("same-hash", r1, file_path="/tmp/a.jpg")
        _link_hash_to_receipt("same-hash", r2, file_path="/tmp/b.jpg")

        assert _find_receipt_by_file_hash("same-hash") == r2


# ---------------------------------------------------------------------------
# Signature round-trip
# ---------------------------------------------------------------------------


class TestSignatureDedupe:
    def test_find_returns_none_when_unseen(self, isolated_db):
        assert _find_receipt_by_signature("x|2026-04-22|25.00") is None

    def test_link_then_find(self, isolated_db):
        store = create_store(name="Mart")
        rid = _insert_receipt(store.id)
        _link_signature_to_receipt("test mart|2026-04-22|25.00", rid)
        assert _find_receipt_by_signature("test mart|2026-04-22|25.00") == rid

    def test_insert_or_replace_updates_binding(self, isolated_db):
        store = create_store(name="Mart")
        r1 = _insert_receipt(store.id)
        r2 = _insert_receipt(store.id)

        sig = "test mart|2026-04-22|25.00"
        _link_signature_to_receipt(sig, r1)
        _link_signature_to_receipt(sig, r2)

        assert _find_receipt_by_signature(sig) == r2


# ---------------------------------------------------------------------------
# _delete_receipt_cascade
# ---------------------------------------------------------------------------


class TestDeleteReceiptCascade:
    def test_clears_all_related_tables(self, isolated_db):
        _ensure_ingest_tables()
        _ensure_dedupe_tables()

        store = create_store(name="Mart")
        item = create_item(canonical_name="x")
        rid = _insert_receipt(store.id)

        # Related rows.
        add_price_point(
            item_id=item.id, store_id=store.id, unit_price=1.0, unit="each",
            source="receipt", receipt_id=rid, date="2026-04-22",
        )
        with get_connection() as c:
            c.execute(
                "INSERT INTO receipt_line_items (receipt_id, line_index, description) VALUES (?, ?, ?)",
                (rid, 0, "x"),
            )
            c.execute(
                "INSERT INTO receipt_raw_json (receipt_id, raw_json) VALUES (?, ?)",
                (rid, "{}"),
            )
            c.commit()

        _link_hash_to_receipt("abc", rid, "/tmp/r.jpg")
        _link_signature_to_receipt("sig|2026-04-22|1.0", rid)

        _delete_receipt_cascade(rid)

        with get_connection() as c:
            counts = tuple(c.execute(
                "SELECT "
                "(SELECT COUNT(*) FROM receipts WHERE id=?),"
                "(SELECT COUNT(*) FROM prices WHERE receipt_id=?),"
                "(SELECT COUNT(*) FROM receipt_line_items WHERE receipt_id=?),"
                "(SELECT COUNT(*) FROM receipt_raw_json WHERE receipt_id=?),"
                "(SELECT COUNT(*) FROM receipt_file_hashes WHERE receipt_id=?),"
                "(SELECT COUNT(*) FROM receipt_signatures WHERE receipt_id=?)",
                (rid, rid, rid, rid, rid, rid),
            ).fetchone())

        assert counts == (0, 0, 0, 0, 0, 0)

    def test_schema_fk_cascade_now_fires_alongside_manual_cascade(self, isolated_db):
        """
        FK pragma is now ON (post-fix). Schema-declared CASCADE on
        receipt_file_hashes.receipt_id fires automatically when the parent
        receipt is deleted. _delete_receipt_cascade's manual DELETEs are
        now redundant but harmless.
        """
        store = create_store(name="Mart")
        rid = _insert_receipt(store.id)
        _link_hash_to_receipt("abc", rid, "/tmp/r.jpg")

        # Deleting JUST the receipt row now cascades to the hash row.
        with get_connection() as c:
            c.execute("DELETE FROM receipts WHERE id = ?", (rid,))
            c.commit()
            remaining = c.execute(
                "SELECT COUNT(*) FROM receipt_file_hashes WHERE receipt_id = ?", (rid,)
            ).fetchone()
        assert remaining[0] == 0
