---
phase: "06-outcome-tracking-and-feedback"
plan: "03"
subsystem: "feedback-controller"
tags: ["feedback-loop", "degradation", "circuit-breaker", "recovery", "risk-management"]

dependency_graph:
  requires: ["06-02"]
  provides: ["FeedbackController service", "degradation/recovery detection", "circuit breaker", "Telegram alerts for degradation/CB"]
  affects: ["07-01"]

tech_stack:
  added: []
  patterns: ["in-memory circuit breaker state", "lazy imports to avoid circular dependencies", "class-level state for single-process apps"]

key_files:
  created:
    - app/services/feedback_controller.py
    - tests/test_feedback_controller.py
  modified:
    - app/services/telegram_notifier.py
    - app/services/risk_manager.py
    - app/workers/jobs.py

decisions:
  - id: "06-03-01"
    description: "Circuit breaker state stored as class-level attributes (not DB) since app is single-process with MemoryJobStore"
  - id: "06-03-02"
    description: "Lazy import of FeedbackController in RiskManager.check() to avoid circular import (feedback_controller imports RiskManager for drawdown metrics)"
  - id: "06-03-03"
    description: "Drawdown-based circuit breaker uses mocked RiskManager.get_drawdown_metrics in tests to isolate from DB-computed max_drawdown ambiguity"
  - id: "06-03-04"
    description: "Recovery checks 7d StrategyPerformance for recent metrics and 30d row for degradation timestamp"

metrics:
  duration: "6min"
  completed: "2026-02-17"
---

# Phase 06 Plan 03: Feedback Controller Summary

FeedbackController with degradation detection, auto-recovery, and circuit breaker halting signal generation during losing streaks or excessive drawdown

## What Was Built

### FeedbackController Service (`app/services/feedback_controller.py`)
- **Degradation detection**: Compares live 30d StrategyPerformance against oldest non-walk-forward BacktestResult baseline. Flags strategies where win rate drops >15% below baseline or profit factor falls below 1.0. Persists `is_degraded` flag to DB.
- **Auto-recovery**: After 7+ days of degradation, checks 7d metrics. If win rate recovers to within 5% of baseline and profit factor >= 1.0, clears degradation flag on all performance rows.
- **Circuit breaker**: Activates on 5+ consecutive losses (sl_hit or expired) OR when current drawdown exceeds 2x historical max drawdown. Auto-resets after 24-hour cooldown. Consecutive loss count resets when a win is detected.
- **In-memory state**: Circuit breaker uses class-level `_circuit_breaker_active` and `_circuit_breaker_triggered_at` attributes, acceptable for single-process app with MemoryJobStore.

### TelegramNotifier Extensions (`app/services/telegram_notifier.py`)
- `notify_degradation(strategy_name, reason, is_recovery)` -- fire-and-forget degradation/recovery alerts
- `notify_circuit_breaker(reason, active)` -- fire-and-forget circuit breaker activation/reset alerts
- Both follow existing pattern: HTML formatting, never raises, disabled mode support

### RiskManager Integration (`app/services/risk_manager.py`)
- Circuit breaker check added as step 0 in `RiskManager.check()`, before daily loss limit
- Uses lazy import of FeedbackController to avoid circular dependency
- When circuit breaker active, ALL candidates rejected with clear reason string

### Job Wiring (`app/workers/jobs.py`)
- `check_outcomes()` now runs FeedbackController checks after every outcome batch
- Collects unique strategy IDs from detected outcomes, runs degradation/recovery per strategy
- Sends Telegram notifications for degradation and recovery events
- Runs circuit breaker check (global, not per-strategy)

## Test Results

12 tests covering all scenarios:
- 4 degradation tests (low win rate, low PF, healthy strategy, DB persistence)
- 2 recovery tests (after 7+ days, before 7 days)
- 6 circuit breaker tests (consecutive losses, drawdown exceeded, not triggered, 24h cooldown, win reset, consecutive count)

Full suite: 137 passed, 1 failed (pre-existing `test_pipeline_risk_rejects_all` mock issue)

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Test database schema out of date**
- **Found during:** Task 1 (RED phase)
- **Issue:** `backtest_results` table in test DB missing `is_walk_forward` column (stale schema)
- **Fix:** Changed `_ensure_tables()` to drop_all/create_all instead of just create_all
- **Files modified:** `tests/test_feedback_controller.py`

**2. [Rule 1 - Bug] Removed incomplete duplicate test**
- **Found during:** Task 1 (GREEN phase)
- **Issue:** Draft `test_circuit_breaker_drawdown_exceeded` had `pass` body alongside proper `_v2` version
- **Fix:** Removed incomplete test, renamed `_v2` to be the canonical test
- **Files modified:** `tests/test_feedback_controller.py`

## Decisions Made

1. **In-memory circuit breaker state**: Class-level attributes rather than DB storage. Single-process app with MemoryJobStore makes this safe and simple. State resets on restart (acceptable -- circuit breaker is a safety mechanism, not persistent state).

2. **Lazy import pattern**: `FeedbackController` imports `RiskManager` for drawdown metrics. `RiskManager.check()` imports `FeedbackController` for circuit breaker check. Lazy import in `RiskManager.check()` breaks the cycle.

3. **Drawdown comparison approach**: Circuit breaker compares `running_drawdown > 2 * max_drawdown` from `RiskManager.get_drawdown_metrics()`. Tests mock these values to isolate logic from DB computation.

4. **Recovery metric source**: Uses 7d StrategyPerformance for recent metrics, 30d row's `calculated_at` for degradation duration check.

## Next Phase Readiness

Phase 6 is now complete. All three plans delivered:
- 06-01: OutcomeDetector (signal outcome tracking)
- 06-02: PerformanceTracker (rolling metrics + live blending)
- 06-03: FeedbackController (degradation, recovery, circuit breaker)

The complete feedback loop is operational: outcomes are detected, performance is tracked, degrading strategies are auto-deprioritized, and the circuit breaker halts signal generation during losing streaks. Phase 7 (Production Deployment) can proceed.
