---
phase: 01-data-foundation
plan: 02
subsystem: data-pipeline
tags: [twelvedata, ohlcv, upsert, apscheduler, cron, gap-detection, postgresql, tenacity]

# Dependency graph
requires:
  - phase: 01-data-foundation
    plan: 01
    provides: "Candle ORM model, AsyncSession factory, APScheduler singleton, Settings with twelve_data_api_key"
provides:
  - "CandleIngestor service: fetch, upsert, incremental fetch, gap detection"
  - "4 APScheduler cron jobs for M15/H1/H4/D1 candle refresh"
  - "GET /candles/{timeframe} REST endpoint with pagination"
  - "GET /candles/{timeframe}/gaps gap detection endpoint"
  - "CandleResponse Pydantic schema"
affects: [01-data-foundation-03, 02-feature-engineering, 04-signal-pipeline]

# Tech tracking
tech-stack:
  added: [twelvedata TDClient, tenacity retry]
  patterns: [pg_insert ON CONFLICT upsert, generate_series gap detection, CronTrigger scheduling, try/except job guards]

key-files:
  created:
    - app/services/__init__.py
    - app/services/candle_ingestor.py
    - app/workers/jobs.py
    - app/api/candles.py
    - app/schemas/candle.py
  modified:
    - app/workers/scheduler.py
    - app/main.py

key-decisions:
  - "Synchronous TDClient._fetch_from_api wrapped with tenacity retry (3 attempts, exponential backoff max 30s)"
  - "Interval embedded as SQL literal in generate_series to avoid asyncpg binding limitation"
  - "CronTrigger for precise candle-close alignment with 1-minute offset"
  - "TimeframeEnum for path parameter validation (rejects invalid timeframes at FastAPI level)"

patterns-established:
  - "Service pattern: CandleIngestor class encapsulates API + DB + logic"
  - "Job guard pattern: all scheduled jobs wrapped in try/except to prevent scheduler crashes"
  - "Upsert deduplication: pg_insert ON CONFLICT DO UPDATE for idempotent writes"
  - "Incremental fetch: get_latest_timestamp -> start_date -> fetch only new candles"

# Metrics
duration: 10min
completed: 2026-02-17
---

# Phase 1 Plan 2: Candle Ingestion Summary

**Twelve Data XAUUSD ingestion pipeline with pg_insert upsert deduplication, generate_series gap detection, CronTrigger scheduling, and REST query endpoints**

## Performance

- **Duration:** 10 min
- **Started:** 2026-02-17T13:54:41Z
- **Completed:** 2026-02-17T14:04:07Z
- **Tasks:** 2
- **Files modified:** 7

## Accomplishments
- CandleIngestor service with Twelve Data fetch, tenacity retry, upsert deduplication, incremental fetch, and gap detection
- 4 APScheduler cron jobs aligned to 1 minute after candle close (M15, H1, H4, D1)
- REST endpoints for querying candles and detecting gaps with proper validation
- All timestamps UTC-aware, all prices Decimal (never float in database)

## Task Commits

Each task was committed atomically:

1. **Task 1: Build Twelve Data candle ingestion service** - `09a4a16` (feat)
2. **Task 2: Register APScheduler jobs and create candles API** - `2586b61` (feat)

## Files Created/Modified
- `app/services/__init__.py` - Empty package init for services module
- `app/services/candle_ingestor.py` - Core ingestion: fetch, upsert, gap detect, incremental fetch
- `app/workers/jobs.py` - refresh_candles job function with try/except guard
- `app/workers/scheduler.py` - CronTrigger job registration for 4 timeframes
- `app/api/candles.py` - GET /candles/{timeframe} and GET /candles/{timeframe}/gaps
- `app/schemas/candle.py` - CandleResponse Pydantic schema with from_attributes
- `app/main.py` - Added candles router and register_jobs() call in lifespan

## Decisions Made
- **Synchronous TDClient wrapper:** The twelvedata library is synchronous internally. Used a sync method `_fetch_from_api` decorated with tenacity retry. Async callers invoke it within the service methods.
- **SQL literal for interval:** asyncpg cannot bind a plain string as a PostgreSQL interval parameter. Embedded the interval value (from a controlled mapping, not user input) as a SQL literal in the generate_series query.
- **CronTrigger over IntervalTrigger:** Provides precise alignment to candle close times. M15 fires at :01,:16,:31,:46 -- exactly 1 minute after each 15-minute candle closes.
- **TimeframeEnum validation:** FastAPI path parameter validated via enum, returning 422 for invalid timeframes before any database query executes.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Fixed asyncpg interval parameter binding in detect_gaps**
- **Found during:** Task 2 (endpoint verification)
- **Issue:** asyncpg driver cannot bind a string as a PostgreSQL interval type via parameterized query. The `:interval::interval` and later `CAST(:interval AS interval)` both failed.
- **Fix:** Embedded the interval as a SQL literal (`'{pg_interval}'::interval`) since the value comes from a controlled internal mapping (INTERVAL_PG dict), not user input.
- **Files modified:** `app/services/candle_ingestor.py`
- **Verification:** `detect_gaps()` returns correct gap list; `/candles/H1/gaps` endpoint returns 200
- **Committed in:** `2586b61` (Task 2 commit)

**2. [Rule 1 - Bug] Fixed SQLAlchemy parameter syntax conflict with PostgreSQL casts**
- **Found during:** Task 2 (endpoint verification)
- **Issue:** Initial `:start_ts::timestamptz` syntax conflicted with SQLAlchemy's `:name` parameter parsing.
- **Fix:** Changed to `CAST(:start_ts AS timestamptz)` syntax which SQLAlchemy handles correctly.
- **Files modified:** `app/services/candle_ingestor.py`
- **Verification:** Query executes without syntax errors
- **Committed in:** `2586b61` (Task 2 commit)

---

**Total deviations:** 2 auto-fixed (2 bugs)
**Impact on plan:** Both fixes necessary for correct asyncpg/SQLAlchemy interop. No scope creep.

## Issues Encountered
- Python 3.9 (system) vs Python 3.12 (venv) version difference caused initial import errors during testing. Resolved by using the project venv consistently.

## User Setup Required

**External service requires configuration.** The Twelve Data API key must be set:
- `TWELVE_DATA_API_KEY` in `.env` - Get from https://twelvedata.com dashboard
- Currently set to `your_api_key_here` placeholder
- Free tier: 800 requests/day, 8 requests/minute

## Next Phase Readiness
- Ingestion pipeline ready for live data once API key is configured
- Plan 01-03 (Technical Indicators) can build on stored candle data via the candles API
- Gap detection enables monitoring of data completeness
- Scheduler jobs will auto-start on app boot

---
*Phase: 01-data-foundation*
*Completed: 2026-02-17*
