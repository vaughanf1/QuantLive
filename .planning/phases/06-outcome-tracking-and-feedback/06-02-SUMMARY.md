---
phase: "06-outcome-tracking-and-feedback"
plan: "02"
subsystem: "performance-tracking"
tags: ["performance-tracker", "rolling-metrics", "live-blending", "strategy-selector"]

dependency_graph:
  requires: ["06-01"]
  provides: ["PerformanceTracker service", "live metric blending in StrategySelector"]
  affects: ["06-03"]

tech_stack:
  added: []
  patterns: ["upsert pattern for strategy_performance rows", "rolling window metric aggregation", "backtest/live score blending"]

key_files:
  created:
    - "app/services/performance_tracker.py"
    - "tests/test_performance_tracker.py"
  modified:
    - "app/services/outcome_detector.py"
    - "app/services/strategy_selector.py"

decisions:
  - id: "06-02-01"
    description: "Profit factor capped at 9999.9999 for Numeric(10,4) DB compatibility (consistent with 03-01)"
  - id: "06-02-02"
    description: "Live blending weight: 70% backtest + 30% live, only when >= 5 live signals exist"
  - id: "06-02-03"
    description: "Live score weights: 0.40 * win_rate + 0.35 * profit_factor_norm + 0.25 * avg_rr_norm"
  - id: "06-02-04"
    description: "PerformanceTracker triggered only on new outcomes (not on every 30s check)"
  - id: "06-02-05"
    description: "is_degraded field left False in PerformanceTracker -- FeedbackController manages it in 06-03"

metrics:
  duration: "3min"
  completed: "2026-02-17"
---

# Phase 6 Plan 2: Performance Tracker Summary

**Rolling 7d/30d performance metrics with live blending into strategy selection scoring**

## What Was Built

### PerformanceTracker Service (`app/services/performance_tracker.py`)
- Computes rolling 7-day and 30-day performance metrics per strategy from live outcomes
- Metrics: win_rate (tp1/tp2 as wins), profit_factor (gross_profit/gross_loss), avg_rr (from signal risk_reward)
- Upsert logic: updates existing StrategyPerformance rows, never creates duplicates
- Edge cases: no outcomes -> zeros, all wins -> profit_factor capped at 9999.9999

### OutcomeDetector Integration (`app/services/outcome_detector.py`)
- After recording outcomes and committing, iterates affected strategy IDs
- Calls `recalculate_for_strategy()` per unique affected strategy
- Exception-safe: logs errors but doesn't crash the outcome detection loop

### StrategySelector Live Blending (`app/services/strategy_selector.py`)
- Fetches latest 30d StrategyPerformance rows after regime modification
- When strategy has >= 5 live signals: blends 70% backtest + 30% live score
- Live score normalization: win_rate direct, profit_factor capped at 3.0, avg_rr capped at 5.0
- Re-sorts after blending to ensure best overall strategy is selected

### Test Suite (`tests/test_performance_tracker.py`)
- 10 database-backed async tests covering all specified cases
- Tests: win_rate calc, profit_factor calc, avg_rr calc, upsert, insert, no outcomes, both periods, cross-strategy isolation, all-wins cap, result classification

## Decisions Made

| ID | Decision | Rationale |
|----|----------|-----------|
| 06-02-01 | profit_factor capped at 9999.9999 | Numeric(10,4) DB column constraint, consistent with 03-01 |
| 06-02-02 | 70/30 backtest/live blend with 5-signal minimum | Prevents noisy live data from dominating when sample is small |
| 06-02-03 | Live weights: 0.40/0.35/0.25 for wr/pf/rr | Win rate most reliable indicator with small samples |
| 06-02-04 | Recalc only on new outcomes | Performance recalculation is expensive; skip on no-outcome 30s checks |
| 06-02-05 | is_degraded managed by 06-03 | Separation of concerns: tracker computes, feedback controller decides |

## Deviations from Plan

None -- plan executed exactly as written.

## Test Results

```
tests/test_performance_tracker.py - 10 passed
tests/test_outcome_detector.py   - 18 passed (existing, still green after wiring)
Total: 28 passed in 1.63s
```

## Next Phase Readiness

06-03 (Feedback Controller) can now:
- Query StrategyPerformance rows populated by PerformanceTracker
- Use the is_degraded field to flag underperforming strategies
- Build on the automatic recalculation loop: Outcome -> PerformanceTracker -> StrategyPerformance -> StrategySelector
