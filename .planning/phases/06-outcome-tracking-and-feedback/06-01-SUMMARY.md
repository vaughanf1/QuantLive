---
phase: "06-outcome-tracking-and-feedback"
plan: "01"
subsystem: "outcome-detection"
tags: ["outcome-detector", "price-polling", "spread-accounting", "apscheduler", "twelve-data"]

dependency_graph:
  requires: ["03-01", "04-02", "05-01"]
  provides: ["OutcomeDetector service", "check_outcomes job", "30-second outcome polling"]
  affects: ["06-02", "06-03"]

tech_stack:
  added: []
  patterns: ["async service with retry", "pure logic separation from I/O", "IntervalTrigger for sub-minute scheduling"]

key_files:
  created:
    - "app/services/outcome_detector.py"
    - "tests/test_outcome_detector.py"
  modified:
    - "app/workers/jobs.py"
    - "app/workers/scheduler.py"

decisions:
  - id: "06-01-01"
    description: "Pure _evaluate_signal method separated from async I/O for testability"
  - id: "06-01-02"
    description: "Outcome exit_price uses current bid price (not the SL/TP level) for accurate PnL"
  - id: "06-01-03"
    description: "Debug-level logging for no-outcome checks to avoid log spam at 30s interval"

metrics:
  duration: "3min"
  completed: "2026-02-17"
  tests_added: 18
  tests_passing: 18
---

# Phase 6 Plan 1: Outcome Detector Service Summary

**One-liner:** OutcomeDetector polls XAUUSD price every 30s via Twelve Data /price endpoint, evaluates active signals against SL/TP/expiry with spread accounting, records outcomes, and dispatches Telegram notifications.

## What Was Built

### OutcomeDetector Service (`app/services/outcome_detector.py`)
- **Price fetching:** Async httpx client calls Twelve Data `/price?symbol=XAU/USD` with tenacity retry (3 attempts, exponential backoff)
- **Signal evaluation:** Pure `_evaluate_signal` method checks expiry, SL, TP2, TP1 in priority order
- **Spread accounting (TRACK-05):** BUY SL checked against bid; SELL SL checked against ask (bid + spread)
- **SL priority (decision 03-01):** SL always wins over TP when both trigger in same check
- **TP2 priority:** TP2 checked before TP1 to catch price jumps past both levels
- **PnL calculation:** BUY = (exit - entry) / 0.10; SELL = (entry - exit) / 0.10
- **Duration tracking:** (now - created_at) in whole minutes
- **Outcome recording:** Creates Outcome row, updates Signal.status, commits transaction

### Job Wiring (`app/workers/jobs.py`)
- `check_outcomes()` async job function following existing try/except pattern
- Instantiates OutcomeDetector with Twelve Data API key from settings
- Sends Telegram notifications for each detected outcome via TelegramNotifier
- Moved TelegramNotifier import to module-level (was inline in run_signal_scanner)

### Scheduler Registration (`app/workers/scheduler.py`)
- Added `IntervalTrigger(seconds=30)` for check_outcomes job
- `max_instances=1` and `coalesce=True` prevent overlapping checks
- Updated total job count from 6 to 7

### Test Coverage (`tests/test_outcome_detector.py`)
18 tests across 3 test classes:
- **TestEvaluateSignal (8 tests):** BUY SL, BUY TP1, BUY TP2, SELL SL with spread, SELL TP1, expired, SL priority over TP, no outcome between levels
- **TestPnlAndDuration (4 tests):** BUY profit, SELL profit, BUY loss, duration minutes
- **TestCheckOutcomesAsync (6 tests):** No active signals, price fetch failure, status updated, outcome fields populated, SELL TP2, expired uses current price

## Decisions Made

| ID | Decision | Rationale |
|----|----------|-----------|
| 06-01-01 | Pure _evaluate_signal separated from I/O | Enables fast unit testing of all evaluation logic without mocking DB or HTTP |
| 06-01-02 | exit_price = current bid (not SL/TP level) | More accurate PnL -- real exit would be at market price, not exact level |
| 06-01-03 | Debug-level logging for no-outcome checks | Job runs every 30s -- info-level would create 2880 log entries/day with no signal activity |

## Deviations from Plan

None -- plan executed exactly as written.

## Commit History

| Commit | Type | Description |
|--------|------|-------------|
| 5b61307 | feat | OutcomeDetector service + 18 unit tests (TDD RED+GREEN) |
| 7f8e8ba | feat | check_outcomes job + 30-second IntervalTrigger scheduler |

## Next Phase Readiness

### Available for 06-02 (Performance Stats API)
- Outcomes table populated with result, exit_price, pnl_pips, duration_minutes
- Signal.status updated on outcome detection (tp1_hit, tp2_hit, sl_hit, expired)
- Can query outcomes table for win rate, average PnL, strategy performance

### Available for 06-03 (Adaptive Confidence)
- Outcome data flows into feedback loop for strategy scoring adjustments
- result field matches trade_simulator.py outcome enum values (lowercase)
