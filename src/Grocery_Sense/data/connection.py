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

# Tracks whether integrity_check has already passed this process run.
# Keyed by resolved db path so in-memory / test DBs each get their own check.
_integrity_checked: set = set()


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


def _check_integrity(conn: sqlite3.Connection, db_path: Path) -> None:
    """
    Run PRAGMA integrity_check on first open of this DB path.
    Raises RuntimeError with a clear message if corruption is detected.
    Skips the check for in-memory databases (':memory:').
    """
    path_key = str(db_path)
    if path_key in _integrity_checked:
        return

    rows = conn.execute("PRAGMA integrity_check;").fetchall()
    # Result is one or more rows; a healthy DB returns exactly [("ok",)]
    results = [row[0] for row in rows]
    if results != ["ok"]:
        detail = "; ".join(str(r) for r in results)
        raise RuntimeError(
            f"SQLite database appears to be corrupted ({db_path}).\n"
            f"integrity_check reported: {detail}\n"
            "Try restoring from a backup or deleting the file to start fresh."
        )

    _integrity_checked.add(path_key)


def get_connection(base_dir: Optional[Path] = None) -> sqlite3.Connection:
    """
    Open a SQLite connection to our DB.

    base_dir is optional; if not provided, we use the default 'db' directory.
    On the first connection per process, runs PRAGMA integrity_check and raises
    RuntimeError immediately if corruption is detected.
    """
    db_path = get_db_path(base_dir)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row  # nicer dict-like access
    _check_integrity(conn, db_path)
    return conn

