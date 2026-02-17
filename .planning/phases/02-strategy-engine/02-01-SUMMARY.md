---
phase: 02-strategy-engine
plan: 01
subsystem: strategies
tags: [pandas-ta, scipy, pydantic, abc, registry, indicators, swing-detection, session-filter, market-structure]

# Dependency graph
requires:
  - phase: 01-data-foundation
    provides: Candle and Signal ORM models with Numeric(10,2) precision
provides:
  - BaseStrategy ABC with __init_subclass__ auto-registry
  - CandidateSignal Pydantic model aligned with Signal DB schema
  - Direction enum, InsufficientDataError, candles_to_dataframe utility
  - Helper modules for EMA/ATR/VWAP/RSI, swing detection, session filtering, BOS/CHoCH
affects: [02-02, 02-03, 04-signal-pipeline]

# Tech tracking
tech-stack:
  added: [pandas-ta-classic 0.3.59, pandas 3.0.0, numpy 2.4.2, scipy 1.17.0]
  patterns: [ABC with __init_subclass__ registry, Pydantic validation for strategy output, thin indicator wrappers]

key-files:
  created:
    - app/strategies/__init__.py
    - app/strategies/base.py
    - app/strategies/helpers/__init__.py
    - app/strategies/helpers/indicators.py
    - app/strategies/helpers/swing_detection.py
    - app/strategies/helpers/session_filter.py
    - app/strategies/helpers/market_structure.py
  modified:
    - requirements.txt

key-decisions:
  - "pandas_ta_classic import name (not pandas_ta) for pandas-ta-classic package"
  - "Class attributes for name/required_timeframes/min_candles instead of abstract properties"
  - "detect_bos as alias for detect_structure_shift for plan artifact compatibility"

patterns-established:
  - "BaseStrategy ABC: concrete strategies define name, required_timeframes, min_candles as class attributes and implement analyze()"
  - "CandidateSignal: all strategy outputs go through Pydantic validation before persistence"
  - "Helper wrappers: thin functions around pandas-ta/scipy, no added logic"

# Metrics
duration: 5min
completed: 2026-02-17
---

# Phase 2 Plan 1: Strategy Engine Foundation Summary

**BaseStrategy ABC with __init_subclass__ auto-registry, CandidateSignal Pydantic model, and helper toolkit (EMA/ATR/VWAP/RSI, swing detection, session filter, BOS/CHoCH)**

## Performance

- **Duration:** 5 min
- **Started:** 2026-02-17T14:59:13Z
- **Completed:** 2026-02-17T15:04:17Z
- **Tasks:** 2
- **Files modified:** 8

## Accomplishments
- BaseStrategy ABC with `__init_subclass__` auto-registry that registers concrete strategies by their `name` class attribute
- CandidateSignal Pydantic model with Decimal fields aligned to Signal DB model (entry_price, stop_loss, take_profit_1, take_profit_2, risk_reward, confidence)
- Four helper modules: pandas-ta indicator wrappers (EMA, ATR, VWAP, RSI), scipy swing detection, UTC session filtering (Asian/London/NY/overlap), and market structure shift detection (BOS/CHoCH)
- Dependencies installed: pandas-ta-classic, pandas, numpy, scipy

## Task Commits

Each task was committed atomically:

1. **Task 1: BaseStrategy ABC, CandidateSignal model, registry, and dependencies** - `f9eb3fb` (feat)
2. **Task 2: Strategy helper modules (indicators, swing detection, session filter, market structure)** - `067749c` (feat)

## Files Created/Modified
- `requirements.txt` - Added pandas-ta-classic, pandas, numpy, scipy
- `app/strategies/__init__.py` - Package init re-exporting base classes
- `app/strategies/base.py` - BaseStrategy ABC, CandidateSignal, Direction, InsufficientDataError, candles_to_dataframe
- `app/strategies/helpers/__init__.py` - Helpers package re-exports
- `app/strategies/helpers/indicators.py` - EMA, ATR, VWAP, RSI wrappers around pandas-ta-classic
- `app/strategies/helpers/swing_detection.py` - Swing high/low detection via scipy argrelextrema
- `app/strategies/helpers/session_filter.py` - Forex session time windows (Asian/London/NY/overlap)
- `app/strategies/helpers/market_structure.py` - BOS and CHoCH detection

## Decisions Made
- Used `pandas_ta_classic` as import name (the pandas-ta-classic package uses this module name, not `pandas_ta`)
- Used class attributes instead of `@abstractmethod @property` for name/required_timeframes/min_candles -- simpler pattern, concrete strategies just set class-level values
- Added `detect_bos` as alias for `detect_structure_shift` to match plan artifact export spec

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Fixed pandas-ta-classic import name**
- **Found during:** Task 2 (indicators module)
- **Issue:** Plan specified `import pandas_ta as ta` but pandas-ta-classic uses module name `pandas_ta_classic`
- **Fix:** Changed import to `import pandas_ta_classic as ta`
- **Files modified:** app/strategies/helpers/indicators.py
- **Verification:** All indicator functions import and compute correctly
- **Committed in:** 067749c (Task 2 commit)

---

**Total deviations:** 1 auto-fixed (1 blocking)
**Impact on plan:** Necessary fix for correct package import. No scope creep.

## Issues Encountered
None beyond the import name fix documented above.

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- Strategy engine foundation complete, ready for concrete strategy implementations (02-02, 02-03)
- All helper functions tested with synthetic data
- Registry pattern ready to auto-register strategies as they are defined

---
*Phase: 02-strategy-engine*
*Completed: 2026-02-17*
