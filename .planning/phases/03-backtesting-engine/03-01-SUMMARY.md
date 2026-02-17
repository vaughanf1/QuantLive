---
phase: 03-backtesting-engine
plan: 01
subsystem: backtesting
tags: [trade-simulation, spread-model, metrics, decimal, xauusd, backtest]

# Dependency graph
requires:
  - phase: 02-strategy-engine
    provides: CandidateSignal, Direction, BaseStrategy (app/strategies/base.py)
  - phase: 02-strategy-engine
    provides: get_active_sessions() (app/strategies/helpers/session_filter.py)
provides:
  - TradeSimulator class with simulate_trade() and simulate_signals()
  - SimulatedTrade dataclass and TradeOutcome enum
  - SessionSpreadModel with session-aware spread lookup
  - MetricsCalculator with 5 key performance metrics
  - BacktestMetrics dataclass
affects: [03-02-backtest-runner, 03-03-walk-forward, 04-signal-pipeline]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Float internally, Decimal at boundary (consistent with 02-02 pattern)"
    - "SL priority over TP in same bar (conservative assumption)"
    - "Session-aware spread costs via get_active_sessions()"
    - "Capped Numeric(10,4) values for DB compatibility (profit_factor max 9999.9999)"

key-files:
  created:
    - app/services/trade_simulator.py
    - app/services/spread_model.py
    - app/services/metrics_calculator.py
  modified: []

key-decisions:
  - "SL always takes priority over TP when both could hit in same bar"
  - "XAUUSD pip value = $0.10 price movement"
  - "MAX_BARS_FORWARD = 72 (3 days at H1) before EXPIRED"
  - "Spread model returns tightest spread when multiple sessions overlap"
  - "profit_factor capped at 9999.9999 for Numeric(10,4) DB column"
  - "Sharpe ratio annualized with 252 trading days, requires >= 2 trades"

patterns-established:
  - "TradeSimulator pattern: float math internally, Decimal(str(round(x, 2))) at SimulatedTrade boundary"
  - "MetricsCalculator pattern: float math internally, Decimal(str(round(x, 4))) at BacktestMetrics boundary"
  - "Session spread lookup: delegates to session_filter.get_active_sessions()"

# Metrics
duration: 3min
completed: 2026-02-17
---

# Phase 3 Plan 1: Core Backtesting Components Summary

**TradeSimulator walks CandidateSignals through OHLC bars (SL-priority, 72-bar expiry), SessionSpreadModel provides session-aware XAUUSD spreads, MetricsCalculator computes win rate/profit factor/Sharpe/drawdown/expectancy**

## Performance

- **Duration:** 3 min
- **Started:** 2026-02-17T16:29:02Z
- **Completed:** 2026-02-17T16:32:02Z
- **Tasks:** 2
- **Files created:** 3

## Accomplishments
- TradeSimulator correctly determines SL_HIT, TP1_HIT, TP2_HIT, EXPIRED outcomes for BUY and SELL trades
- SL takes priority over TP when both could be hit in the same bar (conservative assumption)
- SessionSpreadModel returns session-appropriate spreads (overlap 2 pips, london/NY 3 pips, asian 5 pips)
- MetricsCalculator computes all 5 required metrics with edge case handling (empty, single, all-win, all-loss)
- All Decimal outputs compatible with DB Numeric(10,4) columns

## Task Commits

Each task was committed atomically:

1. **Task 1: TradeSimulator with SimulatedTrade and TradeOutcome** - `edd6764` (feat)
2. **Task 2: SessionSpreadModel and MetricsCalculator** - `fcb3ab6` (feat)

## Files Created/Modified
- `app/services/trade_simulator.py` - TradeSimulator class, SimulatedTrade dataclass, TradeOutcome enum; walks signals through OHLC bars
- `app/services/spread_model.py` - SessionSpreadModel with session-aware spread lookup via get_active_sessions()
- `app/services/metrics_calculator.py` - MetricsCalculator class, BacktestMetrics dataclass; computes 5 performance metrics

## Decisions Made
- SL always takes priority over TP when both could hit in same bar (conservative backtesting assumption)
- XAUUSD pip value defined as $0.10 price movement (industry standard)
- MAX_BARS_FORWARD = 72 bars (3 days at H1 timeframe) before trade expires
- Spread model returns tightest (minimum) spread when multiple sessions overlap
- profit_factor capped at 9999.9999 to fit Numeric(10,4) DB column
- Sharpe ratio returns 0 for fewer than 2 trades (cannot compute standard deviation)
- BUY entry adjusted up by spread (buying at ask), SELL SL checked against high + spread (ask side)

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered

None.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness
- All three backtesting components ready for BacktestRunner orchestration (Plan 03-02)
- TradeSimulator.simulate_signals() provides batch interface for BacktestRunner
- MetricsCalculator.compute() accepts SimulatedTrade list directly from simulator
- No vectorbt dependency - pure pandas/stdlib implementation

---
*Phase: 03-backtesting-engine*
*Completed: 2026-02-17*
