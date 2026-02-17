---
phase: 01-data-foundation
plan: 03
subsystem: testing
tags: [pytest, pytest-asyncio, httpx, asyncpg, mocking, tdd]

# Dependency graph
requires:
  - phase: 01-01
    provides: "SQLAlchemy models, FastAPI app, database engine, health endpoint"
  - phase: 01-02
    provides: "CandleIngestor service, candles API, gap detection, upsert logic"
provides:
  - "19-test async test suite validating all Phase 1 data pipeline behaviors"
  - "Test database infrastructure (goldsignal_test) with per-test isolation"
  - "Proven upsert deduplication, gap detection, incremental fetch, timezone correctness"
affects: [02-backtesting, 03-strategy, testing-infrastructure]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Per-test engine isolation pattern for asyncpg (avoids connection contention)"
    - "TDClient mock pattern returning fake candle JSON for all API tests"
    - "Table truncation between tests (DELETE FROM candles) for isolation"

key-files:
  created:
    - tests/__init__.py
    - tests/conftest.py
    - tests/test_candle_ingestor.py
    - tests/test_health.py
    - tests/test_candles_api.py
    - pytest.ini
  modified: []

key-decisions:
  - "Per-test engine creation instead of savepoint pattern (asyncpg connection contention with session.commit)"
  - "Table truncation (DELETE) for test isolation instead of transaction rollback"
  - "goldsignal_test as separate test database (configurable via TEST_DATABASE_URL env var)"

patterns-established:
  - "Test fixture pattern: db_session yields isolated session, mock_twelve_data patches TDClient"
  - "API test pattern: client fixture with dependency_overrides for test session injection"
  - "All tests use pytest-asyncio auto mode with function-scoped async fixtures"

# Metrics
duration: 7min
completed: 2026-02-17
---

# Phase 1 Plan 3: Test Suite Summary

**19-test async test suite proving upsert deduplication, gap detection, incremental fetch, timezone correctness, and REST API behavior using pytest-asyncio with mocked Twelve Data**

## Performance

- **Duration:** 7 min
- **Started:** 2026-02-17T14:07:30Z
- **Completed:** 2026-02-17T14:14:34Z
- **Tasks:** 2
- **Files modified:** 6

## Accomplishments
- 19 passing tests covering all Phase 1 data pipeline requirements
- Upsert deduplication proven: same candles inserted twice result in exactly 5 rows, not 10
- Gap detection proven: correctly identifies missing H1 candles and filters out Saturday/Sunday
- Incremental fetch proven: uses latest stored timestamp as start_date for subsequent API calls
- Timezone correctness proven: all stored candle timestamps are UTC-aware
- Health and candles API endpoints verified with async HTTP client

## Task Commits

Each task was committed atomically:

1. **Task 1: Create test infrastructure and fixtures** - `0a43dbf` (test)
2. **Task 2: Write tests for candle ingestor and API endpoints** - `465f7f2` (test)

## Files Created/Modified
- `pytest.ini` - Pytest configuration with asyncio_mode=auto
- `tests/__init__.py` - Package init (empty)
- `tests/conftest.py` - Async fixtures: db_session, client, mock_twelve_data, sample_candles
- `tests/test_candle_ingestor.py` - 12 tests: upsert dedup (3), gap detection (3), incremental fetch (4), timezone (2)
- `tests/test_health.py` - 3 tests: status ok, timestamp present, version 0.1.0
- `tests/test_candles_api.py` - 4 tests: list candles, limit param, invalid timeframe 422, empty list

## Decisions Made
- **Per-test engine isolation:** asyncpg does not support concurrent operations on a single connection, so the savepoint/nested-transaction pattern fails when application code calls `session.commit()`. Switched to creating a fresh engine per test with table truncation for cleanup. This is slightly slower but completely reliable.
- **Table truncation over rollback:** `DELETE FROM candles` between tests is simpler and avoids SQLAlchemy event listener complexity with async sessions.
- **Separate test database:** `goldsignal_test` created alongside `goldsignal` to prevent any test data contamination. Configurable via `TEST_DATABASE_URL` env var.

## Deviations from Plan

None - plan executed exactly as written. All existing application code (candle_ingestor.py, health.py, candles.py) worked correctly -- no bugs found during testing.

## Issues Encountered
- **asyncpg connection contention:** Initial implementation using session-scoped engine with savepoint pattern caused "another operation is in progress" errors because `upsert_candles` calls `session.commit()` which interacts poorly with asyncpg's single-operation-per-connection constraint. Resolved by switching to per-test engine creation with explicit table cleanup.
- **pytest-asyncio 1.3.0 scope mismatch:** Session-scoped async fixtures require matching event loop scope in pytest-asyncio 1.3.0. Avoided by making all fixtures function-scoped with a module-level engine for schema management only.

## User Setup Required

None - test database `goldsignal_test` was created automatically. Future developers need PostgreSQL running locally with a `goldsignal_test` database (or set `TEST_DATABASE_URL` env var).

## Next Phase Readiness
- Phase 1 (Data Foundation) is complete: models, ingestion, API, and tests all verified
- Ready to proceed to Phase 2 (Backtesting Framework)
- No blockers or concerns for Phase 2

---
*Phase: 01-data-foundation*
*Completed: 2026-02-17*
