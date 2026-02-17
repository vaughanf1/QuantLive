---
phase: 03-backtesting-engine
verified: 2026-02-17T17:00:00Z
status: passed
score: 5/5 must-haves verified
---

# Phase 3: Backtesting Engine Verification Report

**Phase Goal:** The system can evaluate any strategy's historical performance using rolling-window backtests and produce reliable metrics that account for transaction costs
**Verified:** 2026-02-17T17:00:00Z
**Status:** passed
**Re-verification:** No — initial verification

## Goal Achievement

### Observable Truths

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | System backtests each strategy on rolling 30-day and 60-day windows and calculates win rate, profit factor, Sharpe ratio, max drawdown, and expectancy | VERIFIED | `BacktestRunner.run_all_strategies()` iterates `window_days_list=[30, 60]` calling `run_full_backtest()` which calls `MetricsCalculator.compute()` returning all 5 metrics as Decimal |
| 2 | Backtester uses the exact same strategy analyze() code path as live signal generation — no separate backtest-only strategy implementations exist | VERIFIED | `run_rolling_backtest()` calls `strategy.analyze(window)` at line 87 of backtester.py. No `backtest_analyze`, `analyze_historical`, or `_backtest_only` methods found in any strategy file. Test `test_rolling_backtest_uses_analyze` confirms `analyze.call_count >= 1`. |
| 3 | Walk-forward validation splits data 80/20 and flags strategies that perform significantly worse on out-of-sample data | VERIFIED | `WalkForwardValidator.validate()` splits at `int(len(candles) * 0.8)`, computes WFE ratios (OOS/IS), sets `is_overfitted=True` when either ratio < 0.5. Skips detection when OOS trades < 5. All confirmed by 2 dedicated integration tests (both passing). |
| 4 | Backtest results account for session-appropriate spread costs (not zero-spread assumptions) | VERIFIED | `SessionSpreadModel.get_spread()` calls `get_active_sessions()` from strategy helper and returns: overlap=0.20, london/NY=0.30, asian=0.50, off-session=0.50 (conservative default). `BacktestRunner` calls `self.spread_model.get_spread(signal.timestamp)` for every signal. Live test confirmed: overlap→Decimal('0.20'), asian→Decimal('0.50'). |
| 5 | Backtests run as scheduled background jobs and all results are persisted to the database with timestamps and parameters | VERIFIED | `run_daily_backtests()` is an async coroutine registered via `CronTrigger(hour=2, minute=0, timezone="UTC")` in `register_jobs()`. Job persists `BacktestResult` rows with all 12 fields (win_rate, profit_factor, sharpe_ratio, max_drawdown, expectancy, total_trades, is_walk_forward, is_overfitted, walk_forward_efficiency, spread_model, start_date, end_date). `await session.commit()` called after all rows are added. |

**Score:** 5/5 truths verified

---

### Required Artifacts

| Artifact | Lines | Exists | Substantive | Wired | Status |
|----------|-------|--------|-------------|-------|--------|
| `app/services/trade_simulator.py` | 201 | YES | YES | YES — imported by backtester.py, metrics_calculator.py, all test files | VERIFIED |
| `app/services/spread_model.py` | 60 | YES | YES | YES — imported by backtester.py; delegates to session_filter.get_active_sessions() | VERIFIED |
| `app/services/metrics_calculator.py` | 145 | YES | YES | YES — imported by backtester.py, walk_forward.py, test files | VERIFIED |
| `app/services/backtester.py` | 187 | YES | YES | YES — imported by walk_forward.py and jobs.py | VERIFIED |
| `app/services/walk_forward.py` | 170 | YES | YES | YES — imported by jobs.py | VERIFIED |
| `app/workers/jobs.py` | 236 | YES | YES | YES — imported by scheduler.py | VERIFIED |
| `app/workers/scheduler.py` | 89 | YES | YES | YES — registered in FastAPI app lifespan | VERIFIED |
| `app/models/backtest_result.py` | 52 | YES | YES | YES — imported by jobs.py; all 4 walk-forward columns present | VERIFIED |
| `alembic/versions/2026_02_17_4854af26a1fe_add_walk_forward_fields_to_backtest_.py` | 38 | YES | YES | YES — migration adds is_walk_forward, is_overfitted, walk_forward_efficiency, spread_model | VERIFIED |
| `tests/test_trade_simulator.py` | 267 | YES | YES (9 tests) | YES — collected and passing | VERIFIED |
| `tests/test_metrics_calculator.py` | 145 | YES | YES (6 tests) | YES — collected and passing | VERIFIED |
| `tests/test_backtester.py` | 218 | YES | YES (5 tests) | YES — collected and passing | VERIFIED |

---

### Key Link Verification

| From | To | Via | Status |
|------|----|-----|--------|
| `trade_simulator.py` | `app/strategies/base.py` | `from app.strategies.base import CandidateSignal, Direction` | WIRED |
| `spread_model.py` | `app/strategies/helpers/session_filter.py` | `from app.strategies.helpers.session_filter import get_active_sessions` | WIRED |
| `metrics_calculator.py` | `trade_simulator.py` | `from app.services.trade_simulator import SimulatedTrade, TradeOutcome` | WIRED |
| `backtester.py` | `trade_simulator.py` | `from app.services.trade_simulator import SimulatedTrade, TradeSimulator` | WIRED |
| `backtester.py` | `spread_model.py` | `from app.services.spread_model import SessionSpreadModel` | WIRED |
| `backtester.py` | `app/strategies/base.py` | `strategy.analyze(window)` called inside `run_rolling_backtest()` | WIRED |
| `walk_forward.py` | `backtester.py` | `from app.services.backtester import BacktestRunner` | WIRED |
| `walk_forward.py` | `metrics_calculator.py` | `from app.services.metrics_calculator import BacktestMetrics` | WIRED |
| `jobs.py` | `backtester.py` | `BacktestRunner()` instantiated; `runner.run_full_backtest()` called | WIRED |
| `jobs.py` | `walk_forward.py` | `WalkForwardValidator(runner=runner)` instantiated; `wf_validator.validate()` called | WIRED |
| `jobs.py` | `backtest_result.py` | `BacktestResult(...)` created and `session.add()` / `await session.commit()` called | WIRED |
| `scheduler.py` | `jobs.py` | `from app.workers.jobs import run_daily_backtests`; `CronTrigger(hour=2, minute=0)` | WIRED |

---

### Requirements Coverage

| Requirement | Truth | Status |
|-------------|-------|--------|
| BACK-01 — Rolling window backtests (30d, 60d) | Truth 1 | SATISFIED |
| BACK-02 — Same analyze() code path as live | Truth 2 | SATISFIED |
| BACK-03 — Walk-forward 80/20 overfitting detection | Truth 3 | SATISFIED |
| BACK-04 — Session-appropriate spread costs | Truth 4 | SATISFIED |
| BACK-05 — 5 metrics: win rate, profit factor, Sharpe, max drawdown, expectancy | Truth 1 | SATISFIED |
| BACK-06 — Scheduled background jobs | Truth 5 | SATISFIED |
| BACK-07 — Results persisted to DB with timestamps and parameters | Truth 5 | SATISFIED |

---

### Anti-Patterns Found

None detected. Grep of all 6 core service files returned zero matches for TODO, FIXME, placeholder, not implemented, coming soon, return null, return {}, return [].

---

### Human Verification Required

None. All success criteria for this phase are structurally verifiable:
- Logic correctness confirmed by 20 passing unit/integration tests
- API call paths confirmed by source inspection
- DB persistence confirmed by ORM model attributes and job source analysis
- Scheduler registration confirmed by CronTrigger source inspection

---

## Detailed Findings

### Truth 1: Rolling-window backtests with 5 metrics

`BacktestRunner.run_rolling_backtest()` slides a window of `window_days * 24` H1 candles across the full dataset in steps of `step_days * 24`. For each position it calls `strategy.analyze(window)`, then simulates each returned signal against bars after the window end using `TradeSimulator.simulate_trade()`. The spread is retrieved from `SessionSpreadModel.get_spread(signal.timestamp)` for each signal individually.

`MetricsCalculator.compute()` produces all 5 required metrics:
- **Win rate:** count of TP1_HIT or TP2_HIT outcomes / total trades
- **Profit factor:** gross profit / gross loss (capped at 9999.9999 for Numeric(10,4) DB compatibility)
- **Sharpe ratio:** `(mean_pnl / std_pnl) * sqrt(252)` — returns 0 for fewer than 2 trades
- **Max drawdown:** largest peak-to-trough decline in cumulative PnL curve (absolute pips)
- **Expectancy:** mean PnL per trade in pips

All outputs are `Decimal(str(round(x, 4)))` at the return boundary, compatible with `Numeric(10, 4)` DB columns.

### Truth 2: Same code path as live signal generation

`run_rolling_backtest()` at line 87 of `backtester.py`:
```python
signals = strategy.analyze(window)
```
This is the identical `BaseStrategy.analyze()` abstract method that all live strategy classes implement. No alternative `backtest_analyze()`, `analyze_historical()`, or `_backtest_only` implementations exist anywhere in the strategy tree. The test `test_rolling_backtest_uses_analyze` uses a `MagicMock(spec=BaseStrategy)` to assert `strategy.analyze.call_count >= 1`.

### Truth 3: Walk-forward 80/20 overfitting detection

`WalkForwardValidator.validate()` splits at `split_idx = int(len(candles) * 0.8)`. It runs `run_full_backtest()` independently on each half. If OOS `total_trades < 5`, it returns with `insufficient_oos_trades=True` and `is_overfitted=False` (no noisy detection). Otherwise it computes:
- `wfe_win_rate = oos_win_rate / is_win_rate`
- `wfe_profit_factor = oos_pf / is_pf`

Sets `is_overfitted=True` if either ratio < `DEGRADATION_THRESHOLD` (0.5). Both integration tests exercise these paths and pass.

### Truth 4: Session-appropriate spread costs

`SessionSpreadModel.get_spread()` calls `get_active_sessions(timestamp)` from `app.strategies.helpers.session_filter` (no duplicated session time logic). Spread map:
- Overlap (London + NY): Decimal("0.20") — 2 pips
- London or NY: Decimal("0.30") — 3 pips
- Asian: Decimal("0.50") — 5 pips
- Off-session: Decimal("0.50") — conservative default

When multiple sessions overlap, `min(spreads)` is returned (tightest spread wins). Live verification confirmed: 14:00 UTC → 0.20, 03:00 UTC → 0.50.

BUY entry is adjusted upward by spread (buying at ask). SELL SL is checked against `bar_high + spread` (ask-side stop-out).

### Truth 5: Scheduled jobs and DB persistence

`run_daily_backtests()` is:
- An `async def` coroutine (confirmed by `inspect.iscoroutinefunction`)
- Registered with `CronTrigger(hour=2, minute=0, timezone="UTC")` in `register_jobs()`
- Creates its own DB session via `async_session_factory()` (not FastAPI Depends)
- Queries all H1 XAUUSD candles, checks minimum count (30*24+72=792), iterates all registered strategies
- Persists `BacktestResult` rows for 30d and 60d windows, plus walk-forward OOS rows with `is_walk_forward=True`, `is_overfitted`, and `walk_forward_efficiency`
- All rows tagged `spread_model="session_aware"` for audit trail
- Single `await session.commit()` at end of strategy loop
- Wrapped in top-level `try/except` to prevent scheduler crashes

The Alembic migration `4854af26a1fe` adds the 4 walk-forward columns (`is_walk_forward`, `is_overfitted`, `walk_forward_efficiency`, `spread_model`) as nullable, preserving backward compatibility.

---

## Test Suite Summary

**20/20 tests passing** (`python -m pytest tests/test_trade_simulator.py tests/test_metrics_calculator.py tests/test_backtester.py -v`)

| File | Tests | Coverage |
|------|-------|----------|
| `test_trade_simulator.py` | 9 | BUY TP1/TP2/SL, SELL TP1/SL, SL priority over TP, EXPIRED, spread adjustment, no look-ahead bias |
| `test_metrics_calculator.py` | 6 | Empty, all winners, all losers, mixed, single trade (Sharpe=0), max drawdown calculation |
| `test_backtester.py` | 5 | Instantiation, insufficient data, analyze() call verification (BACK-02), OOS insufficient trades, overfitting detection |

---

_Verified: 2026-02-17T17:00:00Z_
_Verifier: Claude (gsd-verifier)_
