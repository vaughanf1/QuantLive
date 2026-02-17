# Project Research Summary

**Project:** Automated XAUUSD Trade Signal System
**Domain:** Algorithmic trading signal platform — signals-only, no auto-execution
**Researched:** 2026-02-17
**Confidence:** HIGH

---

## Executive Summary

This is a production-grade, closed-loop signal intelligence platform for XAUUSD (gold). It runs three rule-based strategies (Liquidity Sweep Reversal, Trend Continuation, Breakout Expansion) against rolling-window backtests, dynamically selects the best-performing strategy based on regime and live metrics, generates 1-2 high-conviction signals per day with 200-500 pip targets, delivers alerts via Telegram, and self-improves through an outcome-feedback loop. The system is signals-only — no auto-execution, no broker integration, no order management. The human stays in the loop.

The recommended architecture is a single Python process (FastAPI + APScheduler) deployed on Railway, backed by PostgreSQL. Market data comes from Twelve Data (REST + WebSocket), backtesting uses vectorbt, indicators use pandas-ta, and signal delivery uses python-telegram-bot. All components are in-process for v1 — no Celery, no Redis, no distributed infrastructure. The entire stack is justified by Railway's long-running service model and the single-instrument focus. Total cost is approximately $35-40/month (Railway + Twelve Data Grow plan). This is a deliberate minimalist foundation designed to be upgraded selectively as volume grows.

The three highest risks are all in the backtesting layer: look-ahead bias, overfitting to recent regimes, and ignoring transaction costs. If the backtester is wrong, every downstream decision — strategy selection, confidence scoring, self-improvement — is built on bad data. Secondary risks include gold-specific behavior (news spread blowouts, weekend gaps, session liquidity traps) and Railway deployment constraints (cold starts, memory limits, WebSocket fragility). A mandatory 4-6 week paper-trading phase before any public launch is non-negotiable.

---

## Key Findings

### Recommended Stack

The stack is Python-centric with no exotic choices. FastAPI provides the async API layer and serves as the host process for APScheduler background jobs. PostgreSQL (Railway-managed) stores all system state — candles, strategies, backtest results, signals, outcomes, and performance metrics. SQLAlchemy 2.x with asyncpg provides the async ORM. Twelve Data is the market data source (best XAUUSD coverage at this price point; free tier is viable for MVP with aggressive caching). vectorbt handles backtesting (100-1000x faster than event-driven alternatives for rolling-window evaluation). pandas-ta handles technical indicators (pure Python, no C compilation — critical for Railway deployment). python-telegram-bot v21+ handles alert delivery.

The key dependency constraint: NumPy must be pinned to `<2.0` for vectorbt 0.26.x compatibility. APScheduler must stay on v3.x — v4.x is an unstable rewrite. The TradingView Lightweight Charts frontend (for the web dashboard) is deferred to post-MVP; Telegram is the primary delivery channel.

**Core technologies:**
- `FastAPI + uvicorn`: API layer and application host — async-native, auto-docs, Pydantic integration
- `APScheduler 3.x`: In-process job scheduling — no Redis/Celery required for 2-3 recurring tasks
- `SQLAlchemy 2.x + asyncpg + Alembic`: Async ORM with Alembic migrations — industry standard
- `PostgreSQL (Railway)`: Primary data store — ACID, JSONB extensibility, managed hosting
- `twelvedata[websocket]`: Market data — best XAUUSD coverage, native Python SDK, REST + WebSocket
- `vectorbt`: Backtesting — vectorized (NumPy-based), 100-1000x faster than event-driven alternatives
- `pandas-ta`: Technical indicators — 130+ indicators, pure Python, direct DataFrame integration
- `python-telegram-bot 21+`: Alert delivery — async-native, most mature Python Telegram library
- `pydantic-settings`: Config management — type-safe env var parsing
- `loguru`: Structured logging — significantly better than stdlib logging for Railway debugging
- `tenacity`: Retry logic — essential for external API call resilience

**Not recommended:**
- Celery + Redis (overkill for 2-3 jobs), TA-Lib (C compilation breaks Railway), Backtrader (event-driven is too slow), Alpha Vantage (unreliable XAUUSD data), APScheduler v4.x (unstable rewrite)

See `.planning/research/STACK.md` for full version matrix and rationale.

---

### Expected Features

The feature set is organized into four tiers. Tier 1 is the MVP. Tiers 3-4 require live trade history to be meaningful.

**Must have — table stakes (Tier 1, build first):**
- Signal core: entry price, stop loss, take profit (2 levels), direction, timestamp, timeframe, R:R ratio
- Confidence/conviction score per signal (categorical: Low/Medium/High — not decimal precision)
- Backtesting engine with rolling windows and out-of-sample validation
- Telegram delivery with structured format and delivery confirmation
- Signal outcome auto-detection (TP1, TP2, SL, expiry monitoring)
- Session awareness (London/NY/Asian) with signal filtering
- Per-trade risk limits and position sizing calculator
- Basic performance statistics (win rate, profit factor, drawdown, expectancy)

**Should have — differentiators (Tier 2, build second):**
- Volatility regime detection (ATR-based) with regime-strategy mapping
- Dynamic strategy selection based on rolling backtest scores + regime
- Multi-timeframe confluence analysis (HTF trend alignment, LTF entry refinement)
- Daily loss limits and drawdown monitoring
- Pine Script signal overlay for TradingView
- System health monitoring and data freshness checks

**Build third (Tier 3 — needs live trade history):**
- Self-improvement feedback loop with parameter monitoring
- Gold-specific intelligence: DXY correlation, economic calendar (FOMC/NFP/CPI blackouts)
- Advanced risk management: trailing stops, partial TPs, volatility-adjusted sizing
- Session-based and strategy-specific performance analytics
- Error alerting and scheduled performance reports

**Defer to v2+ (Tier 4):**
- Monte Carlo simulation, COT data integration, seasonal patterns
- Advanced TradingView dashboard with interactive signal replay
- Equity curve analysis for dynamic position sizing
- Heatmap visualizations

**Anti-features — deliberately excluded:**
- Auto-execution / broker integration (regulatory and liability risk)
- Machine learning models (black-box overfitting on financial data)
- Multi-pair support (dilutes XAUUSD specialization)
- Mobile app (Telegram IS the mobile app)
- Paid subscription system (premature monetization)
- Real-time tick-by-tick processing (candle-based is sufficient for this trading style)

See `.planning/research/FEATURES.md` for full feature dependency map and competitive analysis.

---

### Architecture Approach

The system is a 10-component closed-loop signal intelligence platform. All components run in a single FastAPI process for v1 (no distributed architecture). The data flow is: Data Ingestion -> PostgreSQL -> Strategy Engine + Backtesting Engine -> Strategy Selector -> Signal Generator -> Telegram Notifier + TradingView UI + Outcome Tracker -> Feedback Loop -> (back to PostgreSQL). The key architectural principle is that the Strategy Engine uses exactly the same `strategy.analyze()` code path for both backtesting and live signal generation — this eliminates the most common source of backtest/live divergence.

**10 components and suggested build order:**

| Order | Component | Core Responsibility |
|-------|-----------|-------------------|
| 1 | PostgreSQL Database | Schema, migrations, all persistent state (candles, signals, outcomes, metrics) |
| 2 | Data Ingestion Layer | Fetch, normalize, validate, store OHLCV from Twelve Data (REST + optional WebSocket) |
| 3 | Strategy Engine | Three BaseStrategy implementations; `CandidateSignal` output; indicators module |
| 4 | Backtesting Engine | Rolling window backtests; walk-forward validation; metric calculation (win rate, PF, Sharpe, drawdown, expectancy) |
| 5 | Strategy Selector | Composite scoring algorithm; regime-aware weighting; degradation penalties |
| 6 | Signal Generator | Validation (dedup, R:R filter, confidence threshold); enrichment; persistence |
| 7 | Telegram Notifier | Format and deliver alerts; delivery confirmation; retry logic |
| 8 | TradingView Integration | Lightweight Charts web dashboard; signal overlay (post-MVP) |
| 9 | Feedback Loop | Degradation detection; strategy performance re-ranking; auto-deprioritization |
| 10 | Outcome Tracker | Poll price vs active signal levels (SL/TP1/TP2/expiry); record outcomes; trigger feedback |

**Schema highlights:** 6 core tables — `candles`, `strategies`, `backtest_results`, `signals`, `outcomes`, `strategy_performance`. JSONB `parameters` and `metadata` columns provide extensibility without constant schema migrations. All timestamps in UTC.

**Scanner design:** Runs every 60 seconds via APScheduler. No-op if no new candle data arrived since last cycle (prevents duplicate signals). Calls `strategy.analyze()` for all active strategies, passes candidates to the selector, and dispatches the winner.

See `.planning/research/ARCHITECTURE.md` for full component boundaries, data flow diagrams, and file structure.

---

### Critical Pitfalls

**P0 — System-invalidating (must prevent in Phase 1):**

1. **Look-ahead bias in the backtester** — Using data that would not have been available at signal time. This is the single most destructive bug in trading system development. Prevention: process one bar at a time, never expose future data, implement a future-leak detector, entry must be next candle's open (with slippage). Verify before any strategy evaluation.

2. **Timezone mismatches** — Different sources use UTC, ET, or broker time (EET). Mismatches make every indicator wrong and every session filter wrong. Prevention: standardize on UTC internally for all storage and processing; convert to display time only at the UI layer; write a regression test comparing daily OHLC against TradingView.

3. **No paper trading phase** — Going from backtest to live signals without validation. Prevention: mandatory 4-6 weeks of private paper trading (50+ signals minimum) before any public launch. Compare paper results to backtest expectations; investigate if win rate diverges by more than 15%.

**P1 — High impact (must prevent in Phase 1-2):**

4. **Overfitting / curve fitting** — Tuning strategy parameters to fit recent gold price action. Gold changes regimes frequently (trending vs ranging vs volatile). Prevention: max 5 tunable parameters per strategy, round-number thresholds, test across 3 distinct market regimes, 6-month in-sample / 2-month out-of-sample rolling walk-forward.

5. **Ignoring transaction costs** — Zero spread/slippage assumptions in backtests. Gold spreads range from 10-20 pips (London/NY) to 80+ pips (Asian session, news events). Prevention: model session-specific spreads, add 5-10 pip slippage buffer, require profitability at pessimistic spread assumptions.

6. **News blackout missing** — Signals generated within 15-30 minutes of NFP, FOMC, CPI cause spread-blowout losses. Prevention: integrate economic calendar from Phase 2; hard blackout 30 min before / 15 min after high-impact events; 60 min post-FOMC.

7. **Self-improvement feedback loop creating negative feedback** — Parameter auto-adjustment in response to normal drawdowns worsens performance. Prevention: constrain the loop to strategy selection only (not individual parameters); circuit breaker at 5 consecutive losses; minimum 2 weeks or 20 trades between adjustments; run a non-adaptive control for comparison.

**P2 — Moderate impact (address in Phase 3-5):**

8. **Recency bias in strategy selection** — Switching strategies too frequently after single wins/losses. Prevention: minimum 30-50 trades per rolling window, 60-90 day window, switching cost (outperform by 0.3+ profit factor before switch), composite scoring (not win rate alone).

9. **Small sample size in rolling backtests** — With 1-2 signals/day per strategy, a 30-day window gives only 15-20 trades — too few for statistical significance. Prevention: 90-180 day window, confidence intervals on all metrics, declare strategy "better" only when confidence intervals don't overlap.

10. **Railway cold starts** — Hobby-tier services spin down; cold start adds 10-30 seconds of latency to signal delivery. Prevention: keep-alive health check (UptimeRobot or similar), Railway Pro plan, APScheduler in-process scheduler (more reliable than Railway cron).

See `.planning/research/PITFALLS.md` for the full priority matrix (P0-P3) with phase mapping and prevention strategies.

---

## Implications for Roadmap

Based on combined research, a 7-phase build order is recommended. Phases 1-4 build the closed loop. Phase 5 adds delivery and visibility. Phase 6 closes the feedback loop. Phase 7 hardens for production.

### Phase 1: Data Foundation
**Rationale:** Everything depends on having clean, timezone-correct OHLCV data in the database and a running application skeleton. The backtester must be built correctly here — look-ahead bias, timezone standardization, and missing candle handling are Phase 1 obligations with no exceptions. Alembic migrations and structured logging also start here.
**Delivers:** PostgreSQL schema, Alembic migrations, FastAPI skeleton with `/health`, Twelve Data ingestion for M15/H1/H4/D1, data validation and gap detection, UTC-standardized storage
**Avoids:** Look-ahead bias (P0), timezone mismatches (P0), unbounded table growth (P3 — design schema with indices and retention from day one)
**Research flag:** Standard patterns. No additional research needed.

### Phase 2: Strategy Engine
**Rationale:** The strategy abstraction must exist before backtesting or signal generation. Start with the most distinctive strategy (Liquidity Sweep Reversal) to validate the pattern. The `BaseStrategy` / `CandidateSignal` contract ensures backtesting and live code share the same path.
**Delivers:** `BaseStrategy` abstract class, `CandidateSignal` dataclass, strategy registry, LiquiditySweepReversal implementation, indicators module (ATR, RSI, swing highs/lows, structure)
**Avoids:** Backtest/live divergence (use same `analyze()` for both)
**Research flag:** Standard patterns. Strategy logic itself requires domain judgment, not external research.

### Phase 3: Backtesting Engine
**Rationale:** Need backtest metrics before the strategy selector can rank strategies. This phase must include spread modeling, walk-forward validation, and metric calculation. This is where overfitting prevention is enforced structurally.
**Delivers:** vectorbt-based backtest runner, rolling 30/60-day windows, walk-forward (80/20 in/out-of-sample), metric calculation (win rate, PF, Sharpe, drawdown, expectancy), session-specific spread model, `backtest_results` persistence, daily background job
**Avoids:** Overfitting (P1), ignoring transaction costs (P1), ambiguous OHLC bar resolution (pessimistic default), small sample size (90+ day window design)
**Research flag:** May need research-phase for walk-forward implementation details if vectorbt's rolling API is insufficient. Evaluate during build.

### Phase 4: Signal Pipeline
**Rationale:** With strategies and backtest metrics in place, wire the full signal pipeline. This is the system's core value: automatic signal selection and generation. All three strategies implemented here. Paper-trading mode must be part of this phase — signals generated but routed only to a private dev channel.
**Delivers:** Strategy selector with composite scoring, signal generator with dedup/R:R/confidence filters, scanner loop (APScheduler, 60s interval, no-op on no new data), TrendContinuation and BreakoutExpansion implementations, `signals` persistence, paper-trading mode (private Telegram channel)
**Avoids:** Recency bias in selection (P2), strategy whipsaw (minimum holding periods), false confidence scores (categorical: Low/Medium/High only), no public launch without paper trading (P0)
**Research flag:** Standard patterns for the selector algorithm. The composite scoring weights (30% win rate, 25% profit factor, 20% Sharpe, 15% expectancy, 10% inverse drawdown) can be validated empirically during paper trading.

### Phase 5: Delivery and Visibility
**Rationale:** Signals are generating. Make them visible to the user. Telegram is the primary channel (MVP). TradingView Lightweight Charts dashboard is secondary (post-MVP but included here for chart review workflow).
**Delivers:** Telegram bot with structured signal format, TP/SL hit notifications, economic calendar integration (news blackout filter), TradingView Lightweight Charts web page with signal overlays (entry/SL/TP markers, outcome color-coding)
**Avoids:** News blackout missing (P1 — economic calendar is part of this phase), Telegram rate limits (message queue, 1 msg/sec, exponential backoff), MarkdownV2 formatting failures (use HTML parse mode instead), chart delivery blocking signal delivery (decouple — text first, chart follows)
**Research flag:** ForexFactory or alternative economic calendar API needs evaluation. May need research-phase to confirm best free/paid option for FOMC/NFP/CPI data.

### Phase 6: Outcome Tracking and Feedback Loop
**Rationale:** The system is generating and delivering signals. Close the loop. Outcome tracking enables performance reporting; the feedback loop enables self-improvement. Implement conservatively — the feedback loop adjusts strategy selection only, never individual strategy parameters.
**Delivers:** Outcome tracker (15-30s polling, checks all active signals vs current price), `outcomes` persistence, signal status updates, feedback loop recalculating rolling strategy metrics, degradation detection (win rate drops 15%+, profit factor < 1.0, 3+ consecutive losses), auto-deprioritization + Telegram alert on degradation, strategy performance dashboard in API
**Avoids:** Self-improvement feedback loops (P1 — constrain to selection only, circuit breaker at 5 consecutive losses, 2-week minimum between adjustments, control variant running in parallel), partial fill tracking confusion (track signal outcomes separately from user outcomes)
**Research flag:** Standard patterns. The degradation thresholds (15% win rate drop, PF < 1.0) should be validated against live paper-trade data before finalizing.

### Phase 7: Production Hardening
**Rationale:** System is functionally complete after Phase 6. Phase 7 focuses on reliability, observability, and graceful degradation so the system runs unattended 24/5.
**Delivers:** Comprehensive error handling and retry logic (tenacity for all external calls), structured JSON logging at every pipeline step with signal data snapshots, Railway keep-alive (UptimeRobot ping), memory profiling and chunked backtest processing, graceful degradation (data API fallback, message queuing, secondary alert channel for system failures), data retention policy (M1 data: 30 days; M15: 6 months; H1+: 2 years), API rate limiting, unit tests for strategies and metric calculations, integration tests for signal pipeline, Alembic migration regression tests
**Avoids:** Cold start latency (P2), memory limits/OOM kills (P2), no graceful degradation (P2), insufficient logging (P3)
**Research flag:** Standard patterns. Railway Pro plan evaluation (cost vs reliability benefit) is a business decision, not a research question.

---

### Phase Ordering Rationale

The order is dependency-driven, not feature-driven:

- **Phases 1-2** establish the data and strategy foundation that everything else depends on. You cannot backtest without data; you cannot select a strategy without a strategy.
- **Phase 3** (backtesting) must come before Phase 4 (signal pipeline) because the strategy selector requires backtest metrics to rank candidates. No metrics = no selection = random strategy choice.
- **Phase 4** includes paper trading mode because the public launch should never be the first live test. The 4-6 week paper trading period overlaps with Phase 5 development — build delivery while paper trading runs.
- **Phase 5** (delivery) comes before Phase 6 (feedback) because you need to deliver signals before you can track their outcomes.
- **Phase 6** (feedback) can only produce meaningful results after accumulating 50+ live signals — this naturally follows Phase 5.
- **Phase 7** (hardening) is last because you can only profile and optimize a system that is functionally complete.

---

### Research Flags

**Needs research-phase during planning:**
- **Phase 3:** vectorbt rolling window API capabilities — confirm whether built-in walk-forward support is sufficient or requires custom implementation
- **Phase 5:** Economic calendar API options — evaluate ForexFactory API, Investing.com, or Finnhub for FOMC/NFP/CPI event data (free tier availability, reliability, update latency)

**Standard patterns (no additional research needed):**
- **Phase 1:** FastAPI + SQLAlchemy + Alembic + Railway — all well-documented with established patterns
- **Phase 2:** Strategy abstraction pattern — standard Python ABC pattern, no unknowns
- **Phase 4:** APScheduler setup, strategy selector composite scoring — documented approaches
- **Phase 5:** python-telegram-bot v21+ — excellent documentation, HTML parse mode resolves formatting pitfalls
- **Phase 6:** Polling-based outcome tracking — simple and reliable for v1; WebSocket upgrade is a v2 decision
- **Phase 7:** Error handling, logging, Railway deployment — all standard patterns

---

## Confidence Assessment

| Area | Confidence | Notes |
|------|------------|-------|
| Stack | HIGH | All core choices are well-established libraries with strong community support. NumPy < 2.0 pin for vectorbt is a known constraint. Twelve Data version should be verified against current PyPI before locking. |
| Features | HIGH | Feature taxonomy is based on clear domain requirements (signals-only trading system). Feature tiers align with architectural dependencies. Anti-feature list is well-reasoned. |
| Architecture | HIGH | 10-component design is clean with explicit boundaries. Database schema is complete. Build order is dependency-validated. Single-process APScheduler approach is appropriate for v1 load. |
| Pitfalls | HIGH | Pitfalls are domain-specific and well-documented. P0 pitfalls (look-ahead bias, timezone, no paper trading) are non-negotiable. P1 pitfalls (overfitting, costs, news blackout, feedback loop) are specific and actionable. |

**Overall confidence: HIGH**

---

### Gaps to Address

1. **Twelve Data version confirmation:** All library versions should be verified against current PyPI before writing `requirements.txt`. The STACK.md research was conducted without live web access. Risk is low (stable libraries) but worth a 5-minute check.

2. **Economic calendar API selection:** The specific API for FOMC/NFP/CPI event data is unresolved. Options include ForexFactory (scraping-based, fragile), Investing.com, Finnhub (has economic calendar endpoint), or Tradermade. Needs validation during Phase 5 planning.

3. **Twelve Data rate limits on free tier:** The free tier (800 req/day) may or may not be sufficient for MVP depending on how aggressively data is cached. This is unknowable without measuring actual request volume during Phase 1 development. Design the OHLCV cache to minimize API calls from day one; upgrade to Grow plan ($29/mo) if needed.

4. **vectorbt walk-forward API depth:** vectorbt's rolling/walk-forward capabilities are well-documented in principle but the specific API for custom rolling evaluation windows should be prototyped in Phase 3 before committing to the approach. Fallback: implement walk-forward manually using pandas date slicing.

5. **Strategy parameter baselines:** The three strategies (Liquidity Sweep Reversal, Trend Continuation, Breakout Expansion) are defined in concept but their initial parameter values (ATR multipliers, RSI thresholds, EMA periods) need to be established during Phase 2 strategy development based on historical XAUUSD behavior. These should not be derived from a single data period — use 3 distinct market regimes for initial calibration.

---

## Sources

### Primary (HIGH confidence)
- STACK.md research (2026-02-17) — full stack selection with rationale and version matrix
- FEATURES.md research (2026-02-17) — complete feature taxonomy with dependency map and implementation tiers
- ARCHITECTURE.md research (2026-02-17) — 10-component design with schema, data flows, file structure, and 7-phase build order
- PITFALLS.md research (2026-02-17) — domain-specific pitfall catalogue with P0-P3 priority matrix and phase mapping

### Secondary (MEDIUM confidence)
- vectorbt documentation patterns — rolling window backtesting approach
- python-telegram-bot v21+ async patterns — delivery and rate limiting
- Railway deployment characteristics — cold start behavior, memory limits, WebSocket reliability

### Tertiary (LOW confidence — verify before implementation)
- Twelve Data API current version and rate limits — verify against PyPI/Twelve Data dashboard
- Economic calendar API options — evaluate during Phase 5 planning
- Gold session spread profiles (10-80+ pips) — validate against broker data during Phase 1

---

*Research completed: 2026-02-17*
*Ready for roadmap: yes*
