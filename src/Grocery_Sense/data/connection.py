"""
pricebrain.data.connection

SQLite connection utilities for the Price app backend.
Stores the database inside src/pricebrain/data/db/
"""

# src/grocery_sense/data/connection.py

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Optional

# Name of the SQLite file
DB_FILENAME = "grocery_sense.db"


def get_db_path(base_dir: Optional[Path] = None) -> Path:
    """
    Return the full path to the DB file.

    If base_dir is None, we put the DB inside the 'db' directory next to this file:
        src/grocery_sense/data/db/Grocery_Sense.db
    """
    if base_dir is None:
        base_dir = Path(__file__).resolve().parent / "db"
    else:
        base_dir = Path(base_dir)

    base_dir.mkdir(parents=True, exist_ok=True)
    return base_dir / DB_FILENAME


def get_connection(base_dir: Optional[Path] = None) -> sqlite3.Connection:
    """
    Open a SQLite connection to our DB.

    base_dir is optional; if not provided, we use the default 'db' directory.
    """
    db_path = get_db_path(base_dir)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row  # nicer dict-like access
    return conn

