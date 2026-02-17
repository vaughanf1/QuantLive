# Requirements: GoldSignal

**Defined:** 2026-02-17
**Core Value:** Deliver 1-2 high-conviction, statistically validated XAUUSD trade signals per day with full automation from generation through outcome tracking.

## v1 Requirements

### Data Ingestion

- [ ] **DATA-01**: System fetches XAUUSD OHLCV candle data from Twelve Data API across M15, H1, H4, D1 timeframes
- [ ] **DATA-02**: System caches historical candle data in PostgreSQL to minimize API calls
- [ ] **DATA-03**: System detects and handles missing candles, data gaps, and stale data
- [ ] **DATA-04**: All timestamps stored and processed in UTC with timezone-aware handling
- [ ] **DATA-05**: System refreshes candle data on schedule aligned to candle close times per timeframe

### Trading Strategies

- [ ] **STRAT-01**: Liquidity Sweep Reversal strategy — detects stop hunts below/above key levels, waits for market structure shift, enters on confirmation
- [ ] **STRAT-02**: Trend Continuation strategy — uses EMA/VWAP trend filter, identifies pullbacks in established trends, enters on momentum confirmation
- [ ] **STRAT-03**: Breakout Expansion strategy — detects consolidation ranges, identifies volatility expansion, optional retest entry
- [ ] **STRAT-04**: All strategies implement a common BaseStrategy interface with `analyze()` returning standardized CandidateSignal
- [ ] **STRAT-05**: Each strategy defines entry logic, stop placement logic, TP logic, invalidation conditions, and session filters
- [ ] **STRAT-06**: Each strategy declares its required timeframes and minimum candle history
- [ ] **STRAT-07**: Adding a new strategy requires zero changes to downstream components (registry pattern)

### Backtesting Engine

- [ ] **BACK-01**: System backtests each strategy on a rolling 30-day window (primary) and 60-day window (secondary)
- [ ] **BACK-02**: Backtester uses the same strategy code as live signal generation (no divergence)
- [ ] **BACK-03**: Backtester calculates: win rate, profit factor, Sharpe ratio, max drawdown, expectancy
- [ ] **BACK-04**: Walk-forward validation splits data 80/20 train/test to detect overfitting
- [ ] **BACK-05**: Backtester accounts for spread as a transaction cost (session-appropriate spread model)
- [ ] **BACK-06**: Backtests run as background jobs daily (not in API request path)
- [ ] **BACK-07**: All backtest results persisted to database with timestamp and parameters used

### Strategy Intelligence

- [ ] **SEL-01**: Strategy selector scores strategies using weighted metrics (win rate, profit factor, Sharpe, expectancy, drawdown)
- [ ] **SEL-02**: Volatility regime detection classifies market as low/medium/high using ATR
- [ ] **SEL-03**: Strategy selection factors in current volatility regime (e.g., favor Breakout in high-vol, Trend Continuation in trending)
- [ ] **SEL-04**: Multi-timeframe confluence scoring — signals score higher when multiple timeframes agree on direction
- [ ] **SEL-05**: Strategy degradation detection — flag strategies with win rate drop >15% or profit factor below 1.0
- [ ] **SEL-06**: Auto-deprioritize degrading strategies in selection scoring
- [ ] **SEL-07**: Minimum sample size enforcement — require 30+ trades in rolling window before trusting metrics

### Signal Generation

- [ ] **SIG-01**: Signals include: direction (BUY/SELL), entry price, stop loss, TP1, TP2, R:R ratio, confidence score, strategy name, reasoning summary
- [ ] **SIG-02**: SL and TP distances calculated using ATR at signal time, then locked as fixed levels
- [ ] **SIG-03**: Minimum R:R filter — reject signals below 1:1.5
- [ ] **SIG-04**: Minimum confidence threshold — reject signals below 60%
- [ ] **SIG-05**: Signal deduplication — no new signal if an active signal exists for same direction within configurable window
- [ ] **SIG-06**: Each signal has an expiry timestamp (configurable, e.g., 8h intraday / 48h swing)
- [ ] **SIG-07**: System avoids directional bias — validates that signal distribution is not systematically skewed long or short
- [ ] **SIG-08**: Scanner loop runs on schedule, no-ops if no new candle data (prevents duplicate processing)

### Risk Management

- [ ] **RISK-01**: Per-trade risk limit configurable (e.g., 1-2% of account)
- [ ] **RISK-02**: Maximum concurrent active signals limit (e.g., max 2-3)
- [ ] **RISK-03**: Position sizing calculator based on SL distance and risk percentage
- [ ] **RISK-04**: Daily loss limit — suppress signal generation after configurable daily drawdown threshold
- [ ] **RISK-05**: Drawdown monitoring with running and maximum drawdown tracking
- [ ] **RISK-06**: Volatility-adjusted position sizing — reduce size in high-vol regimes, increase in low-vol

### Gold-Specific Intelligence

- [ ] **GOLD-01**: Session identification — classify current time as Asian, London, New York, or London/NY overlap
- [ ] **GOLD-02**: Session-based signal filtering — suppress or adjust signals during low-liquidity sessions (e.g., Asian)
- [ ] **GOLD-03**: Gold session volatility profiles — recognize volatility patterns at London open, NY open, and overlap periods
- [ ] **GOLD-04**: DXY (Dollar Index) correlation monitoring — track inverse correlation with gold, flag significant divergences

### Telegram Alerts

- [ ] **TELE-01**: Telegram bot sends formatted signal alerts with entry, SL, TP1, TP2, R:R, confidence, strategy, reasoning
- [ ] **TELE-02**: Telegram bot sends outcome notifications when TP1 hit, TP2 hit, SL hit, or signal expired
- [ ] **TELE-03**: Message formatting uses HTML parse mode (avoids MarkdownV2 escaping issues with gold prices)
- [ ] **TELE-04**: Message delivery with retry logic (3 attempts, exponential backoff)
- [ ] **TELE-05**: Rate limiting compliance (max 1 msg/sec to same chat)

### TradingView Integration

- [ ] **TV-01**: Web page using TradingView Lightweight Charts displays live XAUUSD candlestick chart
- [ ] **TV-02**: When a signal fires, entry arrow marker and SL/TP horizontal lines are drawn on the chart
- [ ] **TV-03**: Historical signals displayed on chart color-coded by outcome (green = TP hit, red = SL hit, gray = active)
- [ ] **TV-04**: Chart data served via FastAPI REST endpoints (candles + signals)
- [ ] **TV-05**: Chart page accessible via browser from Railway-hosted URL

### Outcome Tracking

- [ ] **TRACK-01**: System monitors current price against all active signals every 15-30 seconds
- [ ] **TRACK-02**: Detects TP1 hit, TP2 hit, SL hit, or time expiry for each active signal
- [ ] **TRACK-03**: Outcomes logged automatically to database with no manual input
- [ ] **TRACK-04**: Signal status updated in database when outcome detected (active → tp1_hit → tp2_hit / sl_hit / expired)
- [ ] **TRACK-05**: Spread accounted for in outcome calculations

### Self-Improvement Loop

- [ ] **FEED-01**: Trade outcomes trigger recalculation of rolling performance metrics per strategy
- [ ] **FEED-02**: Updated metrics directly influence strategy selection scoring
- [ ] **FEED-03**: Degradation alerts sent via Telegram when a strategy shows sustained underperformance
- [ ] **FEED-04**: Auto-recovery — if degraded strategy recovers over 7+ days, clear degradation flag
- [ ] **FEED-05**: Circuit breaker — halt signal generation after 5+ consecutive losses or drawdown exceeding 2x historical max

### Infrastructure & Deployment

- [ ] **INFRA-01**: FastAPI application with health check endpoint
- [ ] **INFRA-02**: PostgreSQL database with tables for candles, strategies, backtest results, signals, outcomes, performance metrics
- [ ] **INFRA-03**: Alembic migrations for all schema changes
- [ ] **INFRA-04**: APScheduler running background jobs (data refresh, scanning, backtesting, outcome tracking)
- [ ] **INFRA-05**: Deployed on Railway as long-running service (not serverless)
- [ ] **INFRA-06**: Environment variables for API keys, bot tokens, database URL
- [ ] **INFRA-07**: Structured logging with loguru for debugging and observability
- [ ] **INFRA-08**: Data retention policy — 1-min data for 30 days, higher TFs indefinitely
- [ ] **INFRA-09**: Database indices on (symbol, timeframe, timestamp) for query performance

## v2 Requirements

### Economic Calendar

- **ECON-01**: Integrate economic calendar API (Finnhub or similar)
- **ECON-02**: Suppress signals 30 minutes before and after high-impact USD events (NFP, FOMC, CPI)
- **ECON-03**: Flag signals generated near scheduled events

### Advanced Risk

- **ARISK-01**: Trailing stop option — move SL to breakeven after TP1 hit
- **ARISK-02**: Partial TP management — signal scaled exits (50% at TP1, 50% at TP2)
- **ARISK-03**: Equity curve analysis for dynamic position sizing

### Advanced Analytics

- **ANAL-01**: Session-based performance breakdown (which sessions produce best signals)
- **ANAL-02**: Monte Carlo simulation for equity curve projections
- **ANAL-03**: Time-in-trade analysis (how long do winners vs losers take)
- **ANAL-04**: Performance heatmap by day-of-week and session

### Advanced TradingView

- **ATV-01**: Signal zone highlighting (shaded areas instead of just lines)
- **ATV-02**: Interactive dashboard panel on chart showing live signal status
- **ATV-03**: Multi-timeframe context visualization

### Monitoring

- **MON-01**: System health dashboard (data feed status, uptime, delivery success)
- **MON-02**: Data feed integrity checks and gap alerting
- **MON-03**: Latency monitoring (signal generation to delivery)

## Out of Scope

| Feature | Reason |
|---------|--------|
| AI/ML prediction models | System is rule-based and statistically testable |
| Automated broker execution | Signals only — user executes manually on prop firm account |
| Multi-asset support | XAUUSD only — deep specialization is a strength |
| Mobile app | Telegram is the mobile delivery channel |
| Social/copy trading | Single user system |
| Real-time tick-by-tick processing | Candle-based analysis sufficient for swing-intraday |
| Custom strategy builder | Curated strategies, not a platform |
| Sentiment analysis | Noisy and unreliable for gold vs. macro data |
| Cryptocurrency support | Different market microstructure |

## Traceability

| Requirement | Phase | Status |
|-------------|-------|--------|
| DATA-01 | Phase 1 | Complete |
| DATA-02 | Phase 1 | Complete |
| DATA-03 | Phase 1 | Complete |
| DATA-04 | Phase 1 | Complete |
| DATA-05 | Phase 1 | Complete |
| STRAT-01 | Phase 2 | Complete |
| STRAT-02 | Phase 2 | Complete |
| STRAT-03 | Phase 2 | Complete |
| STRAT-04 | Phase 2 | Complete |
| STRAT-05 | Phase 2 | Complete |
| STRAT-06 | Phase 2 | Complete |
| STRAT-07 | Phase 2 | Complete |
| BACK-01 | Phase 3 | Pending |
| BACK-02 | Phase 3 | Pending |
| BACK-03 | Phase 3 | Pending |
| BACK-04 | Phase 3 | Pending |
| BACK-05 | Phase 3 | Pending |
| BACK-06 | Phase 3 | Pending |
| BACK-07 | Phase 3 | Pending |
| SEL-01 | Phase 4 | Pending |
| SEL-02 | Phase 4 | Pending |
| SEL-03 | Phase 4 | Pending |
| SEL-04 | Phase 4 | Pending |
| SEL-05 | Phase 4 | Pending |
| SEL-06 | Phase 4 | Pending |
| SEL-07 | Phase 4 | Pending |
| SIG-01 | Phase 4 | Pending |
| SIG-02 | Phase 4 | Pending |
| SIG-03 | Phase 4 | Pending |
| SIG-04 | Phase 4 | Pending |
| SIG-05 | Phase 4 | Pending |
| SIG-06 | Phase 4 | Pending |
| SIG-07 | Phase 4 | Pending |
| SIG-08 | Phase 4 | Pending |
| RISK-01 | Phase 4 | Pending |
| RISK-02 | Phase 4 | Pending |
| RISK-03 | Phase 4 | Pending |
| RISK-04 | Phase 4 | Pending |
| RISK-05 | Phase 4 | Pending |
| RISK-06 | Phase 4 | Pending |
| GOLD-01 | Phase 4 | Pending |
| GOLD-02 | Phase 4 | Pending |
| GOLD-03 | Phase 4 | Pending |
| GOLD-04 | Phase 4 | Pending |
| TELE-01 | Phase 5 | Pending |
| TELE-02 | Phase 5 | Pending |
| TELE-03 | Phase 5 | Pending |
| TELE-04 | Phase 5 | Pending |
| TELE-05 | Phase 5 | Pending |
| TV-01 | Phase 5 | Pending |
| TV-02 | Phase 5 | Pending |
| TV-03 | Phase 5 | Pending |
| TV-04 | Phase 5 | Pending |
| TV-05 | Phase 5 | Pending |
| TRACK-01 | Phase 6 | Pending |
| TRACK-02 | Phase 6 | Pending |
| TRACK-03 | Phase 6 | Pending |
| TRACK-04 | Phase 6 | Pending |
| TRACK-05 | Phase 6 | Pending |
| FEED-01 | Phase 6 | Pending |
| FEED-02 | Phase 6 | Pending |
| FEED-03 | Phase 6 | Pending |
| FEED-04 | Phase 6 | Pending |
| FEED-05 | Phase 6 | Pending |
| INFRA-01 | Phase 1 | Complete |
| INFRA-02 | Phase 1 | Complete |
| INFRA-03 | Phase 1 | Complete |
| INFRA-04 | Phase 1 | Complete |
| INFRA-05 | Phase 7 | Pending |
| INFRA-06 | Phase 1 | Complete |
| INFRA-07 | Phase 1 | Complete |
| INFRA-08 | Phase 7 | Pending |
| INFRA-09 | Phase 1 | Complete |

**Coverage:**
- v1 requirements: 73 total
- Mapped to phases: 73
- Unmapped: 0

---
*Requirements defined: 2026-02-17*
*Last updated: 2026-02-17 after Phase 2 completion*
