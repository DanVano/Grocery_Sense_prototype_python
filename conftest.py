import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

import pytest


@pytest.fixture(autouse=True)
def isolated_db(tmp_path):
    """Redirect all SQLite connections to a per-test temp database."""
    import Grocery_Sense.data.connection as _conn_mod
    from Grocery_Sense.data.schema import create_tables, _migrate

    db_path = str(tmp_path / "test.db")
    _conn_mod._TEST_DB_PATH = db_path
    _conn_mod._integrity_checked.clear()

    conn = _conn_mod.get_connection()
    create_tables(conn)
    _migrate(conn)
    conn.close()

    yield

    _conn_mod._TEST_DB_PATH = None
    _conn_mod._integrity_checked.clear()
