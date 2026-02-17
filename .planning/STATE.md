# Project State

## Project Reference

See: .planning/PROJECT.md (updated 2026-02-17)

**Core value:** Deliver 1-2 high-conviction, statistically validated XAUUSD trade signals per day with full automation from generation through outcome tracking.
**Current focus:** Phase 4 - Signal Pipeline (In Progress)

## Current Position

Phase: 4 of 7 (Signal Pipeline)
Plan: 4 of 5 in current phase
Status: In progress
Last activity: 2026-02-17 -- Completed 04-04-PLAN.md (Gold Intelligence Service)

Progress: [#############.......] 59% (13/22 plans)

## Performance Metrics

**Velocity:**
- Total plans completed: 13
- Average duration: 4.3min
- Total execution time: 0.9 hours

**By Phase:**

| Phase | Plans | Total | Avg/Plan |
|-------|-------|-------|----------|
| 01-data-foundation | 3/3 | 24min | 8min |
| 02-strategy-engine | 3/3 | 14min | 4.7min |
| 03-backtesting-engine | 3/3 | 11min | 3.7min |
| 04-signal-pipeline | 4/5 | 8min | 2min |

**Recent Trend:**
- Last 5 plans: 03-03 (5min), 04-01 (2min), 04-02 (2min), 04-03 (2min), 04-04 (2min)
- Trend: accelerating, ~2min/plan

*Updated after each plan completion*

## Accumulated Context

### Decisions

Decisions are logged in PROJECT.md Key Decisions table.
Recent decisions affecting current work:

- [Roadmap]: 7-phase structure derived from 73 requirements across 12 categories
- [Roadmap]: Phase 4 (Signal Pipeline) consolidates strategy selection, signal generation, risk management, and gold intelligence into one vertical slice
- [Roadmap]: Production deployment (Railway) deferred to Phase 7 -- local development through Phases 1-6
- [01-01]: Numeric(10,2) for prices, Numeric(10,4) for metrics -- never Float for financial data
- [01-01]: MemoryJobStore for APScheduler (avoids sync driver dependency)
- [01-01]: asynccontextmanager lifespan instead of deprecated on_event decorators
- [01-01]: lru_cache singleton for Settings; pool_pre_ping=True for connection resilience
- [01-01]: PostgreSQL 17 + Python 3.12 installed via Homebrew for local development
- [01-02]: Synchronous TDClient wrapped with tenacity retry (3 attempts, exponential backoff max 30s)
- [01-02]: SQL literal for interval in generate_series (asyncpg cannot bind string as interval type)
- [01-02]: CronTrigger for precise candle-close alignment with 1-minute offset
- [01-02]: TimeframeEnum for path parameter validation at FastAPI level
- [01-03]: Per-test engine isolation for asyncpg (avoids connection contention with session.commit)
- [01-03]: Table truncation (DELETE) for test isolation instead of transaction rollback
- [01-03]: goldsignal_test as separate test database (configurable via TEST_DATABASE_URL)
- [02-01]: pandas_ta_classic import name (not pandas_ta) for pandas-ta-classic package
- [02-01]: Class attributes for name/required_timeframes/min_candles instead of abstract properties
- [02-01]: detect_bos as alias for detect_structure_shift for plan artifact compatibility
- [02-02]: Simplified confirmation (close beyond sweep extreme within 3 bars) for v1 rather than full BOS/CHoCH
- [02-02]: Additive confidence scoring (base 50, +10 per bonus) capped at 100
- [02-02]: Float math internally, Decimal(str(round(x, 2))) at CandidateSignal boundary
- [02-03]: Momentum confirmation uses next bar (i+1) to avoid lookahead bias
- [02-03]: EMA spread widening uses simplified heuristic (no historical EMA storage)
- [02-03]: Breakout triggers on first non-compressed bar after consolidation exits
- [03-01]: SL always takes priority over TP when both could hit in same bar (conservative)
- [03-01]: XAUUSD pip value = $0.10 price movement; MAX_BARS_FORWARD = 72 (3 days at H1)
- [03-01]: Spread model returns tightest spread when multiple sessions overlap
- [03-01]: profit_factor capped at 9999.9999 for Numeric(10,4) DB compatibility
- [03-01]: BUY entry adjusted up by spread (ask), SELL SL checked against high + spread
- [03-02]: Mutable default avoidance: window_days_list defaults to None, set to [30, 60] in method body
- [03-02]: Force-added Alembic migration despite gitignore rule for version control tracking
- [03-03]: Strategy imports inside run_daily_backtests() to avoid circular imports and trigger registry
- [03-03]: Walk-forward efficiency averaged across win_rate and profit_factor WFE ratios
- [03-03]: BacktestResult rows tagged with spread_model="session_aware" for traceability
- [04-01]: Dataclass for StrategyScore (internal scoring, no serialization boundary)
- [04-01]: Degradation baseline is OLDEST non-walk-forward BacktestResult per strategy
- [04-01]: Backtest result fallback chain: 60-day -> 30-day -> any non-walk-forward
- [04-02]: Dedup checks symbol + direction + active status within 4h window (not strategy-specific)
- [04-02]: Bias detection is informational only -- appends note to reasoning, never rejects signals
- [04-04]: DXY correlation informational only -- divergence appends to reasoning, does not modify confidence
- [04-04]: No session-based suppression -- all sessions allowed; overlap gets +5 confidence boost
- [04-04]: Session label priority: "overlap" if active, else first active session, else "off_hours"

### Pending Todos

None yet.

### Blockers/Concerns

- Twelve Data free tier rate limits (800 req/day) may be insufficient -- design aggressive caching from Phase 1
- vectorbt not used -- walk-forward implemented with pure pandas 80/20 split (simpler, no extra dependency)
- Economic calendar API selection unresolved -- evaluate during Phase 5 planning
- Alembic requires PYTHONPATH set to project root when run from CLI (prefix with PYTHONPATH=.)
- TWELVE_DATA_API_KEY still set to placeholder -- needs real key before live ingestion

## Session Continuity

Last session: 2026-02-17T17:37:48Z
Stopped at: Completed 04-04-PLAN.md (Gold Intelligence Service)
Resume file: None
