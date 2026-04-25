"""
M1 — receipts_repo

Covers:
  - ensure_receipt_support_tables creates the auxiliary tables
  - list_recent_receipts joins store name + counts line items
  - get_receipt / list_receipt_line_items / get_receipt_raw_json
  - delete_receipt_cascade manually cascades to all related tables
    (workaround for FK pragma being off — see test_fk_behavior.py)
  - delete_receipt_with_backup snapshots and deletes
  - restore_receipt_from_backup reinserts under a new id and relinks children
  - list_deleted_backups
"""

from __future__ import annotations

from datetime import date

import pytest

from Grocery_Sense.data.connection import get_connection
from Grocery_Sense.data.repositories.items_repo import create_item
from Grocery_Sense.data.repositories.receipts_repo import (
    delete_receipt_cascade,
    delete_receipt_with_backup,
    ensure_receipt_support_tables,
    get_receipt,
    get_receipt_raw_json,
    list_deleted_backups,
    list_receipt_line_items,
    list_recent_receipts,
    restore_receipt_from_backup,
)
from Grocery_Sense.data.repositories.stores_repo import create_store


def _insert_receipt(store_id: int, *, purchase_date="2026-04-22",
                    total_amount=25.00, file_path="/tmp/r.pdf") -> int:
    """Insert a basic receipt row and return its id."""
    with get_connection() as c:
        cur = c.execute(
            """
            INSERT INTO receipts
                (store_id, purchase_date, subtotal_amount, tax_amount, total_amount,
                 source, file_path, image_overall_confidence, azure_request_id)
            VALUES (?, ?, ?, ?, ?, 'receipt', ?, 4, 'req-1')
            """,
            (store_id, purchase_date, 22.00, 3.00, total_amount, file_path),
        )
        rid = int(cur.lastrowid)
        c.commit()
    return rid


def _insert_line_item(receipt_id: int, *, line_index: int,
                      item_id: int | None = None, description: str = "widget",
                      quantity: float = 1.0, unit_price: float = 1.0,
                      line_total: float = 1.0) -> int:
    with get_connection() as c:
        cur = c.execute(
            """
            INSERT INTO receipt_line_items
                (receipt_id, line_index, item_id, description, quantity, unit_price, line_total)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (receipt_id, line_index, item_id, description, quantity, unit_price, line_total),
        )
        lid = int(cur.lastrowid)
        c.commit()
    return lid


def _insert_raw_json(receipt_id: int, raw: str = '{"x":1}') -> None:
    ensure_receipt_support_tables()
    with get_connection() as c:
        c.execute(
            """
            INSERT INTO receipt_raw_json (receipt_id, operation_id, json_path, raw_json)
            VALUES (?, 'op-1', '/tmp/a.json', ?)
            """,
            (receipt_id, raw),
        )
        c.commit()


# ---------------------------------------------------------------------------
# ensure_receipt_support_tables
# ---------------------------------------------------------------------------


class TestEnsureSupportTables:
    def test_creates_auxiliary_tables(self, isolated_db):
        ensure_receipt_support_tables()
        with get_connection() as c:
            rows = c.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            ).fetchall()
        names = {r[0] for r in rows}
        assert {
            "deleted_receipt_backups",
            "receipt_raw_json",
            "receipt_file_hashes",
            "receipt_signatures",
        } <= names

    def test_is_idempotent(self, isolated_db):
        ensure_receipt_support_tables()
        ensure_receipt_support_tables()  # safe to call twice


# ---------------------------------------------------------------------------
# list_recent_receipts
# ---------------------------------------------------------------------------


class TestListRecentReceipts:
    def test_returns_ordered_by_id_desc_with_store_name(self, isolated_db):
        a = create_store(name="A")
        r1 = _insert_receipt(a.id, purchase_date="2026-04-20")
        r2 = _insert_receipt(a.id, purchase_date="2026-04-22")

        rows = list_recent_receipts()
        assert [r["id"] for r in rows] == [r2, r1]
        assert all(r["store_name"] == "A" for r in rows)

    def test_includes_line_item_count(self, isolated_db):
        a = create_store(name="A")
        rid = _insert_receipt(a.id)
        _insert_line_item(rid, line_index=0)
        _insert_line_item(rid, line_index=1)

        rows = list_recent_receipts()
        assert rows[0]["item_count"] == 2

    def test_limit_and_offset(self, isolated_db):
        a = create_store(name="A")
        ids = [_insert_receipt(a.id) for _ in range(5)]

        page_1 = list_recent_receipts(limit=2, offset=0)
        page_2 = list_recent_receipts(limit=2, offset=2)
        assert [r["id"] for r in page_1] == [ids[4], ids[3]]
        assert [r["id"] for r in page_2] == [ids[2], ids[1]]


# ---------------------------------------------------------------------------
# get_receipt / line items / raw json
# ---------------------------------------------------------------------------


class TestGetReceipt:
    def test_returns_dict(self, isolated_db):
        a = create_store(name="A")
        rid = _insert_receipt(a.id, total_amount=42.0)
        data = get_receipt(rid)
        assert data is not None
        assert data["id"] == rid
        assert data["store_name"] == "A"
        assert data["total_amount"] == 42.0

    def test_missing_returns_none(self, isolated_db):
        assert get_receipt(9999) is None


class TestListReceiptLineItems:
    def test_joins_canonical_name(self, isolated_db):
        a = create_store(name="A")
        rid = _insert_receipt(a.id)
        item = create_item(canonical_name="eggs")
        _insert_line_item(rid, line_index=0, item_id=item.id, description="EGGS DOZEN")
        _insert_line_item(rid, line_index=1, description="unmapped")

        rows = list_receipt_line_items(rid)
        assert len(rows) == 2
        assert rows[0]["canonical_name"] == "eggs"
        assert rows[0]["description"] == "EGGS DOZEN"
        assert rows[1]["canonical_name"] == ""  # no item_id → empty string

    def test_ordered_by_line_index(self, isolated_db):
        a = create_store(name="A")
        rid = _insert_receipt(a.id)
        _insert_line_item(rid, line_index=2)
        _insert_line_item(rid, line_index=0)
        _insert_line_item(rid, line_index=1)

        rows = list_receipt_line_items(rid)
        assert [r["line_index"] for r in rows] == [0, 1, 2]


class TestGetReceiptRawJson:
    def test_returns_raw_and_path(self, isolated_db):
        a = create_store(name="A")
        rid = _insert_receipt(a.id)
        _insert_raw_json(rid, raw='{"hello":"world"}')

        raw, path = get_receipt_raw_json(rid)
        assert raw == '{"hello":"world"}'
        assert path == "/tmp/a.json"

    def test_missing_returns_none_tuple(self, isolated_db):
        raw, path = get_receipt_raw_json(9999)
        assert raw is None
        assert path is None


# ---------------------------------------------------------------------------
# delete_receipt_cascade
# ---------------------------------------------------------------------------


class TestDeleteReceiptCascade:
    def test_removes_receipt_and_children(self, isolated_db):
        """
        Schema's FK CASCADEs are inert (FK pragma off). delete_receipt_cascade
        must manually clear every related table. Locks in the manual cascade.
        """
        a = create_store(name="A")
        rid = _insert_receipt(a.id)
        item = create_item(canonical_name="x")
        _insert_line_item(rid, line_index=0, item_id=item.id)
        _insert_raw_json(rid)

        # Add a price row linked to this receipt.
        from Grocery_Sense.data.repositories.prices_repo import add_price_point
        add_price_point(
            item_id=item.id, store_id=a.id, unit_price=1.0, unit="each",
            source="receipt", receipt_id=rid, date="2026-04-22",
        )

        delete_receipt_cascade(rid)

        with get_connection() as c:
            counts = tuple(c.execute(
                "SELECT "
                "(SELECT COUNT(*) FROM receipts WHERE id=?),"
                "(SELECT COUNT(*) FROM receipt_line_items WHERE receipt_id=?),"
                "(SELECT COUNT(*) FROM receipt_raw_json WHERE receipt_id=?),"
                "(SELECT COUNT(*) FROM prices WHERE receipt_id=?)",
                (rid, rid, rid, rid),
            ).fetchone())
        assert counts == (0, 0, 0, 0)


# ---------------------------------------------------------------------------
# delete_receipt_with_backup + restore
# ---------------------------------------------------------------------------


class TestDeleteWithBackupAndRestore:
    def test_delete_captures_snapshot(self, isolated_db):
        a = create_store(name="A")
        rid = _insert_receipt(a.id, total_amount=9.99)
        item = create_item(canonical_name="x")
        _insert_line_item(rid, line_index=0, item_id=item.id, description="X")
        _insert_raw_json(rid)

        backup_id = delete_receipt_with_backup(rid)
        assert backup_id > 0

        # Receipt gone; backup exists.
        assert get_receipt(rid) is None

        backups = list_deleted_backups()
        assert any(b["backup_id"] == backup_id for b in backups)
        assert any(b["original_receipt_id"] == rid for b in backups)

    def test_restore_produces_new_id_with_relinked_children(self, isolated_db):
        a = create_store(name="A")
        rid = _insert_receipt(a.id, total_amount=9.99)
        item = create_item(canonical_name="x")
        _insert_line_item(rid, line_index=0, item_id=item.id, description="X")
        _insert_line_item(rid, line_index=1, description="Y")
        _insert_raw_json(rid, raw='{"saved":1}')

        from Grocery_Sense.data.repositories.prices_repo import add_price_point
        add_price_point(
            item_id=item.id, store_id=a.id, unit_price=1.0, unit="each",
            source="receipt", receipt_id=rid, date="2026-04-22",
        )

        backup_id = delete_receipt_with_backup(rid)
        new_id = restore_receipt_from_backup(backup_id)

        assert new_id != rid

        restored = get_receipt(new_id)
        assert restored is not None
        assert restored["total_amount"] == 9.99

        lines = list_receipt_line_items(new_id)
        assert [l["line_index"] for l in lines] == [0, 1]

        raw, _ = get_receipt_raw_json(new_id)
        assert raw == '{"saved":1}'

        # prices relinked to the new receipt id.
        with get_connection() as c:
            n = c.execute(
                "SELECT COUNT(*) FROM prices WHERE receipt_id = ?", (new_id,)
            ).fetchone()[0]
        assert n == 1

    def test_delete_missing_raises(self, isolated_db):
        with pytest.raises(ValueError):
            delete_receipt_with_backup(9999)

    def test_restore_missing_backup_raises(self, isolated_db):
        with pytest.raises(ValueError):
            restore_receipt_from_backup(9999)


# ---------------------------------------------------------------------------
# list_deleted_backups
# ---------------------------------------------------------------------------


class TestListDeletedBackups:
    def test_returns_recent_first(self, isolated_db):
        a = create_store(name="A")
        r1 = _insert_receipt(a.id)
        r2 = _insert_receipt(a.id)

        b1 = delete_receipt_with_backup(r1)
        b2 = delete_receipt_with_backup(r2)

        backups = list_deleted_backups()
        assert [b["backup_id"] for b in backups] == [b2, b1]

    def test_limit(self, isolated_db):
        a = create_store(name="A")
        ids = [_insert_receipt(a.id) for _ in range(3)]
        for r in ids:
            delete_receipt_with_backup(r)

        backups = list_deleted_backups(limit=1)
        assert len(backups) == 1
