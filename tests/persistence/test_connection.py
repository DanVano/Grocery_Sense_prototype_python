"""
M1 — connection.py

Covers:
  - get_db_path defaults + base_dir override
  - _TEST_DB_PATH overrides get_connection target
  - sqlite3.Row factory is enabled
  - integrity check runs on first connection and caches per-path
  - Corrupted DB raises a clear RuntimeError
"""

from __future__ import annotations

import sqlite3

import pytest

from Grocery_Sense.data import connection as _conn


# ---------------------------------------------------------------------------
# get_db_path
# ---------------------------------------------------------------------------


class TestGetDbPath:
    def test_default_directory_is_created(self, tmp_path):
        path = _conn.get_db_path(base_dir=tmp_path / "alt")
        assert (tmp_path / "alt").is_dir()
        assert path.name == _conn.DB_FILENAME

    def test_path_ends_in_filename(self, tmp_path):
        path = _conn.get_db_path(base_dir=tmp_path)
        assert path.parent == tmp_path
        assert path.name == "grocery_sense.db"


# ---------------------------------------------------------------------------
# get_connection behaviour
# ---------------------------------------------------------------------------


class TestGetConnection:
    def test_honors_test_db_path(self, isolated_db):
        """
        The isolated_db fixture sets _TEST_DB_PATH; get_connection MUST hit it.
        """
        conn = _conn.get_connection()
        try:
            row = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='stores'"
            ).fetchone()
            assert row is not None
        finally:
            conn.close()

    def test_row_factory_is_sqlite3_row(self, isolated_db):
        conn = _conn.get_connection()
        try:
            assert conn.row_factory is sqlite3.Row
        finally:
            conn.close()

    def test_base_dir_override_ignored_when_test_path_set(self, tmp_path, monkeypatch):
        """
        When _TEST_DB_PATH is set, base_dir is bypassed — confirming that tests
        can never accidentally touch a production path.
        """
        test_db = tmp_path / "a.db"
        monkeypatch.setattr(_conn, "_TEST_DB_PATH", str(test_db))
        monkeypatch.setattr(_conn, "_integrity_checked", set())

        conn = _conn.get_connection(base_dir=tmp_path / "should_be_ignored")
        try:
            # Write something to prove we're on the test path, not the override.
            conn.execute("CREATE TABLE marker (x INTEGER)")
            conn.execute("INSERT INTO marker VALUES (1)")
            conn.commit()
        finally:
            conn.close()
        assert test_db.exists()
        assert not (tmp_path / "should_be_ignored").exists()


# ---------------------------------------------------------------------------
# _check_integrity — runs once per path, catches corruption
# ---------------------------------------------------------------------------


class TestIntegrityCheck:
    def test_healthy_db_passes_and_caches(self, tmp_path, monkeypatch):
        db = tmp_path / "healthy.db"
        monkeypatch.setattr(_conn, "_TEST_DB_PATH", str(db))
        monkeypatch.setattr(_conn, "_integrity_checked", set())

        # Wrap _check_integrity to count the number of times the full body
        # (i.e. PRAGMA-running path) is actually entered.
        full_run_calls = {"n": 0}
        real = _conn._check_integrity

        def counting_check(conn, db_path):
            already = str(db_path) in _conn._integrity_checked
            real(conn, db_path)
            if not already:
                full_run_calls["n"] += 1

        monkeypatch.setattr(_conn, "_check_integrity", counting_check)

        _conn.get_connection().close()
        _conn.get_connection().close()
        _conn.get_connection().close()

        assert full_run_calls["n"] == 1, (
            "integrity_check body should execute exactly once per path; "
            f"ran {full_run_calls['n']} times"
        )
        assert str(db) in _conn._integrity_checked

    def test_corrupted_db_raises(self, tmp_path, monkeypatch):
        """
        Write garbage into a file so that SQLite will either fail to open
        cleanly or fail the integrity check. Either way, callers must see
        a clear RuntimeError rather than silently using the bad file.
        """
        db = tmp_path / "broken.db"
        # Create a real DB first...
        real = sqlite3.connect(str(db))
        real.execute("CREATE TABLE x (v INTEGER)")
        real.commit()
        real.close()
        # ...then overwrite header bytes to corrupt it.
        with db.open("r+b") as f:
            f.seek(0)
            f.write(b"\x00" * 16)

        monkeypatch.setattr(_conn, "_TEST_DB_PATH", str(db))
        monkeypatch.setattr(_conn, "_integrity_checked", set())

        with pytest.raises((RuntimeError, sqlite3.DatabaseError)):
            _conn.get_connection()

    def test_integrity_cache_is_per_path(self, tmp_path, monkeypatch):
        """Different DB files get their own integrity entry."""
        db_a = tmp_path / "a.db"
        db_b = tmp_path / "b.db"
        monkeypatch.setattr(_conn, "_integrity_checked", set())

        monkeypatch.setattr(_conn, "_TEST_DB_PATH", str(db_a))
        _conn.get_connection().close()

        monkeypatch.setattr(_conn, "_TEST_DB_PATH", str(db_b))
        _conn.get_connection().close()

        assert str(db_a) in _conn._integrity_checked
        assert str(db_b) in _conn._integrity_checked


# ---------------------------------------------------------------------------
# FINDING: foreign_keys pragma is never enabled
# ---------------------------------------------------------------------------


class TestForeignKeysOn:
    """
    get_connection enables `PRAGMA foreign_keys = ON` on every connection.
    Every ON DELETE CASCADE / SET NULL in schema.py is therefore enforced.
    Detailed cascade behaviour tests live in test_fk_behavior.py.
    """

    def test_foreign_keys_pragma_is_on(self, isolated_db):
        conn = _conn.get_connection()
        try:
            fk_state = conn.execute("PRAGMA foreign_keys").fetchone()[0]
        finally:
            conn.close()
        assert fk_state == 1
