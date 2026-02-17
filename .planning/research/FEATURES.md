# Features Research: Automated XAUUSD Trade Signal System

> **Research Type:** Project Research — Features dimension
> **Date:** 2026-02-17
> **Context:** Greenfield build of a production-grade automated trade signal system for XAUUSD (Gold). System runs 3 rule-based strategies (Liquidity Sweep Reversal, Trend Continuation, Breakout Expansion), backtests on rolling windows, dynamically selects best strategy, generates 1-2 high-conviction signals/day with 200-500 pip targets. Sends Telegram alerts, overlays signals on TradingView charts, auto-tracks outcomes, self-improves via feedback loop. Swing-intraday trading style. Signals only — no auto-execution.

---

## Table Stakes (Must-Have or Users Leave)

These are baseline features that any credible automated trade signal system must have. Without them, the system is not viable.

### 1. Signal Generation Core

| Feature | Description | Complexity | Dependencies |
|---------|-------------|------------|--------------|
| **Clear entry price** | Exact price level for trade entry | Low | Price data feed |
| **Stop-loss level** | Defined invalidation level for every signal | Low | Entry price, ATR/volatility |
| **Take-profit target(s)** | At least one profit target per signal; ideally 2-3 scaled targets | Low | Entry price, S/R levels |
| **Direction (Long/Short)** | Unambiguous buy or sell signal | Low | Strategy engine |
| **Signal timestamp** | Exact UTC time of signal generation | Low | System clock |
| **Timeframe context** | Which timeframe(s) the signal is based on (e.g., H1/H4) | Low | Strategy config |
| **Risk-reward ratio** | Calculated R:R for each signal (minimum 1:2 for credibility) | Low | SL/TP levels |
| **Signal confidence/conviction score** | Numeric or categorical rating of signal strength | Medium | Backtester, strategy scoring |
| **Pair identification** | Clearly labeled as XAUUSD with current spread context | Low | Broker data |

### 2. Backtesting Engine

| Feature | Description | Complexity | Dependencies |
|---------|-------------|------------|--------------|
| **Historical data ingestion** | Ability to load and process OHLCV candle data across multiple timeframes | Medium | Data provider API |
| **Strategy parameter definition** | Configurable parameters for each strategy (thresholds, periods, etc.) | Medium | Strategy framework |
| **Rolling-window backtesting** | Test strategies over sliding time windows (not just one static period) | High | Historical data, compute |
| **Core performance metrics** | Win rate, profit factor, max drawdown, Sharpe ratio, avg R:R | Medium | Trade log |
| **Out-of-sample validation** | Separate test data from training data to prevent overfitting | Medium | Data splitting logic |
| **Trade-by-trade log** | Full record of every simulated trade with entry/exit/PnL | Medium | Backtester core |

### 3. Risk Management

| Feature | Description | Complexity | Dependencies |
|---------|-------------|------------|--------------|
| **Per-trade risk limit** | Cap risk per signal (e.g., 1-2% of account) | Low | Account size input |
| **Daily loss limit** | Stop generating signals after X% daily drawdown | Medium | Real-time P&L tracking |
| **Maximum concurrent signals** | Limit number of open signals at once (e.g., max 2-3) | Low | Signal state tracker |
| **Position sizing calculator** | Calculate lot size based on SL distance and risk percentage | Medium | SL level, account size, pip value |
| **Drawdown monitoring** | Track running and maximum drawdown in real time | Medium | Trade outcome tracker |

### 4. Alert Delivery

| Feature | Description | Complexity | Dependencies |
|---------|-------------|------------|--------------|
| **Telegram bot integration** | Send formatted signal alerts via Telegram | Medium | Telegram Bot API |
| **Structured alert format** | Consistent, parseable message format (direction, entry, SL, TP, R:R) | Low | Signal generator |
| **Signal status updates** | Notify on TP hit, SL hit, or signal expiry | Medium | Price monitoring, trade tracker |
| **Delivery confirmation** | Verify alerts were actually sent and received | Low | Telegram API response |

### 5. Performance Tracking

| Feature | Description | Complexity | Dependencies |
|---------|-------------|------------|--------------|
| **Win/loss record** | Track outcome of every signal | Low | Price monitoring |
| **Running P&L** | Cumulative pip and percentage returns | Low | Trade outcomes |
| **Basic statistics dashboard** | Win rate, avg win, avg loss, profit factor, max drawdown | Medium | Trade log |
| **Signal outcome auto-detection** | Automatically detect when SL or TP is hit | Medium | Real-time price feed |

### 6. Market Session Awareness

| Feature | Description | Complexity | Dependencies |
|---------|-------------|------------|--------------|
| **Session identification** | Know whether it's Asian, London, or New York session | Low | UTC clock, session schedule |
| **Session-based signal filtering** | Only generate signals during high-liquidity sessions for gold (London/NY overlap is critical for XAUUSD) | Medium | Session identifier, strategy config |
| **Session open/close awareness** | Understand that gold volatility clusters around London open, NY open, and session overlaps | Low | Session schedule |

### 7. TradingView Integration (Basic)

| Feature | Description | Complexity | Dependencies |
|---------|-------------|------------|--------------|
| **Pine Script indicator for signal overlay** | Display entry, SL, TP lines on TradingView chart | Medium | Pine Script v5/v6 |
| **Webhook receiver** | Accept TradingView webhook alerts as signal triggers or confirmations | Medium | Server endpoint, TradingView alert config |
| **Visual signal markers** | Plot arrows/labels on chart at signal points | Medium | Pine Script |

---

## Differentiators (Competitive Advantage)

These features separate a professional system from the hundreds of generic signal bots. They create defensible value.

### 8. Dynamic Strategy Selection

| Feature | Description | Complexity | Dependencies |
|---------|-------------|------------|--------------|
| **Volatility regime detection** | Classify market as low/medium/high volatility (ATR-based, Bollinger Band width, or ADX) | High | Price data, indicator library |
| **Regime-strategy mapping** | Automatically select which strategy to deploy based on current regime (e.g., Breakout in high-vol, Trend Continuation in trending, Liquidity Sweep in ranging) | High | Regime detector, backtester scores |
| **Strategy performance scoring** | Score each strategy's recent performance on a rolling basis and weight signal generation accordingly | High | Rolling backtest engine |
| **Auto-deactivation of losing strategies** | Temporarily disable strategies that fall below performance thresholds | Medium | Strategy scorer |
| **Strategy blending** | When multiple strategies agree on direction, increase conviction score | Medium | Multi-strategy engine |

### 9. Self-Improvement Feedback Loop

| Feature | Description | Complexity | Dependencies |
|---------|-------------|------------|--------------|
| **Outcome-based parameter tuning** | Adjust strategy parameters based on recent trade outcomes (e.g., tighten SL if too many full-SL hits) | High | Trade log, optimization engine |
| **Signal quality post-mortem** | Analyze why signals failed — was it SL too tight? Bad entry timing? Wrong session? | High | Detailed trade log with market context |
| **Rolling performance windows** | Evaluate strategy performance on 7-day, 30-day, 90-day windows | Medium | Trade log, time-series analysis |
| **Adaptive confidence scoring** | Update conviction model based on actual hit rates per confidence level | High | Historical confidence vs. outcome data |
| **Parameter drift detection** | Alert when optimized parameters shift significantly from prior windows (signals regime change) | High | Rolling optimization engine |

### 10. Multi-Timeframe Confluence Analysis

| Feature | Description | Complexity | Dependencies |
|---------|-------------|------------|--------------|
| **HTF trend alignment** | Check if signal aligns with higher timeframe trend (e.g., H4 signal confirmed by Daily trend) | Medium | Multi-TF data feed |
| **LTF entry refinement** | Use lower timeframe for precise entry within HTF signal zone | High | Multi-TF data, sub-candle analysis |
| **Confluence scoring** | Score signals higher when multiple timeframes and indicators agree | Medium | Multi-TF analysis, scoring model |
| **Key level identification across TFs** | Identify major S/R, FVGs, order blocks that appear on multiple timeframes | High | Multi-TF price action analysis |

### 11. Gold-Specific Intelligence

| Feature | Description | Complexity | Dependencies |
|---------|-------------|------------|--------------|
| **DXY (Dollar Index) correlation monitoring** | Track inverse correlation between gold and USD; flag divergences | Medium | DXY data feed |
| **US Treasury yield awareness** | Monitor real yields as a leading indicator for gold direction | Medium | Bond data feed |
| **FOMC/NFP/CPI calendar integration** | Flag high-impact USD events that will move gold; suppress or adjust signals around them | Medium | Economic calendar API |
| **Gold-specific volatility patterns** | Recognize that gold tends to spike at London open, consolidate mid-London, then move at NY open | Medium | Session volatility profiles |
| **COT (Commitment of Traders) data** | Monitor institutional positioning in gold futures for directional bias | High | COT data feed (weekly) |
| **Seasonal gold patterns** | Incorporate known seasonal tendencies (e.g., gold strength in Q1, weakness in Q3) | Low | Historical seasonal data |

### 12. Advanced Risk Management

| Feature | Description | Complexity | Dependencies |
|---------|-------------|------------|--------------|
| **Trailing stop logic** | Move SL to breakeven after partial TP; trail SL behind price using ATR or structure | High | Real-time price monitoring |
| **Partial TP management** | Signal scaled exits (e.g., close 50% at TP1, 30% at TP2, 20% at TP3) | Medium | Multi-target signal format |
| **Correlation-based exposure limits** | If already long gold, don't add another long signal (or reduce size) | Medium | Open signal tracker |
| **Volatility-adjusted position sizing** | Automatically reduce size in high-volatility regimes; increase in low-vol | High | Volatility regime detector, position sizer |
| **Equity curve analysis** | Detect when the system is in a drawdown phase and reduce signal frequency/size | High | Running P&L tracker, statistical analysis |
| **Risk of ruin calculation** | Calculate probability of account blow-up given current strategy performance | Medium | Win rate, R:R, position size data |

### 13. Advanced Performance Analytics

| Feature | Description | Complexity | Dependencies |
|---------|-------------|------------|--------------|
| **Session-based performance breakdown** | Which sessions produce the best signals? | Medium | Trade log with session tags |
| **Strategy-specific analytics** | Performance metrics broken down per strategy | Medium | Trade log with strategy tags |
| **Expectancy calculation** | (Win% x Avg Win) - (Loss% x Avg Loss) per strategy and overall | Low | Trade log |
| **Monte Carlo simulation** | Simulate thousands of possible equity curves to understand realistic outcomes | High | Trade log, statistical engine |
| **Time-in-trade analysis** | How long do winning vs. losing trades take? Optimize holding periods | Medium | Trade timestamps |
| **Heatmap of signal performance** | Visual grid showing performance by day-of-week and session | Medium | Trade log, visualization |

### 14. TradingView Integration (Advanced)

| Feature | Description | Complexity | Dependencies |
|---------|-------------|------------|--------------|
| **Interactive signal dashboard on chart** | Pine Script table showing live signal status, P&L, active strategy | High | Pine Script v5/v6, data bridge |
| **Historical signal replay** | Visualize past signals on chart with outcomes color-coded (green = TP hit, red = SL hit) | Medium | Signal log, Pine Script |
| **Multi-timeframe signal visualization** | Show HTF context (trend direction, key levels) alongside LTF signals | High | Pine Script multi-TF |
| **Signal zone highlighting** | Shade entry zones, SL zones, and TP zones rather than just lines | Medium | Pine Script boxes/fills |
| **Real-time conviction indicator** | Display current strategy confidence and regime as an on-chart panel | Medium | Pine Script, data bridge |

### 15. Monitoring and Observability

| Feature | Description | Complexity | Dependencies |
|---------|-------------|------------|--------------|
| **System health dashboard** | Monitor data feed status, strategy engine uptime, alert delivery success | Medium | Monitoring infrastructure |
| **Data feed integrity checks** | Detect gaps, stale data, or anomalous price spikes | Medium | Data validation logic |
| **Latency monitoring** | Track time from signal generation to alert delivery | Low | Timestamps at each stage |
| **Error alerting** | Notify (via Telegram or other channel) when system components fail | Medium | Error handling, alert system |
| **Scheduled status reports** | Daily/weekly automated summary of system status and performance | Medium | Reporting engine, scheduler |

---

## Anti-Features (Things to Deliberately NOT Build)

These are features that seem tempting but would add complexity, risk, or distraction without proportional value. Deliberately excluding them is a strategic choice.

| Anti-Feature | Why NOT to Build It | Risk if Built |
|-------------|-------------------|---------------|
| **Auto-execution / broker integration** | Signals-only system by design. Auto-execution adds massive regulatory, liability, and technical risk (slippage, broker API failures, order management). Keep the human in the loop. | Regulatory exposure, catastrophic loss from bugs, broker dependency |
| **Machine learning / neural network models** | Rule-based strategies are interpretable, debuggable, and auditable. ML models are black boxes that overfit easily on financial data. The feedback loop provides structured improvement without ML complexity. | Overfitting, unexplainable failures, false confidence, massive compute cost |
| **Social/copy trading features** | Community features distract from core signal quality. Building social infrastructure is a separate product. | Scope creep, moderation burden, liability |
| **Multi-pair support (initially)** | XAUUSD specialization is a strength. Each pair has unique characteristics. Spreading across pairs dilutes gold-specific intelligence. | Diluted domain expertise, multiplied maintenance, weaker signals |
| **Paid subscription / paywall system** | Monetization infrastructure is premature. Build a system that works first. | Distraction from core product, premature optimization |
| **Real-time tick-by-tick processing** | Candle-based analysis (M15/H1/H4) is sufficient for swing-intraday. Tick data adds orders of magnitude of complexity and cost for marginal benefit at this trading style. | Infrastructure cost, latency requirements, over-engineering |
| **Custom indicator builder / strategy IDE** | This is a signal system, not a platform. Users don't need to build their own strategies — the system's value is its curated, tested strategies. | Scope explosion, support burden, quality dilution |
| **Paper trading simulation mode** | Backtesting covers validation. Paper trading adds real-time simulation complexity with minimal additional insight over forward-testing with small size. | Engineering effort with low marginal value |
| **Mobile app** | Telegram IS the mobile app. Building a native app is a massive undertaking that duplicates Telegram's alert delivery. | Massive scope creep, maintenance burden, app store overhead |
| **Sentiment analysis from social media** | Social sentiment is noisy, lagging, and unreliable for gold. Institutional flows and macro data matter far more for XAUUSD. | Noise over signal, API costs, false confidence |
| **Cryptocurrency pair support** | Completely different market microstructure, 24/7 trading, different volatility profile. Not transferable from gold expertise. | Distraction, different domain, diluted focus |

---

## Feature Dependencies Map

```
Data Feed (OHLCV)
├── Historical Data Ingestion
│   └── Backtesting Engine
│       ├── Rolling-Window Backtesting
│       │   └── Strategy Performance Scoring
│       │       └── Dynamic Strategy Selection
│       │           └── Regime-Strategy Mapping
│       ├── Out-of-Sample Validation
│       └── Core Performance Metrics
│           └── Self-Improvement Feedback Loop
│               ├── Outcome-Based Parameter Tuning
│               └── Adaptive Confidence Scoring
├── Real-Time Price Feed
│   ├── Signal Generation Core
│   │   ├── Entry / SL / TP Levels
│   │   ├── Conviction Scoring
│   │   └── Multi-Timeframe Confluence
│   ├── Signal Outcome Auto-Detection
│   │   └── Performance Tracking
│   │       └── Advanced Analytics
│   ├── Trailing Stop Logic
│   └── Volatility Regime Detection
│       ├── Volatility-Adjusted Position Sizing
│       └── Strategy Selection
├── Session Scheduler
│   ├── Session-Based Filtering
│   └── Session Performance Breakdown
└── External Data Feeds
    ├── DXY Correlation
    ├── Treasury Yields
    ├── Economic Calendar (FOMC/NFP/CPI)
    └── COT Data

Alert System (Telegram)
├── Signal Delivery
├── Status Updates (TP/SL hit)
├── Error Alerting
└── Performance Reports

TradingView Integration
├── Pine Script Signal Overlay (Basic)
│   └── Interactive Dashboard (Advanced)
├── Webhook Receiver
└── Historical Signal Replay
```

---

## Implementation Priority Tiers

### Tier 1: Foundation (Build First)
1. Data feed ingestion (OHLCV for XAUUSD across M15, H1, H4, D1)
2. Three core strategies (Liquidity Sweep Reversal, Trend Continuation, Breakout Expansion)
3. Signal generation with entry/SL/TP/R:R
4. Basic backtesting engine with rolling windows
5. Telegram alert delivery with structured format
6. Signal outcome auto-detection
7. Session awareness (London/NY/Asian)
8. Per-trade risk limits and position sizing

**Complexity:** Medium | **Timeline driver:** Data feed reliability and strategy codification

### Tier 2: Intelligence (Build Second)
1. Volatility regime detection
2. Dynamic strategy selection based on regime + rolling backtest scores
3. Multi-timeframe confluence analysis
4. Conviction scoring system
5. Basic performance tracking dashboard
6. Daily loss limits and drawdown monitoring
7. Pine Script signal overlay for TradingView

**Complexity:** High | **Dependencies:** Tier 1 fully operational with data flowing

### Tier 3: Edge (Build Third)
1. Self-improvement feedback loop (parameter tuning from outcomes)
2. Gold-specific intelligence (DXY correlation, economic calendar)
3. Advanced risk management (trailing stops, partial TPs, volatility-adjusted sizing)
4. Advanced TradingView dashboard
5. Session-based and strategy-based performance analytics
6. System health monitoring and observability

**Complexity:** High | **Dependencies:** Tier 2 with sufficient trade history for meaningful analysis

### Tier 4: Polish (Build Last)
1. Monte Carlo simulation
2. COT data integration
3. Seasonal pattern overlays
4. Equity curve analysis for position sizing
5. Historical signal replay on TradingView
6. Heatmap visualizations

**Complexity:** Medium-High | **Dependencies:** Tier 3 with 3+ months of live signal data

---

## Key Risks and Considerations

| Risk | Mitigation |
|------|-----------|
| **Overfitting in backtesting** | Rolling windows, out-of-sample validation, forward-walk analysis |
| **Data quality issues** | Multiple data source validation, gap detection, spike filtering |
| **Gold-specific liquidity gaps** | Weekend gap handling, holiday calendar, session-aware signal suppression |
| **Telegram API rate limits** | Message queuing, batching updates, fallback channels |
| **TradingView Pine Script limitations** | Pine Script has limited external data access; webhook bridge needed for real-time signal data |
| **Strategy decay** | Continuous rolling evaluation, automatic deactivation thresholds, parameter drift alerts |
| **Look-ahead bias in backtesting** | Strict chronological data processing, no future data leakage in indicator calculations |
| **Survivorship bias** | Track all signals including expired/cancelled, not just completed trades |

---

## Competitive Landscape Summary

| Feature Category | Generic Signal Bots | Professional Signal Services | This System (Target) |
|-----------------|---------------------|------------------------------|---------------------|
| Signal quality (entry/SL/TP) | Basic | Good | Excellent (multi-strategy, confluence-scored) |
| Backtesting | None or static | Basic | Rolling-window with regime awareness |
| Risk management | None | Basic (fixed lot) | Dynamic (volatility-adjusted, drawdown-aware) |
| Self-improvement | None | Manual review | Automated feedback loop |
| Gold specialization | Generic multi-pair | Some gold focus | Deep XAUUSD specialization |
| Transparency | Black box | Some explanation | Full trade log, strategy attribution, conviction reasoning |
| TradingView integration | None or basic | Alerts only | Full chart overlay with interactive dashboard |
| Session awareness | None | Manual | Automated session filtering and performance tracking |

---

*This research informs requirements definition. Each feature should be validated against actual user needs and technical feasibility before committing to implementation.*
