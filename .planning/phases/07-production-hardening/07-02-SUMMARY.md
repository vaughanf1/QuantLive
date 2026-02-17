---
phase: 07-production-hardening
plan: 02
subsystem: infra
tags: [data-retention, failure-tracking, telegram, scheduler, operational-monitoring]

# Dependency graph
requires:
  - phase: 05-delivery-and-visibility
    provides: TelegramNotifier with signal/outcome/degradation messaging
  - phase: 01-data-foundation
    provides: Candle model, APScheduler jobs, database session factory
provides:
  - DataRetentionService with configurable per-timeframe candle pruning
  - FailureTracker with consecutive failure counting and threshold alerts
  - Telegram system alert and health digest methods
  - Scheduled retention job (03:00 UTC) and health digest job (06:00 UTC)
  - Failure tracking wired into all existing jobs
affects: [07-production-hardening]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Class-level state for FailureTracker (single-process with MemoryJobStore)"
    - "SQLAlchemy delete() for bulk retention pruning (no raw SQL)"
    - "Additive failure tracking pattern: wrap existing try/except without changing logic"

key-files:
  created:
    - app/services/data_retention.py
    - app/services/failure_tracker.py
  modified:
    - app/services/telegram_notifier.py
    - app/workers/jobs.py
    - app/workers/scheduler.py

key-decisions:
  - "Class-level state for FailureTracker (consistent with circuit breaker pattern from 06-03)"
  - "SQLAlchemy delete() for retention (not raw SQL) for type safety and model consistency"
  - "Health digest collects candle counts per timeframe, active signals, today's outcomes, and job failure counts"
  - "Only M15 and H1 candles pruned; H4/D1/signals/outcomes explicitly excluded"

patterns-established:
  - "Failure tracking pattern: record_success in try block, record_failure + should_alert in except block"
  - "System alert pattern: notify_system_alert(title, details) for operational notifications"

# Metrics
duration: 3min
completed: 2026-02-17
---

# Phase 7 Plan 02: Data Retention, Failure Tracking, and Health Digest Summary

**DataRetentionService pruning M15/H1/backtests with FailureTracker alerting after 3+ consecutive failures across all 9 scheduled jobs**

## Performance

- **Duration:** 3 min
- **Started:** 2026-02-17T23:55:02Z
- **Completed:** 2026-02-17T23:58:00Z
- **Tasks:** 2
- **Files modified:** 5

## Accomplishments
- DataRetentionService deletes M15 candles >90d, H1 candles >365d, and backtest results >180d while preserving H4/D1/signals/outcomes
- FailureTracker sends exactly one Telegram system alert per failure streak (3+ consecutive), resets on success
- Health digest job at 06:00 UTC sends Telegram summary with active signals, outcomes, candle counts, and job failure status
- Data retention job at 03:00 UTC (after backtests at 02:00) with failure tracking
- Failure tracking wired additively into refresh_candles, run_signal_scanner, and check_outcomes without changing existing logic

## Task Commits

Each task was committed atomically:

1. **Task 1: DataRetentionService and FailureTracker** - `e2ece6b` (feat)
2. **Task 2: Scheduled jobs and failure tracking wiring** - `2ace04b` (feat)

## Files Created/Modified
- `app/services/data_retention.py` - Retention service with configurable thresholds per timeframe (M15: 90d, H1: 365d, backtests: 180d)
- `app/services/failure_tracker.py` - Consecutive failure counter per job with threshold alerting (3+) and one-alert-per-streak logic
- `app/services/telegram_notifier.py` - Added format_system_alert, notify_system_alert, format_health_digest, notify_health_digest methods
- `app/workers/jobs.py` - Added run_data_retention, send_health_digest jobs; wired FailureTracker into refresh_candles, run_signal_scanner, check_outcomes
- `app/workers/scheduler.py` - Registered run_data_retention (03:00 UTC) and send_health_digest (06:00 UTC); updated job count to 9

## Decisions Made
- Class-level state for FailureTracker (consistent with circuit breaker pattern from 06-03, single-process with MemoryJobStore)
- SQLAlchemy delete() for retention (not raw SQL) for type safety and model consistency
- Health digest collects candle counts per timeframe, active signals, today's outcomes, and job failure counts
- Only M15 and H1 candles pruned; H4/D1/signals/outcomes explicitly excluded from retention

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered

None

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness
- All 9 scheduled jobs registered with correct cron triggers
- Failure tracking provides operational visibility for any job degradation
- Health digest gives daily operational summary via Telegram
- Ready for remaining production hardening plans (deployment, status endpoint)

---
*Phase: 07-production-hardening*
*Completed: 2026-02-17*
