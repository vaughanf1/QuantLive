---
phase: 04-signal-pipeline
plan: 02
subsystem: signal-generation
tags: [signal-generator, validation, dedup, expiry, bias-detection, xauusd]

# Dependency graph
requires:
  - phase: 02-strategy-engine
    provides: "BaseStrategy ABC, CandidateSignal model, strategy registry, candles_to_dataframe"
  - phase: 01-data-foundation
    provides: "Signal and Candle ORM models, async database session"
provides:
  - "SignalGenerator service with generate(), validate(), compute_expiry(), expire_stale_signals()"
  - "Signal validation filters: R:R >= 2.0, confidence >= 65%, 4h dedup, bias detection"
  - "Timeframe-specific signal expiry (M15=4h, H1=8h, H4=24h, D1=48h)"
affects:
  - 04-signal-pipeline (plans 03-05: risk manager, gold intelligence, pipeline orchestrator)
  - 05-telegram-delivery (signal output consumption)

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Lazy strategy imports inside generate() method body (circular import avoidance)"
    - "Float comparison for R:R and confidence thresholds (Decimal->float conversion)"
    - "Pydantic model_copy(update=...) for immutable CandidateSignal modification"

key-files:
  created:
    - app/services/signal_generator.py
  modified: []

key-decisions:
  - "Bias detection is informational only -- appends note to reasoning but does not reject signals"
  - "Dedup checks symbol + direction + active status within 4h window (not strategy-specific)"

patterns-established:
  - "SignalGenerator as stateless service class (no __init__ state) -- instantiate per use"
  - "Validation pipeline: ordered filters with early continue, accumulate passing candidates"
  - "expire_stale_signals() runs before each scanner cycle for cleanup"

# Metrics
duration: 2min
completed: 2026-02-17
---

# Phase 4 Plan 02: Signal Generator Summary

**SignalGenerator service with R:R/confidence filters, 4h dedup window, directional bias detection, and timeframe-specific expiry**

## Performance

- **Duration:** 2 min
- **Started:** 2026-02-17T17:33:53Z
- **Completed:** 2026-02-17T17:35:26Z
- **Tasks:** 1
- **Files created:** 1

## Accomplishments
- SignalGenerator service with generate() that runs strategy.analyze() on latest XAUUSD candles
- Validation pipeline applying R:R >= 2.0, confidence >= 65%, 4-hour dedup, and bias detection filters in order
- Timeframe-specific signal expiry (M15=4h, H1=8h, H4=24h, D1=48h)
- Stale signal cleanup function for pipeline lifecycle management

## Task Commits

Each task was committed atomically:

1. **Task 1: SignalGenerator service with generation and validation** - `72cb77e` (feat)

## Files Created/Modified
- `app/services/signal_generator.py` - SignalGenerator service: generate, validate, dedup, expiry, bias detection (328 lines)

## Decisions Made
- Dedup window checks symbol + direction + active status (not strategy-specific) to prevent any same-direction duplicate within 4 hours regardless of which strategy produced it
- Bias detection appends a note to candidate reasoning but never rejects the signal (informational only per RESEARCH.md recommendation)
- compute_expiry() is separated from validate() because signal persistence happens in the pipeline orchestrator (Plan 05)
- Lazy strategy imports follow the Phase 3 pattern inside generate() method body

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered

None

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness
- SignalGenerator ready for composition in SignalPipeline orchestrator (Plan 05)
- Risk manager (Plan 03) can consume validated candidates from SignalGenerator.validate()
- Gold intelligence (Plan 04) can enrich validated candidates before persistence
- Signal model has expires_at column ready for compute_expiry() output

---
*Phase: 04-signal-pipeline*
*Completed: 2026-02-17*
