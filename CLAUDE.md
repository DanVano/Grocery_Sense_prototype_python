# Grocery Sense — CLAUDE.md

## Project Overview

Grocery Sense is a **desktop grocery shopping optimizer** for families. It tracks prices from receipts and store flyers, manages shared shopping lists, suggests meals based on deals, and optimizes which stores to shop at. Built entirely in Python as a standalone desktop application (no web server).

---

## Technology Stack

| Layer | Technology |
|-------|-----------|
| Language | Python 3.x |
| GUI | Tkinter (stdlib) |
| Database | SQLite 3 (stdlib) |
| Testing | pytest |
| OCR / Document AI | Azure Document Intelligence (`azure-ai-documentintelligence`) |
| Fuzzy Matching | rapidfuzz |
| HTTP Client | requests |

**Install dependencies:**
```bash
python -m venv .venv
source .venv/Scripts/activate   # Windows (bash)
# or: .venv\Scripts\activate    # Windows (cmd/PowerShell)
pip install -r requirements.txt
```

---

## Build, Test, and Run Commands

```bash
# Run the full GUI application
python -m Grocery_Sense.main

# Run all tests
pytest tests/

# Run a specific test file
pytest tests/test_shopping_list_service.py

# Run tests with verbose output
pytest tests/ -v
```

There is no build step — Python runs directly from source. The SQLite database is created automatically on first launch at `src/Grocery_Sense/data/db/grocery_sense.db`.

---

## Core Architecture

The codebase follows a **clean layered architecture** with strict separation of concerns:

```
UI Layer          src/Grocery_Sense/ui/           Tkinter windows (one per feature)
    ↓
Service Layer     src/Grocery_Sense/services/     Business logic
    ↓
Repository Layer  src/Grocery_Sense/data/repositories/   Data access (function-based)
    ↓
Domain Layer      src/Grocery_Sense/domain/models.py      Dataclass definitions
    ↓
Data Layer        src/Grocery_Sense/data/         SQLite connection + schema init
    ↑
Integration Layer src/Grocery_Sense/integrations/ External API clients (Azure, Flipp)
```

- **UI never touches the database directly** — always goes through a service.
- **Services never touch the database directly** — always go through repositories.
- **Repositories are function-based** (not class-based ORM). Each module exposes plain functions like `get_item_by_id()`, `list_all_items()`.
- **Domain models are `@dataclass`** — lightweight, no getters/setters.

---

## Important File Paths

### Entry Points
| File | Purpose |
|------|---------|
| `src/Grocery_Sense/ui/tk_main.py` | Main Tkinter window — the real app entry point |
| `src/Grocery_Sense/main.py` | CLI smoke-test harness; also used as `python -m Grocery_Sense.main` |

### Core Domain
| File | Purpose |
|------|---------|
| `src/Grocery_Sense/domain/models.py` | All dataclass definitions (`Store`, `Item`, `Receipt`, `ShoppingListItem`, etc.) |
| `src/Grocery_Sense/data/schema.py` | SQLite schema DDL and `initialize_database()` |
| `src/Grocery_Sense/data/connection.py` | SQLite connection manager |

### Repositories (Data Access)
| File | Purpose |
|------|---------|
| `src/Grocery_Sense/data/repositories/items_repo.py` | Canonical grocery item CRUD |
| `src/Grocery_Sense/data/repositories/prices_repo.py` | Price history queries |
| `src/Grocery_Sense/data/repositories/receipts_repo.py` | Receipt management |
| `src/Grocery_Sense/data/repositories/flyers_repo.py` | Flyer deal data |
| `src/Grocery_Sense/data/repositories/shopping_list_repo.py` | Shopping list CRUD |
| `src/Grocery_Sense/data/repositories/stores_repo.py` | Store CRUD + favorites |
| `src/Grocery_Sense/data/repositories/item_aliases_repo.py` | Fuzzy match alias lookup |
| `src/Grocery_Sense/data/repositories/items_admin_repo.py` | Item bulk operations |

### Services (Business Logic)
| File | Purpose |
|------|---------|
| `src/Grocery_Sense/services/basket_optimizer_service.py` | Find cheapest multi-store combinations |
| `src/Grocery_Sense/services/deals_service.py` | Identify current sales/deals |
| `src/Grocery_Sense/services/meal_suggestion_service.py` | Recommend meals from recipes + deals |
| `src/Grocery_Sense/services/price_history_service.py` | Price trend analysis |
| `src/Grocery_Sense/services/price_drop_alert_service.py` | Alert when tracked items go on sale |
| `src/Grocery_Sense/services/shopping_list_service.py` | Shopping list parsing + management |
| `src/Grocery_Sense/services/flyer_ingest_service.py` | Parse and ingest flyer data |
| `src/Grocery_Sense/services/unit_normalization_service.py` | Normalize units (kg, lb, each, etc.) |
| `src/Grocery_Sense/services/preferences_service.py` | Household dietary preferences |
| `src/Grocery_Sense/services/planning_service.py` | Plan-to-receipt analysis |
| `src/Grocery_Sense/services/weekly_planner_service.py` | Weekly meal planning |
| `src/Grocery_Sense/services/ingredient_mapping_service.py` | Map recipe ingredients to items |
| `src/Grocery_Sense/services/demo_seed_service.py` | Seed demo/sample data |
| `src/Grocery_Sense/services/multibuy_deal_service.py` | Multi-buy deal analysis (2-for-1 etc.) |

### UI Windows
| File | Purpose |
|------|---------|
| `src/Grocery_Sense/ui/tk_main.py` | Main window + navigation menu |
| `src/Grocery_Sense/ui/receipt_import_window.py` | Upload receipts + Azure OCR |
| `src/Grocery_Sense/ui/receipt_browser_window.py` | Browse receipt history |
| `src/Grocery_Sense/ui/deal_feed_window.py` | Current flyer deals display |
| `src/Grocery_Sense/ui/basket_optimizer_window.py` | Multi-store optimization UI |
| `src/Grocery_Sense/ui/price_history_window.py` | Price trend charts |
| `src/Grocery_Sense/ui/price_drop_alerts_window.py` | Price alert viewer |
| `src/Grocery_Sense/ui/item_manager_window.py` | Manage canonical items |
| `src/Grocery_Sense/ui/flyer_import_window.py` | Manual flyer upload |
| `src/Grocery_Sense/ui/store_plan_window.py` | Store visit planning |
| `src/Grocery_Sense/ui/preferences_wizard_window.py` | Household preference setup wizard |

### Integrations
| File | Purpose |
|------|---------|
| `src/Grocery_Sense/integrations/azure_docint_client.py` | Azure Document Intelligence receipt OCR |
| `src/Grocery_Sense/integrations/flyer_docint_client.py` | Flyer document processing |
| `src/Grocery_Sense/integrations/flipp_client.py` | Flipp API stub (not yet implemented) |

### Configuration & Data
| File | Purpose |
|------|---------|
| `src/Grocery_Sense/config/config_store.py` | JSON-based user/household profile storage |
| `src/Grocery_Sense/data/db/grocery_sense.db` | SQLite database (auto-created on first run) |
| `src/Grocery_Sense/recipes/recipe_engine.py` | Recipe loader and filter |
| `src/Grocery_Sense/recipes/recipes.json` | Recipe database |
| `requirements.txt` | Python dependencies |

### Tests
| File | Purpose |
|------|---------|
| `tests/test_shopping_list_service.py` | Shopping list service tests |
| `tests/test_weekly_planner.py` | Weekly planner service tests |
| `tests/test_meal_suggestion_service.py` | Meal suggestion engine tests |
| `tests/test_items_repo.py` | Item repository tests |
| `tests/test_repo_usage.py` | Repository integration tests |
| `tests/test_backend.py` | Backend integration tests |
| `tests/test_db_creation.py` | Database schema creation test |
| `tests/test_item_mapping_manual.py` | Manual item mapping tests |

---

## Database Schema (SQLite)

11 core tables:

| Table | Purpose |
|-------|---------|
| `stores` | Grocery store locations and metadata |
| `items` | Canonical grocery item catalog |
| `receipts` | Purchase receipt headers |
| `receipt_line_items` | Individual line items per receipt |
| `prices` | Price history (from receipts and flyers) |
| `flyer_sources` | Flyer metadata (provider, validity window) |
| `shopping_list` | Active household shopping list |
| `item_aliases` | Fuzzy-match aliases for item name resolution |
| `user_profile` | Legacy user settings |
| `sync_meta` | Device sync metadata |
| `receipt_raw_json` | Raw Azure OCR JSON responses |

The schema is defined in `src/Grocery_Sense/data/schema.py` and initialized via `initialize_database()`, called automatically on app start.

---

## Coding Standards

### General
- **Python 3.x** syntax throughout — no Python 2 patterns.
- **Type hints** on function signatures where they aid readability. Not required everywhere, but maintain consistency with the surrounding file.
- **No external ORM** (no SQLAlchemy, no Django ORM). Raw SQLite via the `sqlite3` stdlib module only.

### Repository Layer
- Repository functions are **module-level functions**, not methods on a class.
- Each function receives a `conn` (SQLite connection) as its first argument, or calls `get_connection()` internally — follow the pattern of the file you're editing.
- SQL is written as **plain string literals** — no query builders.
- Use **parameterized queries** (`?` placeholders) at all times. Never format user input into SQL strings.

### Service Layer
- Services may be functions or classes — follow the pattern of the existing service you're editing.
- Services depend on repositories and other services via import, not via a DI container. Optional dependencies may be passed in `__init__` (e.g., `MealSuggestionService(price_history_service=None)`).
- Business logic lives in services. Repositories must not contain business logic.

### Domain Models
- Domain objects are `@dataclass` — keep them as plain data containers.
- Do not add methods or business logic to dataclasses. Logic belongs in services.

### UI Layer
- Each feature window is its own class in its own file.
- Windows import services directly — they do not import repositories.
- Keep UI event handlers thin: delegate to services, display the result.
- Use Tkinter's `StringVar`, `IntVar`, etc. for two-way binding where the pattern is already established.

### Error Handling
- Only validate at system boundaries (user input fields, Azure API responses, file uploads).
- Do not add defensive checks for conditions that cannot occur given internal guarantees.
- Surface errors to the user via Tkinter message boxes (`messagebox.showerror`), not print statements.

### Naming Conventions
- **Files**: `snake_case.py`
- **Functions & variables**: `snake_case`
- **Classes**: `PascalCase`
- **Constants**: `UPPER_SNAKE_CASE`
- Repository functions: verb-first — `get_`, `list_`, `insert_`, `update_`, `delete_`
- Service functions/methods: action-oriented — `calculate_`, `find_`, `parse_`, `suggest_`

### Testing
- Tests live in `tests/` and are named `test_*.py`.
- Use pytest — no unittest classes unless already present in the file you're editing.
- Tests may use an in-memory SQLite database (`:memory:`) for isolation.
- Do not mock the database in tests — use real SQLite connections (either `:memory:` or a test fixture DB).

### What Not To Do
- Do not add docstrings or comments to code you did not change.
- Do not refactor surrounding code when fixing a bug or adding a feature.
- Do not introduce web frameworks (Flask, FastAPI, etc.) — this is a desktop app.
- Do not add ORM dependencies — keep SQLite raw.
- Do not create helper utilities for one-off operations.
