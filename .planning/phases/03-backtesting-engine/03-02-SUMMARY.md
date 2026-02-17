---
phase: 03-backtesting-engine
plan: 02
subsystem: backtesting
tags: [backtest, walk-forward, overfitting, rolling-window, metrics, alembic]

# Dependency graph
requires:
  - phase: 03-01
    provides: "TradeSimulator, SessionSpreadModel, MetricsCalculator"
  - phase: 02-strategy-engine
    provides: "BaseStrategy with analyze() and strategy registry"
provides:
  - "BacktestRunner class with rolling window backtest orchestration"
  - "WalkForwardValidator class with 80/20 IS/OOS overfitting detection"
  - "BacktestResult model with walk-forward fields"
  - "Alembic migration for walk-forward columns"
affects: ["03-03", "04-signal-pipeline", "05-monitoring"]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Rolling window backtest: slide window_days across H1 data, call strategy.analyze()"
    - "Walk-forward validation: 80/20 split with WFE ratio comparison"
    - "Same code path: backtest uses identical strategy.analyze() as live"

key-files:
  created:
    - "app/services/backtester.py"
    - "app/services/walk_forward.py"
    - "alembic/versions/2026_02_17_4854af26a1fe_add_walk_forward_fields_to_backtest_.py"
  modified:
    - "app/models/backtest_result.py"

key-decisions:
  - "Mutable default avoidance: window_days_list defaults to None, set to [30, 60] in method body"
  - "Force-added migration file despite alembic/versions/*.py gitignore rule for migration tracking"

patterns-established:
  - "Rolling window: window_candles = window_days * 24 for H1 timeframe"
  - "WFE threshold: OOS/IS ratio < 0.5 flags overfitting"
  - "Minimum sample: skip overfitting detection with < 5 OOS trades"

# Metrics
duration: 3min
completed: 2026-02-17
---

# Phase 3 Plan 2: BacktestRunner and WalkForwardValidator Summary

**Rolling-window BacktestRunner orchestrating strategy.analyze() on 30/60-day H1 windows, with 80/20 walk-forward overfitting detection via WFE ratio comparison**

## Performance

- **Duration:** 3 min
- **Started:** 2026-02-17T16:34:56Z
- **Completed:** 2026-02-17T16:38:03Z
- **Tasks:** 2
- **Files modified:** 4

## Accomplishments
- BacktestRunner slides rolling windows across H1 candle data and calls the EXACT same strategy.analyze() as live signal generation
- WalkForwardValidator splits data 80/20, runs independent backtests on each period, and flags overfitting when OOS metrics degrade below 50% of IS
- Walk-forward skips overfitting detection with fewer than 5 OOS trades to avoid noisy conclusions
- BacktestResult model extended with 4 walk-forward columns, Alembic migration applied

## Task Commits

Each task was committed atomically:

1. **Task 1: BacktestRunner with rolling window logic** - `e069831` (feat)
2. **Task 2: WalkForwardValidator and BacktestResult migration** - `4301f9d` (feat)

**Plan metadata:** pending (docs: complete plan)

## Files Created/Modified
- `app/services/backtester.py` - BacktestRunner with run_rolling_backtest, run_full_backtest, run_all_strategies
- `app/services/walk_forward.py` - WalkForwardValidator with WalkForwardResult dataclass and validate method
- `app/models/backtest_result.py` - Added is_walk_forward, is_overfitted, walk_forward_efficiency, spread_model columns
- `alembic/versions/2026_02_17_4854af26a1fe_add_walk_forward_fields_to_backtest_.py` - Migration adding 4 nullable columns

## Decisions Made
- Mutable default avoidance: `window_days_list` parameter defaults to `None` and is set to `[30, 60]` inside the method body rather than using a mutable default argument
- Force-added Alembic migration despite `alembic/versions/*.py` gitignore rule to keep migration tracked in version control

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered
- Alembic migration file blocked by `.gitignore` rule (`alembic/versions/*.py`). Resolved with `git add -f` since the initial migration had the same pattern. The gitignore rule appears to be from initial project setup but migrations should be tracked.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness
- BacktestRunner and WalkForwardValidator complete, ready for Plan 03-03 (backtest scheduler/storage)
- All backtesting pipeline components now available: TradeSimulator, SpreadModel, MetricsCalculator, BacktestRunner, WalkForwardValidator
- Walk-forward fields in DB ready for storing validation results

---
*Phase: 03-backtesting-engine*
*Completed: 2026-02-17*
