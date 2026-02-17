---
phase: 05-delivery-and-visibility
plan: 02
subsystem: ui
tags: [tradingview, lightweight-charts, fastapi, jinja2, candlestick, chart, signals]

# Dependency graph
requires:
  - phase: 01-data-foundation
    provides: "Candle model and XAUUSD H1 data storage"
  - phase: 04-signal-pipeline
    provides: "Signal and Outcome models with trade signal data"
provides:
  - "GET /chart HTML page with live XAUUSD candlestick chart"
  - "GET /chart/candles JSON endpoint returning H1 candle data with Unix timestamps"
  - "GET /chart/signals JSON endpoint returning signals with outcome colors"
  - "Auto-refreshing visual dashboard for signal verification"
affects: [05-delivery-and-visibility, 06-monitoring, 07-production]

# Tech tracking
tech-stack:
  added: [jinja2, lightweight-charts-v5.1]
  patterns: [jinja2-templates, cdn-loaded-charting, rest-json-for-frontend]

key-files:
  created:
    - app/api/chart.py
    - app/templates/chart.html
  modified:
    - app/main.py
    - requirements.txt

key-decisions:
  - "TradingView Lightweight Charts v5.1 loaded from unpkg CDN (no npm build step)"
  - "Jinja2Templates with absolute path resolution via Path(__file__).resolve()"
  - "UTC-defensive Unix timestamp conversion with naive-datetime guard"
  - "LEFT JOIN Signal-Outcome for color-coded historical markers"

patterns-established:
  - "Chart data pattern: REST JSON endpoints + static HTML template with inline JS"
  - "UTC normalization: _to_unix_seconds() helper with naive-datetime guard"
  - "Outcome color mapping: green=TP, red=SL, blue=active, gray=expired"

# Metrics
duration: 3min
completed: 2026-02-17
---

# Phase 5 Plan 2: Chart Visualization Summary

**Browser-accessible XAUUSD H1 candlestick chart with TradingView Lightweight Charts v5.1, signal entry markers, SL/TP price lines, and 60-second auto-refresh**

## Performance

- **Duration:** 3 min
- **Started:** 2026-02-17T22:18:15Z
- **Completed:** 2026-02-17T22:21:17Z
- **Tasks:** 2
- **Files modified:** 4

## Accomplishments
- Three chart REST endpoints: GET /chart (HTML), GET /chart/candles (JSON), GET /chart/signals (JSON)
- H1 XAUUSD candle data served with Unix timestamp seconds in chronological order
- Signal markers as colored entry arrows with SL/TP horizontal price lines for active signals
- Dark theme responsive chart with 60-second auto-refresh and color-coded outcome legend

## Task Commits

Each task was committed atomically:

1. **Task 1: Create chart REST endpoints** - `170daab` (feat)
2. **Task 2: Create chart HTML template** - `e5fb450` (feat)

## Files Created/Modified
- `app/api/chart.py` - Chart REST endpoints: GET /chart, GET /chart/candles, GET /chart/signals
- `app/templates/chart.html` - HTML page with Lightweight Charts v5.1, signal overlays, auto-refresh
- `app/main.py` - Chart router registered with FastAPI app
- `requirements.txt` - Added jinja2>=3.1.0 dependency

## Decisions Made
- TradingView Lightweight Charts v5.1 loaded from unpkg CDN -- avoids npm build tooling, keeps frontend simple
- Jinja2Templates directory resolved via `Path(__file__).resolve()` for portable template path resolution
- UTC-defensive timestamp conversion: `_to_unix_seconds()` applies `replace(tzinfo=utc)` guard for naive datetimes
- Signal-Outcome LEFT JOIN returns (Signal, Outcome|None) tuples for color computation
- Markers sorted by time ascending as required by Lightweight Charts API

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered
- Pre-existing test failure in `tests/test_signal_pipeline.py::test_pipeline_risk_rejects_all` (coroutine/len TypeError in signal_pipeline.py:241) -- confirmed not caused by this plan's changes. All 97 other tests pass.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness
- Chart page ready at /chart -- requires running FastAPI server with candle data in database
- Signal visualization ready for manual verification once signals are generated
- No blockers for remaining Phase 5 plans

---
*Phase: 05-delivery-and-visibility*
*Completed: 2026-02-17*
