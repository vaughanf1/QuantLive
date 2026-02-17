# GoldSignal — Automated XAUUSD Trade Signal System

## What This Is

A production-grade automated trade signal system for XAUUSD (Gold) that behaves like a professional trading desk. The system fetches live market data, runs multiple rule-based strategies simultaneously, dynamically selects the best performer via rolling backtests, generates high-conviction trade signals with entry/SL/TP, sends formatted alerts to Telegram, overlays signals on TradingView charts, automatically tracks whether TP or SL is hit, and continuously improves via a performance feedback loop. Designed for a swing-intraday trader targeting 200–500 pip moves on gold.

## Core Value

Deliver 1–2 high-conviction, statistically validated XAUUSD trade signals per day — with full automation from signal generation through outcome tracking — so the trader never misses a quality setup and always knows what's working.

## Requirements

### Validated

(None yet — ship to validate)

### Active

- [ ] Fetch live and historical XAUUSD price data via reliable market data API
- [ ] Run 3+ rule-based trading strategies simultaneously (Liquidity Sweep Reversal, Trend Continuation, Breakout Expansion)
- [ ] Determine optimal multi-timeframe structure per strategy for swing-intraday 200–500 pip targets
- [ ] Automatically backtest each strategy on rolling 30–60 day window
- [ ] Calculate backtest metrics: win rate, profit factor, Sharpe ratio, max drawdown, expectancy
- [ ] Dynamically select best-performing strategy for signal generation
- [ ] Generate trade signals with: direction, entry, SL, TP1, TP2, R:R, confidence score, strategy name, reasoning
- [ ] Adapt TP/SL distances to volatility using ATR
- [ ] Avoid directional bias (prevent "always buy" problem)
- [ ] Apply session filters (London/NY) where appropriate per strategy
- [ ] Send formatted trade signals to Telegram via Bot API
- [ ] Integrate TradingView charts with live XAUUSD data
- [ ] Programmatically overlay entry points, SL lines, TP levels, and key levels on TradingView charts when signals fire
- [ ] Monitor price after signal to detect TP1 hit, TP2 hit, SL hit, or time expiry
- [ ] Log all trade outcomes automatically to PostgreSQL (no manual input)
- [ ] Store market candles, strategies, backtest results, signals, outcomes, and performance metrics in database
- [ ] Use trade outcomes to update performance stats and influence future strategy selection
- [ ] Detect degrading strategies and reduce their weighting
- [ ] Run 24/7 on Railway without manual execution

### Out of Scope

- AI/ML candle prediction models — system is rule-based and statistically testable
- Automated broker execution — signals only, user executes manually
- Multi-asset support — XAUUSD only for v1
- Mobile app — Telegram is the delivery channel
- Social/copy trading features — single user system
- Options or derivatives strategies — spot gold only

## Context

**Trading style:** Swing-intraday on XAUUSD. Targets 200–500 pips ($2–$5 moves on gold). Looking for 1–2 high-conviction setups per day, quality over quantity.

**Strategy philosophy:** Professional, rule-based strategies with defined entry logic, stop placement, TP logic, invalidation conditions, and optional session filters. No AI guessing. Every strategy must be backtestable and produce measurable edge.

**Three core strategies:**

1. **Liquidity Sweep Reversal** — Detects stop hunts below/above key levels, waits for market structure shift, enters on confirmation. Classic smart money concept.

2. **Trend Continuation** — Uses EMA/VWAP trend filter, identifies pullbacks within established trends, enters on momentum confirmation. Bread-and-butter trend following.

3. **Breakout Expansion** — Detects consolidation ranges, identifies volatility expansion, optional retest entry. Captures range breakouts with momentum.

**TradingView integration:** Embed TradingView charting widgets or use their library to display live gold charts. When a signal fires, automatically overlay visual markers — entry arrows, SL/TP horizontal lines, key levels — so the trader can perform technical analysis alongside the signals.

**Data source:** To be determined during research phase. Candidates include TwelveData, Oanda, Polygon.io, AlphaVantage. Needs reliable XAUUSD data at multiple timeframes with reasonable rate limits and cost.

**Deployment:** Railway for both the FastAPI backend and PostgreSQL database. Background workers or cron for 24/7 scheduling. Must be self-running with no manual intervention.

## Constraints

- **Tech stack**: Python (FastAPI), PostgreSQL, Railway, Telegram Bot API, vectorbt or Backtrader — specified by user
- **No AI prediction**: All strategies must be rule-based with statistical edge, not ML models
- **Signals only**: No broker API integration for v1 — user executes trades manually
- **Single asset**: XAUUSD only — system is purpose-built for gold
- **Budget-conscious**: Use free or low-cost data APIs where possible, Railway's starter pricing
- **Latency tolerance**: Swing-intraday style means sub-second execution speed is not critical, but signal detection should be within minutes of setup forming

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| Rule-based strategies only | Backtestable, transparent, no black-box risk | — Pending |
| 1–2 signals/day target | Quality over quantity matches trading style | — Pending |
| Telegram as delivery channel | Instant mobile alerts, simple integration | — Pending |
| Railway for deployment | Managed hosting, integrated PostgreSQL, simple scaling | — Pending |
| Signals only (no auto-execute) | Reduces risk, keeps trader in control | — Pending |
| TradingView for charting | Industry standard, embeddable widgets, visual signal overlay | — Pending |
| Data source TBD | Needs research to find best gold data coverage vs cost | — Pending |

---
*Last updated: 2026-02-17 after initialization*
