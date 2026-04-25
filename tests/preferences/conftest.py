"""
Preferences autouse isolation.

config_store reads/writes a JSON file at src/Grocery_Sense/config/user_config.json
and a cache file at src/Grocery_Sense/config/deals_cache.json. It also holds
a module-level cache (_config_cache / _config_mtime) that persists across
test calls unless cleared. This fixture redirects both files to tmp_path
and resets the cache on every test.
"""

from __future__ import annotations

import pytest

from Grocery_Sense.config import config_store


@pytest.fixture(autouse=True)
def tmp_config_file(tmp_path, monkeypatch):
    f = tmp_path / "user_config.json"
    cache = tmp_path / "deals_cache.json"

    monkeypatch.setattr(config_store, "_CONFIG_FILE", f)
    monkeypatch.setattr(config_store, "_CACHE_FILE", cache)

    # Clear the module-level cache so each test starts fresh.
    monkeypatch.setattr(config_store, "_config_cache", None)
    monkeypatch.setattr(config_store, "_config_mtime", None)

    return f
