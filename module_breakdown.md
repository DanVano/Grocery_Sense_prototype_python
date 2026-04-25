# Grocery Sense — Module Breakdown

Runtime-responsibility map of the codebase, used to scope and prioritize the pytest suite. Generated in Phase 1 of the QA peer-review. Revise when modules move between responsibilities, not when files move between folders.

## Module Map

Each file belongs to exactly one primary module. Folder location is secondary to runtime responsibility.

### M1 — Data Persistence Layer
Raw SQLite plumbing + domain dataclasses. CRUD only, no business logic.

- `data/connection.py`, `data/schema.py`
- `data/repositories/` — `stores_repo`, `items_repo`, `items_admin_repo`, `item_aliases_repo`, `receipts_repo`, `prices_repo`, `flyers_repo`, `shopping_list_repo`
- `domain/models.py`

### M2 — External Data Gateway
Boundary adapters for third-party systems. HTTP/SDK calls only.

- `integrations/azure_docint_client.py`
- `integrations/flyer_docint_client.py`
- `integrations/flipp_client.py`

### M3 — Price Intelligence Engine ★ critical
Math + comparison logic. The normalizer is the trunk the rest of the app hangs from.

- `services/unit_normalization_service.py`
- `services/price_history_service.py`
- `services/price_drop_alert_service.py`
- `services/multibuy_deal_service.py`
- `services/deals_service.py`

### M4 — Ingestion Pipeline ★ critical
Dirty-data funnel: OCR / Flipp blobs → canonical rows.

- `services/flyer_ingest_service.py`
- `services/flyer_sync_service.py`
- `services/flyer_sync_scheduler.py`
- `services/ingredient_mapping_service.py`

### M5 — Shopping & Planning Engine ★ critical
User-facing outputs: lists, baskets, meal picks, trips.

- `services/shopping_list_service.py`
- `services/basket_optimizer_service.py`
- `services/planning_service.py`
- `services/weekly_planner_service.py`
- `services/meal_suggestion_service.py`
- `recipes/recipe_engine.py`

### M6 — Profile & Preferences
Household identity + dietary constraints.

- `config/config_store.py`
- `services/preferences_service.py`
- `services/demo_seed_service.py`

### M7 — Presentation (Tkinter)
All `ui/*.py` windows and `main.py`. Manually exercised; excluded from the automated suite.

## Prioritization

Tested in this order (approved):

1. **M3 Price Intelligence Engine** — quiet/catastrophic failure mode; cheap pure-math tests.
2. **M4 Ingestion Pipeline** — biggest silent-corruption surface; rides the FK graph.
3. **M5 Shopping & Planning Engine** — user-facing output; "plausibly wrong" answers escape notice.
4. **M1 Data Persistence Layer** — mechanical, fails loud; still needs round-trip + FK-cascade coverage.
5. **M2 External Data Gateway** — failures are loud (HTTP/SDK errors); golden-file tests on canned responses.
6. **M6 Profile & Preferences** — side-channel inputs; low blast radius.

## Test Strategy (locked in)

- **One top-level `tests/conftest.py`** with a `memory_db` fixture: `:memory:` SQLite, runs `create_tables` + `_migrate`, monkeypatches `data.connection.get_connection`. Real SQLite only — no DB mocks (per CLAUDE.md).
- **Layout:** `tests/persistence/`, `tests/price_intelligence/`, `tests/ingestion/`, `tests/planning/`, `tests/integrations/`, `tests/preferences/`. Nested `conftest.py` only when a fixture is genuinely shared within a folder.
- **Classes** only where grouping improves readability (`TestKgLbRoundTrip`, `TestMultiBuyParse`); otherwise function-based to match existing style.
- **Golden fixtures** under `tests/fixtures/` — canned Azure DocInt JSON + Flipp responses for M2/M4.
- **Coverage targets:** 85% on M3/M4/M5, 60% on M1/M2/M6, UI excluded.
- **CI command:** `pytest tests/ --tb=short`. Add `-x` locally for fast-fail.
- **Existing smoke test** (`tests/test_backend.py`): folded into a proper integration test under `tests/planning/` with real assertions, not prints.

## Mandatory Failure Coverage

These classes of real-world failure must be exercised by the suite. Raised by Agent 2 (Defensive Dev) in Phase 1 debate; locked in as non-negotiable.

### 1. OCR-mangled quantities & units — hits M3, M4
Canonical bad inputs to parametrize:
- `"O.453"` (letter O instead of zero)
- `"1,25 kg"` (EU decimal comma)
- `"1/2 lb"` (fraction)
- `"1 L"` vs `"1 l"` vs `"1 lb"` (capital-L/lowercase-l ambiguity)

Rule: pipeline must **either** normalize correctly **or** drop `confidence` and reject. Never silently write a garbage `unit_price`.

### 2. Multi-buy / promo phrasing — hits M3, M4
Phrases the parser must decode:
- `"2/$5.00"` → $2.50/ea
- `"3 for $10"` → $3.33/ea
- `"Buy 2 Get 1 Free"` → effective 2/3 of unit price
- `"Mix & Match 4/$10"` → $2.50/ea
- `"Was $4.99 Now $2.99"` → $2.99 with prior-price metadata
- `"$1 off"` with no anchor price in OCR crop → **reject, do not guess**

Golden table of phrase → `(unit_price, deal_kind)` lives under `tests/fixtures/multibuy_phrases.json`.

### 3. Ingredient-aliasing collisions & orphaned aliases — hits M4
- False positives: `"cream"` → `"ice cream"`, `"oil"` → `"olive oil"`. The `DEFAULT_EXCLUDE_SAFE_PHRASES` allowlist at `ingredient_mapping_service.py:30` is fragile; weak matches must resolve to `method="none"`.
- Whitespace duplicates: `"Milk 2L"` vs `"Milk 2 L"` must not both satisfy `items.canonical_name UNIQUE`.
- Cascade gap: `item_aliases` has no `ON DELETE CASCADE` (`schema.py:199`) — deleting an item orphans its aliases. Test must assert documented-intent behavior and flag the schema gap if intent is "cascade."

### Cross-cutting failures (also mandatory)
- **Null / zero-price flyer lines** — `$0.00` giveaways or missing price field; `prices.unit_price NOT NULL` currently causes silent drops.
- **Duplicate `flipp_store_id`** — no `UNIQUE` constraint (`schema.py:29`); basket optimizer could double-count.
- **Inverted flyer dates** — `valid_from > valid_to` from OCR noise; expired/inverted flyers excluded from "current deals."

## Conventions

- Repos are function-based modules (raw `sqlite3`), one per table-ish concept, named `*_repo.py`. Match this style in tests.
- Services are a mix of functions and classes — match the file being tested.
- UI calls services, services call repos, repos call the DB. Tests respect the same layering: never have a persistence test import from `services/`, never have a service test bypass its repo boundary.
