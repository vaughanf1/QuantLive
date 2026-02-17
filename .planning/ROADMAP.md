# Roadmap: GoldSignal

## Overview

GoldSignal is built in 7 phases following the natural dependency chain of a closed-loop trading signal system. Phases 1-3 establish the data, strategy, and backtesting foundation. Phase 4 wires the full signal pipeline (the system's core value). Phase 5 makes signals visible to the trader via Telegram and TradingView. Phase 6 closes the feedback loop with outcome tracking and self-improvement. Phase 7 hardens the system for unattended 24/7 operation on Railway.

## Phases

**Phase Numbering:**
- Integer phases (1, 2, 3): Planned milestone work
- Decimal phases (2.1, 2.2): Urgent insertions (marked with INSERTED)

Decimal phases appear between their surrounding integers in numeric order.

- [ ] **Phase 1: Data Foundation** - Application skeleton, database schema, and reliable XAUUSD data ingestion
- [ ] **Phase 2: Strategy Engine** - Three rule-based trading strategies with shared interface
- [ ] **Phase 3: Backtesting Engine** - Rolling-window backtests with walk-forward validation and metric calculation
- [ ] **Phase 4: Signal Pipeline** - Strategy selection, signal generation, risk management, and gold-specific intelligence
- [ ] **Phase 5: Delivery and Visibility** - Telegram alerts and TradingView chart overlay
- [ ] **Phase 6: Outcome Tracking and Feedback** - Automated outcome detection and self-improvement loop
- [ ] **Phase 7: Production Hardening** - Railway deployment, data retention, and 24/7 reliability

## Phase Details

### Phase 1: Data Foundation
**Goal**: The system has a running FastAPI application backed by PostgreSQL that reliably fetches, validates, and stores XAUUSD candle data across all required timeframes
**Depends on**: Nothing (first phase)
**Requirements**: DATA-01, DATA-02, DATA-03, DATA-04, DATA-05, INFRA-01, INFRA-02, INFRA-03, INFRA-04, INFRA-06, INFRA-07, INFRA-09
**Success Criteria** (what must be TRUE):
  1. FastAPI application starts, responds to `/health`, and connects to PostgreSQL with all tables created via Alembic migrations
  2. System fetches XAUUSD OHLCV candles from Twelve Data across M15, H1, H4, D1 timeframes and stores them in the database with UTC timestamps
  3. Candle data refreshes automatically on schedule aligned to candle close times without manual intervention
  4. System detects and logs missing candles, data gaps, and stale data rather than silently using bad data
  5. Repeated fetches do not create duplicate candles -- cached data is reused and only new candles are fetched
**Plans**: TBD

Plans:
- [ ] 01-01: FastAPI skeleton, PostgreSQL schema, Alembic migrations, structured logging
- [ ] 01-02: Twelve Data ingestion, caching, gap detection, scheduled refresh
- [ ] 01-03: Data validation, index performance, integration test of full data pipeline

### Phase 2: Strategy Engine
**Goal**: Three rule-based trading strategies analyze XAUUSD data and produce standardized candidate signals through a common interface
**Depends on**: Phase 1
**Requirements**: STRAT-01, STRAT-02, STRAT-03, STRAT-04, STRAT-05, STRAT-06, STRAT-07
**Success Criteria** (what must be TRUE):
  1. All three strategies (Liquidity Sweep Reversal, Trend Continuation, Breakout Expansion) can be run against historical candle data and each produces CandidateSignal outputs with entry, SL, TP, direction, and reasoning
  2. Every strategy implements the BaseStrategy interface -- calling `analyze()` on any strategy returns the same CandidateSignal structure
  3. A new strategy can be added by creating one file and registering it -- zero changes to downstream code (registry pattern verified)
  4. Each strategy declares its required timeframes and minimum candle history, and raises a clear error if insufficient data is available
**Plans**: TBD

Plans:
- [ ] 02-01: BaseStrategy interface, CandidateSignal dataclass, strategy registry, indicators module
- [ ] 02-02: Liquidity Sweep Reversal implementation
- [ ] 02-03: Trend Continuation and Breakout Expansion implementations

### Phase 3: Backtesting Engine
**Goal**: The system can evaluate any strategy's historical performance using rolling-window backtests and produce reliable metrics that account for transaction costs
**Depends on**: Phase 1, Phase 2
**Requirements**: BACK-01, BACK-02, BACK-03, BACK-04, BACK-05, BACK-06, BACK-07
**Success Criteria** (what must be TRUE):
  1. System backtests each strategy on rolling 30-day and 60-day windows and calculates win rate, profit factor, Sharpe ratio, max drawdown, and expectancy
  2. Backtester uses the exact same strategy `analyze()` code as live signal generation -- no separate backtest-only strategy implementations exist
  3. Walk-forward validation splits data 80/20 and flags strategies that perform significantly worse on out-of-sample data (overfitting detection)
  4. Backtest results account for session-appropriate spread costs (not zero-spread assumptions)
  5. Backtests run as scheduled background jobs and all results are persisted to the database with timestamps and parameters
**Plans**: TBD

Plans:
- [ ] 03-01: vectorbt backtest runner, rolling windows, metric calculation
- [ ] 03-02: Walk-forward validation, spread model, overfitting detection
- [ ] 03-03: Background scheduling, result persistence, backtest verification

### Phase 4: Signal Pipeline
**Goal**: The system automatically selects the best-performing strategy, generates validated trade signals with risk management, and accounts for gold-specific market behavior
**Depends on**: Phase 1, Phase 2, Phase 3
**Requirements**: SEL-01, SEL-02, SEL-03, SEL-04, SEL-05, SEL-06, SEL-07, SIG-01, SIG-02, SIG-03, SIG-04, SIG-05, SIG-06, SIG-07, SIG-08, RISK-01, RISK-02, RISK-03, RISK-04, RISK-05, RISK-06, GOLD-01, GOLD-02, GOLD-03, GOLD-04
**Success Criteria** (what must be TRUE):
  1. Strategy selector ranks strategies using weighted backtest metrics and current volatility regime, and selects the highest-scoring strategy for signal generation
  2. Generated signals include all required fields (direction, entry, SL, TP1, TP2, R:R, confidence, strategy name, reasoning) with ATR-based SL/TP distances
  3. Signals below minimum R:R (1:1.5) or minimum confidence (60%) are automatically rejected, and duplicate signals for the same direction within the dedup window are suppressed
  4. Risk management enforces per-trade risk limits, maximum concurrent signals, daily loss limits, and volatility-adjusted position sizing
  5. System identifies current gold trading session (Asian/London/NY/overlap) and suppresses or adjusts signals during low-liquidity periods
**Plans**: TBD

Plans:
- [ ] 04-01: Strategy selector with composite scoring and volatility regime detection
- [ ] 04-02: Signal generator with validation filters, dedup, expiry, and bias detection
- [ ] 04-03: Risk management engine (position sizing, concurrent limits, daily loss limits, drawdown monitoring)
- [ ] 04-04: Gold-specific intelligence (session identification, session filtering, volatility profiles, DXY correlation)
- [ ] 04-05: Scanner loop integration (APScheduler, no-op on stale data, end-to-end pipeline test)

### Phase 5: Delivery and Visibility
**Goal**: Trade signals reach the trader instantly via Telegram and are visually displayed on a TradingView chart with entry/SL/TP overlays
**Depends on**: Phase 4
**Requirements**: TELE-01, TELE-02, TELE-03, TELE-04, TELE-05, TV-01, TV-02, TV-03, TV-04, TV-05
**Success Criteria** (what must be TRUE):
  1. When a signal fires, a formatted Telegram message arrives within seconds containing entry, SL, TP1, TP2, R:R, confidence, strategy name, and reasoning
  2. When a signal outcome is detected (TP1 hit, TP2 hit, SL hit, expired), a follow-up Telegram notification is sent
  3. Telegram delivery retries on failure (3 attempts, exponential backoff) and respects rate limits (max 1 msg/sec)
  4. A browser-accessible web page displays a live XAUUSD candlestick chart with signal markers (entry arrows, SL/TP horizontal lines) and color-coded historical outcomes
  5. Chart data is served via FastAPI REST endpoints and the page is accessible from the Railway-hosted URL
**Plans**: TBD

Plans:
- [ ] 05-01: Telegram bot setup, signal formatting (HTML parse mode), delivery with retry and rate limiting
- [ ] 05-02: Telegram outcome notifications
- [ ] 05-03: TradingView Lightweight Charts page with signal overlays, REST endpoints, historical outcome display

### Phase 6: Outcome Tracking and Feedback
**Goal**: The system automatically detects trade outcomes and uses them to continuously improve strategy selection
**Depends on**: Phase 4, Phase 5
**Requirements**: TRACK-01, TRACK-02, TRACK-03, TRACK-04, TRACK-05, FEED-01, FEED-02, FEED-03, FEED-04, FEED-05
**Success Criteria** (what must be TRUE):
  1. System monitors price against all active signals every 15-30 seconds and correctly detects TP1 hit, TP2 hit, SL hit, or time expiry (accounting for spread)
  2. All outcomes are logged automatically to the database with no manual input, and signal status transitions are tracked (active -> tp1_hit -> tp2_hit / sl_hit / expired)
  3. Trade outcomes trigger recalculation of rolling performance metrics that directly influence future strategy selection scoring
  4. Degrading strategies (win rate drop >15% or profit factor below 1.0) are auto-deprioritized and a Telegram alert is sent; recovered strategies are automatically restored after 7+ days
  5. Circuit breaker halts signal generation after 5+ consecutive losses or drawdown exceeding 2x historical max
**Plans**: TBD

Plans:
- [ ] 06-01: Outcome tracker (price polling, TP/SL/expiry detection, spread accounting, status updates)
- [ ] 06-02: Feedback loop (metric recalculation, strategy re-ranking, degradation detection, auto-deprioritization)
- [ ] 06-03: Circuit breaker, auto-recovery logic, degradation Telegram alerts

### Phase 7: Production Hardening
**Goal**: The system runs unattended 24/7 on Railway with proper data lifecycle management
**Depends on**: Phase 1 through Phase 6
**Requirements**: INFRA-05, INFRA-08
**Success Criteria** (what must be TRUE):
  1. Application is deployed on Railway as a long-running service (not serverless) and stays alive without manual restarts
  2. Data retention policy is enforced -- older low-timeframe data is pruned automatically while higher timeframe data is retained indefinitely
  3. System recovers gracefully from transient failures (API outages, database hiccups) without losing signal state or generating duplicate signals
**Plans**: TBD

Plans:
- [ ] 07-01: Railway deployment configuration, keep-alive, environment setup
- [ ] 07-02: Data retention policy, cleanup jobs, graceful degradation, smoke tests

## Progress

**Execution Order:**
Phases execute in numeric order: 1 -> 2 -> 3 -> 4 -> 5 -> 6 -> 7

| Phase | Plans Complete | Status | Completed |
|-------|----------------|--------|-----------|
| 1. Data Foundation | 0/3 | Not started | - |
| 2. Strategy Engine | 0/3 | Not started | - |
| 3. Backtesting Engine | 0/3 | Not started | - |
| 4. Signal Pipeline | 0/5 | Not started | - |
| 5. Delivery and Visibility | 0/3 | Not started | - |
| 6. Outcome Tracking and Feedback | 0/3 | Not started | - |
| 7. Production Hardening | 0/2 | Not started | - |
