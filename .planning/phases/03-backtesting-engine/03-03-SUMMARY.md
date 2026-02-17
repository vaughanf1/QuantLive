---
phase: 03-backtesting-engine
plan: 03
subsystem: backtesting
tags: [apscheduler, cron, pytest, walk-forward, trade-simulation, metrics]

# Dependency graph
requires:
  - phase: 03-backtesting-engine (plans 01-02)
    provides: TradeSimulator, MetricsCalculator, SessionSpreadModel, BacktestRunner, WalkForwardValidator, BacktestResult model
  - phase: 01-data-foundation
    provides: Candle model, async_session_factory, APScheduler infrastructure
  - phase: 02-strategy-engine
    provides: BaseStrategy registry, concrete strategies, CandidateSignal model
provides:
  - run_daily_backtests() async job function with DB persistence
  - CronTrigger registration at 02:00 UTC for daily backtests
  - 20-test suite covering trade simulation, metrics, and walk-forward
affects: [04-signal-pipeline, 05-api-endpoints]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Scheduled job creates own session via async_session_factory() (not FastAPI Depends)"
    - "Strategy imports inside job function to trigger registration and avoid circular imports"
    - "Top-level try/except in all scheduled jobs to prevent scheduler crashes"

key-files:
  created:
    - tests/test_trade_simulator.py
    - tests/test_metrics_calculator.py
    - tests/test_backtester.py
  modified:
    - app/workers/jobs.py
    - app/workers/scheduler.py

key-decisions:
  - "Strategy imports inside run_daily_backtests() to avoid circular imports and ensure registry population"
  - "Walk-forward efficiency averaged across win_rate and profit_factor WFE ratios for single DB column"
  - "BacktestResult rows persisted with spread_model='session_aware' for traceability"

patterns-established:
  - "Job function pattern: import dependencies inside function, wrap in try/except, create own session"
  - "Test pattern: pure unit tests with mock strategies and inline candle data (no DB required)"

# Metrics
duration: 5min
completed: 2026-02-17
---

# Phase 3 Plan 3: Daily Backtest Job and Test Suite Summary

**APScheduler daily backtest job at 02:00 UTC with BacktestResult persistence and 20-test validation suite covering trade simulation, metrics, and walk-forward overfitting detection**

## Performance

- **Duration:** 5 min
- **Started:** 2026-02-17T16:40:46Z
- **Completed:** 2026-02-17T16:45:10Z
- **Tasks:** 2
- **Files modified:** 5

## Accomplishments
- Daily backtest job runs all registered strategies on 30d and 60d windows with result persistence
- Walk-forward validation results persisted with overfitting flags and efficiency metrics
- 20 passing tests: 9 TradeSimulator, 6 MetricsCalculator, 5 BacktestRunner/WalkForward
- Scheduler now registers 5 jobs (4 candle refresh + 1 daily backtest)

## Task Commits

Each task was committed atomically:

1. **Task 1: Daily backtest job with result persistence** - `4ae23cf` (feat)
2. **Task 2: Test suite for backtesting engine** - `a1197eb` (test)

## Files Created/Modified
- `app/workers/jobs.py` - Added run_daily_backtests() async job with strategy iteration and BacktestResult persistence
- `app/workers/scheduler.py` - Registered CronTrigger(hour=2, minute=0) for daily backtests
- `tests/test_trade_simulator.py` - 9 unit tests: BUY/SELL TP1/TP2/SL, SL priority, expired, spread, no-lookahead
- `tests/test_metrics_calculator.py` - 6 unit tests: empty, all winners, all losers, mixed, single trade, max drawdown
- `tests/test_backtester.py` - 5 integration tests: instantiation, insufficient data, analyze integration, OOS trades, overfitting detection

## Decisions Made
- Strategy imports inside run_daily_backtests() function body (not at module level) to trigger auto-registration and avoid circular imports
- Walk-forward efficiency stored as average of win_rate and profit_factor WFE ratios in single Decimal column
- BacktestResult rows tagged with spread_model="session_aware" for future audit trail

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Fixed _make_candles helper overflow in test_expired_no_hit**
- **Found during:** Task 2 (test suite)
- **Issue:** Test helper used `replace(hour=10+i)` which overflows at i=14 for 73-bar candle sets
- **Fix:** Changed to `base_time + timedelta(hours=i)` for unlimited bar count support
- **Files modified:** tests/test_trade_simulator.py
- **Verification:** All 20 tests pass
- **Committed in:** a1197eb (Task 2 commit)

---

**Total deviations:** 1 auto-fixed (1 bug in test helper)
**Impact on plan:** Trivial fix to test fixture. No scope creep.

## Issues Encountered
None

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- Phase 3 (Backtesting Engine) is fully complete: all 3 plans delivered
- BacktestRunner, WalkForwardValidator, and daily job are operational
- 20 tests validate simulation correctness, metric calculation, and overfitting detection
- Ready for Phase 4 (Signal Pipeline) which will consume backtest results for strategy selection

---
*Phase: 03-backtesting-engine*
*Completed: 2026-02-17*
