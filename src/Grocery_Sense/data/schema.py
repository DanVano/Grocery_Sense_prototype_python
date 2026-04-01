"""
pricebrain.data.schema

SQLite schema definition and initialization for the Price app backend.
"""

from typing import Optional
from pathlib import Path
import sqlite3


def create_tables(conn: sqlite3.Connection) -> None:
    """
    Create all tables if they do not exist.

    Run this once at startup (safe to call multiple times).
    """
    cur = conn.cursor()

    # --- stores ---
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS stores (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            name            TEXT NOT NULL,
            address         TEXT,
            city            TEXT,
            postal_code     TEXT,
            flipp_store_id  TEXT,
            is_favorite     INTEGER NOT NULL DEFAULT 0,
            priority        INTEGER NOT NULL DEFAULT 0,
            notes           TEXT,
            created_at      TEXT NOT NULL DEFAULT (datetime('now'))
        );
        """
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_stores_name ON stores(name);"
    )

    # --- items ---
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS items (
            id                   INTEGER PRIMARY KEY AUTOINCREMENT,
            canonical_name       TEXT NOT NULL UNIQUE,
            category             TEXT,
            default_unit         TEXT,
            typical_package_size REAL,
            typical_package_unit TEXT,
            is_tracked           INTEGER NOT NULL DEFAULT 1,
            notes                TEXT,
            created_at           TEXT NOT NULL DEFAULT (datetime('now'))
        );
        """
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_items_name ON items(canonical_name);"
    )

    # --- receipts ---
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS receipts (
            id                      INTEGER PRIMARY KEY AUTOINCREMENT,
            store_id                INTEGER NOT NULL,
            purchase_date           TEXT NOT NULL,           -- 'YYYY-MM-DD'
            subtotal_amount         REAL,                    -- pre-tax
            tax_amount              REAL,
            total_amount            REAL,                    -- subtotal + tax
            source                  TEXT NOT NULL,           -- 'receipt' | 'manual'
            file_path               TEXT,                    -- temp path to image/pdf
            image_overall_confidence INTEGER,                -- 1-5
            keep_image_until        TEXT,                    -- date to keep image until
            azure_request_id        TEXT,
            created_at              TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (store_id) REFERENCES stores(id) ON DELETE CASCADE
        );
        """
    )
    cur.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_receipts_store_date
        ON receipts(store_id, purchase_date);
        """
    )

    # --- flyer_sources ---
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS flyer_sources (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            provider    TEXT NOT NULL,           -- e.g. 'flipp'
            external_id TEXT,                    -- flipp flyer id
            store_id    INTEGER NOT NULL,        -- link to stores
            valid_from  TEXT NOT NULL,           -- 'YYYY-MM-DD'
            valid_to    TEXT NOT NULL,
            created_at  TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (store_id) REFERENCES stores(id) ON DELETE CASCADE
        );
        """
    )
    cur.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_flyer_validity
        ON flyer_sources(store_id, valid_from, valid_to);
        """
    )

    # --- prices ---
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS prices (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            item_id          INTEGER NOT NULL,
            store_id         INTEGER NOT NULL,
            receipt_id       INTEGER,           -- nullable if source != 'receipt'
            flyer_source_id  INTEGER,           -- nullable if source != 'flyer'
            source           TEXT NOT NULL,     -- 'receipt' | 'flyer' | 'manual'
            date             TEXT NOT NULL,     -- 'YYYY-MM-DD'
            unit_price       REAL NOT NULL,     -- pre-tax, normalized (e.g. per kg)
            unit             TEXT NOT NULL,     -- 'kg', 'lb', 'each', etc.
            quantity         REAL,              -- actual quantity (e.g. 1.25 kg)
            total_price      REAL,              -- line total pre-tax
            raw_name         TEXT,              -- original text from receipt/flyer
            confidence       INTEGER,           -- 1-5 mapping confidence
            created_at       TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (item_id)         REFERENCES items(id)         ON DELETE CASCADE,
            FOREIGN KEY (store_id)        REFERENCES stores(id)        ON DELETE CASCADE,
            FOREIGN KEY (receipt_id)      REFERENCES receipts(id)      ON DELETE CASCADE,
            FOREIGN KEY (flyer_source_id) REFERENCES flyer_sources(id) ON DELETE CASCADE
        );
        """
    )
    cur.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_prices_item_date
        ON prices(item_id, date);
        """
    )
    cur.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_prices_item_store_date
        ON prices(item_id, store_id, date);
        """
    )
    cur.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_prices_flyer_source_id
        ON prices(flyer_source_id);
        """
    )
    cur.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_prices_source_date
        ON prices(source, date);
        """
    )

    # --- receipt_line_items ---
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS receipt_line_items (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            receipt_id   INTEGER NOT NULL,
            line_index   INTEGER NOT NULL,
            item_id      INTEGER,
            description  TEXT,
            quantity     REAL,
            unit_price   REAL,
            line_total   REAL,
            discount     REAL,
            confidence   INTEGER,
            created_at   TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (receipt_id) REFERENCES receipts(id) ON DELETE CASCADE,
            FOREIGN KEY (item_id)    REFERENCES items(id)    ON DELETE SET NULL
        );
        """
    )
    cur.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_receipt_line_items_receipt_id
        ON receipt_line_items(receipt_id);
        """
    )

    # --- Fuzzy matching ---
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS item_aliases (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            alias_text TEXT NOT NULL UNIQUE,
            item_id INTEGER NOT NULL,
            confidence REAL NOT NULL DEFAULT 1.0,
            source TEXT NOT NULL DEFAULT 'manual',
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            last_seen_at TEXT,
            times_seen INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY(item_id) REFERENCES items(id)
        );
        """
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_item_aliases_item_id ON item_aliases(item_id);"
    )

    # --- shopping_list ---
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS shopping_list (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            item_id          INTEGER,           -- nullable, we may not know canonical item yet
            display_name     TEXT NOT NULL,     -- what user typed/spoke
            quantity         REAL,
            unit             TEXT,
            planned_store_id INTEGER,           -- nullable
            added_by         TEXT,
            added_at         TEXT NOT NULL DEFAULT (datetime('now')),
            is_checked_off   INTEGER NOT NULL DEFAULT 0,  -- 0/1
            is_active        INTEGER NOT NULL DEFAULT 1,  -- 0/1
            notes            TEXT,
            FOREIGN KEY (item_id)          REFERENCES items(id)   ON DELETE SET NULL,
            FOREIGN KEY (planned_store_id) REFERENCES stores(id)  ON DELETE SET NULL
        );
        """

    )
    cur.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_shopping_list_active
        ON shopping_list(is_active, planned_store_id);
        """
    )

    # --- user_profile ---
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS user_profile (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            household_name      TEXT,
            postal_code         TEXT,
            currency            TEXT DEFAULT 'CAD',
            preferred_store_ids TEXT,           -- e.g. '1,2,5' or JSON string
            eats_chicken        INTEGER NOT NULL DEFAULT 1,
            eats_beef           INTEGER NOT NULL DEFAULT 1,
            eats_pork           INTEGER NOT NULL DEFAULT 1,
            eats_fish           INTEGER NOT NULL DEFAULT 1,
            is_vegetarian       INTEGER NOT NULL DEFAULT 0,
            is_gluten_free      INTEGER NOT NULL DEFAULT 0,
            has_nut_allergy     INTEGER NOT NULL DEFAULT 0,
            created_at          TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at          TEXT
        );
        """
    )

    # --- sync_meta ---
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS sync_meta (
            id                         INTEGER PRIMARY KEY AUTOINCREMENT,
            device_role                TEXT,     -- 'primary' | 'secondary'
            instance_id                TEXT,     -- random UUID per installation
            last_sync_from_primary_at  TEXT,
            last_sync_to_primary_at    TEXT,
            created_at                 TEXT NOT NULL DEFAULT (datetime('now'))
        );
        """
    )

    conn.commit()


def initialize_database(base_dir: Optional[Path] = None) -> None:
    """
    Convenience helper: open a connection, create tables, close it.

    Call this once at app startup in your Tkinter / CLI entrypoint.
    """
    from .connection import get_connection  # local import to avoid cycles

    conn = get_connection(base_dir)
    try:
        create_tables(conn)
    finally:
        conn.close()
