# Grocery Sense — CLAUDE.md

## Overview

Desktop grocery shopping optimizer for families. Tracks prices from receipts and store flyers, manages a shared shopping list, suggests meals from deals, and optimizes multi-store trips. Pure Python desktop app.

## Stack

Python 3.x · Tkinter + SQLite (stdlib) · pytest · Azure Document Intelligence · rapidfuzz · requests.

## Run

```bash
python -m Grocery_Sense.main    # launch the GUI
pytest tests/                   # run tests
```

SQLite DB auto-creates at `src/Grocery_Sense/data/db/grocery_sense.db` on first launch.

## Architecture

Strict top-down layering:

```
ui/ → services/ → data/repositories/ → data/ (connection, schema) → domain/models.py
                              ↑
                  integrations/ (Azure OCR, Flipp)
```

- UI calls services. Never repositories, never the DB.
- Services call repositories. Never the DB directly.
- Repositories are function-based modules (raw `sqlite3`), one per table-ish concept, named `*_repo.py`.
- Domain models are `@dataclass` — plain data, no methods.
- Business logic lives in services only.

## Layout

All source under `src/Grocery_Sense/`:

- `ui/` — Tkinter windows, one file per feature. Main window: `ui/tk_main.py`.
- `services/` — Business logic. Mix of functions and classes.
- `data/repositories/` — CRUD modules.
- `data/` — `connection.py`, `schema.py` (DDL + `initialize_database()`), `db/`.
- `domain/models.py` — dataclass definitions.
- `integrations/` — external API clients.
- `config/config_store.py` — JSON-backed user/household profile.
- `recipes/` — `recipe_engine.py` + `recipes.json`.
- `main.py` — CLI smoke-test / module entry.

Tests in top-level `tests/` as `test_*.py`.

## Database

SQLite, schema in `data/schema.py`, initialized on app start. Tables group as:

- **Catalog:** `stores`, `items`, `item_aliases`
- **Purchases:** `receipts`, `receipt_line_items`, `receipt_raw_json`
- **Prices & deals:** `prices`, `flyer_sources`
- **User state:** `shopping_list`, `user_profile`, `sync_meta`

## Constraints

**Must not:**
- Add an ORM — raw `sqlite3` only.
- Add a web framework (Flask, FastAPI, etc.).
- Hardcode secrets (Azure keys, etc.) — load from `.env` or `config_store.py`.
- Format user input into SQL — always `?` placeholders.
- Touch `Notes/` unless the user explicitly names it — personal, excluded from git.
- Add docstrings, comments, or drive-by refactors to code you didn't change.
- Create helper utilities for one-off operations.
- Add defensive checks for conditions internal guarantees already cover. Validate only at boundaries (user input, Azure responses, file uploads).
- Mock the database in tests.

**Must:**
- Use `unit_normalization_service` when comparing prices across unit scales (kg/lb/g). Never write ad-hoc unit math.
- Match the style of the file being edited (function vs. class service, type hint density, `StringVar` usage, whether repo functions take `conn` or call `get_connection()` internally).
- Surface errors to the user via `messagebox.showerror`, not `print`.
- Use pytest with real SQLite — `:memory:` or a fixture DB.
