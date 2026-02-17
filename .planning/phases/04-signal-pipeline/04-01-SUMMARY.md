---
phase: 04-signal-pipeline
plan: 01
subsystem: api
tags: [strategy-selection, composite-scoring, volatility-regime, atr, ema, degradation-detection]

# Dependency graph
requires:
  - phase: 03-backtesting-engine
    provides: BacktestResult model with win_rate, profit_factor, sharpe_ratio, expectancy, max_drawdown
  - phase: 02-strategy-engine
    provides: BaseStrategy registry, compute_atr, compute_ema indicator helpers, candles_to_dataframe
  - phase: 01-data-foundation
    provides: Candle model with H1/H4 OHLCV data, Strategy model
provides:
  - StrategySelector service with composite scoring and select_best() returning StrategyScore
  - VolatilityRegime enum (LOW/MEDIUM/HIGH) via ATR percentile classification
  - StrategyScore dataclass with all scoring metadata
  - H4 EMA confluence check for multi-timeframe signal enrichment
  - METRIC_WEIGHTS configuration for composite scoring
affects: [04-signal-pipeline plans 02-05, signal-generator, signal-pipeline orchestrator]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Composite scoring with min-max normalisation and weighted sum"
    - "ATR percentile-based volatility regime classification (25th/75th thresholds)"
    - "Strategy degradation detection via baseline comparison"
    - "Regime-strategy modifier pattern (multiplicative +/-10%)"

key-files:
  created:
    - app/services/strategy_selector.py
  modified: []

key-decisions:
  - "Task 2 (H4 confluence) included in Task 1 commit since both target the same file and the method is a natural part of the class"
  - "Used dataclass (not Pydantic BaseModel) for StrategyScore -- internal scoring data, no serialization boundary"
  - "Degradation checks baseline against OLDEST result (not a rolling window) for simplicity"
  - "Fallback chain for backtest results: 60-day window -> 30-day -> any non-walk-forward"

patterns-established:
  - "Composite scoring: normalise metrics to [0,1], multiply by weight, sum"
  - "Regime detection: ATR percentile with 25th/75th thresholds on 720 H1 candles"
  - "Degradation: absolute win rate drop >0.15 or profit factor <1.0"

# Metrics
duration: 3min
completed: 2026-02-17
---

# Phase 4 Plan 01: Strategy Selector Summary

**Composite-scored strategy selector with ATR volatility regime, degradation detection, and H4 EMA confluence check**

## Performance

- **Duration:** 3 min
- **Started:** 2026-02-17T17:33:16Z
- **Completed:** 2026-02-17T17:36:00Z
- **Tasks:** 2
- **Files modified:** 1

## Accomplishments
- StrategySelector service with `select_best()` returning the highest-scoring strategy based on 5 weighted backtest metrics
- ATR-based volatility regime detection classifying market as LOW/MEDIUM/HIGH using percentile ranking on 30 days of H1 candles
- Regime-strategy score modifiers: breakout_expansion penalised -10% in HIGH volatility, trend_continuation penalised -10% in LOW volatility
- Strategy degradation detection flagging win rate drops >15% from baseline and profit factor <1.0
- H4 EMA-50 vs EMA-200 confluence check for multi-timeframe signal confidence enrichment
- Minimum 50-trade enforcement excluding unreliable strategies from selection

## Task Commits

Each task was committed atomically:

1. **Task 1: StrategySelector service with composite scoring and volatility regime** - `71e638d` (feat)
2. **Task 2: Multi-timeframe confluence check** - included in `71e638d` (same file, method naturally part of class)

## Files Created/Modified
- `app/services/strategy_selector.py` - StrategySelector class with composite scoring, volatility regime detection, degradation checks, and H4 confluence method (342 lines)

## Decisions Made
- Used `dataclass` for StrategyScore instead of Pydantic BaseModel since it is internal scoring data with no serialization boundary
- Included `check_h4_confluence` in the Task 1 commit since both tasks target the same file and the method is a natural part of the StrategySelector class
- Degradation baseline is the OLDEST non-walk-forward BacktestResult for each strategy (simple, stable baseline)
- Fallback chain for backtest results: prefer window_days=60, fallback to 30, then any non-walk-forward result

## Deviations from Plan

None - plan executed exactly as written. Task 2 was included in Task 1's commit since both tasks target the same single file and the method was a natural part of the class definition.

## Issues Encountered
None

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- StrategySelector is ready for integration with SignalGenerator (Plan 04-02)
- `select_best()` returns `StrategyScore | None` with full scoring metadata
- `check_h4_confluence()` is public and ready for SignalGenerator to call for +5 confidence boost
- No blockers for Plan 04-02

---
*Phase: 04-signal-pipeline*
*Completed: 2026-02-17*
