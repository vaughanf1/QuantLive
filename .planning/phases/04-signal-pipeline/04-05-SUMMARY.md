---
phase: 04-signal-pipeline
plan: 05
subsystem: pipeline
tags: [orchestrator, scheduler, apscheduler, signal-pipeline, integration-tests]

# Dependency graph
requires:
  - phase: 04-01
    provides: "StrategySelector with composite scoring and H4 confluence"
  - phase: 04-02
    provides: "SignalGenerator with generate, validate, expire, compute_expiry"
  - phase: 04-03
    provides: "RiskManager with check(), position sizing, daily loss limit"
  - phase: 04-04
    provides: "GoldIntelligence with session enrichment and DXY correlation"
provides:
  - "SignalPipeline orchestrator composing all 4 services into sequential flow"
  - "run_signal_scanner() hourly job with stale data guard"
  - "Scheduler with 6 registered jobs (4 candle + 1 backtest + 1 scanner)"
  - "7 integration tests verifying pipeline orchestration logic"
affects: [05-api-signals, 06-outcome-tracking, 07-deployment]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Pipeline orchestrator pattern: compose services sequentially with early-exit on empty results"
    - "Stale data guard via module-level timestamp comparison (SIG-08)"
    - "AsyncMock-based pipeline testing without database"

key-files:
  created:
    - "app/services/signal_pipeline.py"
    - "tests/test_signal_pipeline.py"
  modified:
    - "app/workers/jobs.py"
    - "app/workers/scheduler.py"

key-decisions:
  - "Module-level _last_scanned_ts for stale data guard (simplest approach, no DB query needed)"
  - "Pipeline does not catch exceptions -- clean separation where job handles all error recovery"
  - "Position size appended to reasoning string (no separate metadata column needed)"

patterns-established:
  - "Pipeline orchestrator: early-exit pattern at each stage (no strategy -> return, no candidates -> return, etc.)"
  - "H4 confluence boost applied after risk check but before enrichment"

# Metrics
duration: 3min
completed: 2026-02-17
---

# Phase 4 Plan 5: Signal Pipeline Orchestrator Summary

**SignalPipeline orchestrator wiring StrategySelector, SignalGenerator, RiskManager, and GoldIntelligence into hourly scheduled pipeline with stale data guard and 7 integration tests**

## Performance

- **Duration:** 3 min
- **Started:** 2026-02-17T17:43:09Z
- **Completed:** 2026-02-17T17:46:11Z
- **Tasks:** 2
- **Files modified:** 4

## Accomplishments
- SignalPipeline orchestrator (202 lines) composing all 4 services in correct order: expire -> select -> generate -> validate -> risk -> H4 boost -> enrich -> persist
- run_signal_scanner() job with stale H1 candle guard preventing duplicate processing (SIG-08)
- Scheduler updated to 6 registered jobs with scanner at :02 every hour
- 7 integration tests covering all pipeline flow paths with full mocking

## Task Commits

Each task was committed atomically:

1. **Task 1: SignalPipeline orchestrator and scanner job** - `519ad50` (feat)
2. **Task 2: Pipeline integration tests** - `c036c3b` (test)

## Files Created/Modified
- `app/services/signal_pipeline.py` - Pipeline orchestrator composing all 4 services into sequential flow with signal persistence
- `app/workers/jobs.py` - Added run_signal_scanner() with stale data guard and module-level timestamp tracking
- `app/workers/scheduler.py` - Added scanner job registration at :02 every hour (6 total jobs)
- `tests/test_signal_pipeline.py` - 7 integration tests verifying pipeline orchestration without database

## Decisions Made
- Module-level `_last_scanned_ts` for stale data guard: simplest approach that avoids extra DB queries; reset on process restart is acceptable (scanner will just run once)
- Pipeline does not catch exceptions internally: clean separation where the job handler wraps all work in try/except (consistent with run_daily_backtests pattern)
- Position size appended to reasoning string rather than a separate column: keeps Signal model unchanged while preserving the information for audit

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered

None.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness
- Phase 4 (Signal Pipeline) is now COMPLETE with all 5 plans delivered
- All services (StrategySelector, SignalGenerator, RiskManager, GoldIntelligence) are wired into the SignalPipeline orchestrator
- Scheduler runs 6 jobs: 4 candle refresh + 1 daily backtest + 1 hourly signal scanner
- Ready for Phase 5 (API & Signals) to expose signals via REST endpoints

---
*Phase: 04-signal-pipeline*
*Completed: 2026-02-17*
