---
phase: 04-signal-pipeline
plan: 03
subsystem: risk-management
tags: [risk, position-sizing, atr, drawdown, capital-protection]

# Dependency graph
requires:
  - phase: 01-data-foundation
    provides: Signal and Outcome models for DB queries
  - phase: 02-strategy-engine
    provides: CandidateSignal model for risk check input
provides:
  - RiskManager service with check(), calculate_position_size(), get_drawdown_metrics()
  - RiskCheckResult dataclass for approved/rejected signal decisions
  - Risk configuration constants (RISK_PER_TRADE, MAX_CONCURRENT_SIGNALS, DAILY_LOSS_LIMIT_PCT)
  - account_balance setting in Settings (100K default)
affects: [04-signal-pipeline remaining plans, 05-monitoring, 06-notifications, 07-deployment]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "ATR-adjusted position sizing with floor/cap clamping"
    - "DB-derived daily P&L (no persistent counter) for stale state avoidance"
    - "Ordered risk checks: daily loss -> concurrent limit -> position sizing"

key-files:
  created:
    - app/services/risk_manager.py
  modified:
    - app/config.py

key-decisions:
  - "Daily P&L derived from DB each check (not cached) to avoid stale state across days"
  - "ATR factor = baseline/current with 0.5x-1.5x clamp for volatility adjustment"
  - "PIP_VALUE = 0.10 for XAUUSD (consistent with 03-01 backtester decision)"
  - "Default position sizing in check() uses 1.0/1.0 ATR until SignalPipeline provides real values"

patterns-established:
  - "RiskCheckResult dataclass for structured approve/reject responses"
  - "Sequential risk gate pattern: fail-fast on daily loss, then per-candidate checks"

# Metrics
duration: 5min
completed: 2026-02-17
---

# Phase 4 Plan 3: Risk Manager Summary

**RiskManager service with 1% per-trade risk, 2-concurrent signal cap, 2% daily loss suppression, and ATR-adjusted position sizing (0.5x-1.5x)**

## Performance

- **Duration:** 5 min
- **Started:** 2026-02-17T17:34:39Z
- **Completed:** 2026-02-17T17:39:31Z
- **Tasks:** 2
- **Files modified:** 2

## Accomplishments
- Settings.account_balance added with 100K default for prop firm trading
- RiskManager enforces 1% per-trade risk, 2 concurrent signal cap, 2% daily loss limit
- Volatility-adjusted position sizing using ATR factor with 0.5x floor and 1.5x cap
- Running and maximum drawdown metrics computed from historical outcomes
- Edge case handling for zero/negative SL distance and ATR inputs

## Task Commits

Each task was committed atomically:

1. **Task 1: Add account_balance to Settings** - `a826930` (feat)
2. **Task 2: RiskManager service with position sizing, limits, and drawdown** - `e1dd3ef` (feat)

## Files Created/Modified
- `app/config.py` - Added account_balance field (100K default, ACCOUNT_BALANCE env var)
- `app/services/risk_manager.py` - RiskManager class with check(), calculate_position_size(), get_drawdown_metrics(), RiskCheckResult dataclass, and risk constants

## Decisions Made
- Daily P&L derived from DB each check (not cached counter) to avoid stale state across UTC day boundaries
- ATR factor computed as baseline_atr / current_atr, clamped to [0.5, 1.5] -- higher volatility reduces position size
- PIP_VALUE set to $0.10 for XAUUSD, consistent with backtester (03-01)
- check() method uses 1.0/1.0 default ATR values; SignalPipeline will provide real ATR when integrated
- Risk checks ordered: daily loss first (global gate), then concurrent limit, then position sizing per candidate

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered
None

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- RiskManager ready for integration into SignalPipeline (Plan 04-05)
- check() method accepts CandidateSignal list and returns approved/rejected results
- Position sizing will need real ATR values from candle data when SignalPipeline is built
- All risk constants are module-level for easy override/testing

---
*Phase: 04-signal-pipeline*
*Completed: 2026-02-17*
