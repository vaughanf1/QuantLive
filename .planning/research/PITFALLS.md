# PITFALLS.md — Automated XAUUSD Trading Signal System

> **Purpose:** Catalogue domain-specific pitfalls for an automated XAUUSD trade signal system (Python/FastAPI on Railway, rule-based strategies, rolling backtests, Telegram alerts, self-improvement loop). Each pitfall includes warning signs, prevention strategy, and phase mapping.
>
> **Date:** 2026-02-17

---

## Table of Contents

1. [Backtesting Pitfalls](#1-backtesting-pitfalls)
2. [Gold-Specific Market Pitfalls](#2-gold-specific-market-pitfalls)
3. [Strategy Selection Pitfalls](#3-strategy-selection-pitfalls)
4. [Data Quality Pitfalls](#4-data-quality-pitfalls)
5. [Confidence Score Pitfalls](#5-confidence-score-pitfalls)
6. [Outcome Tracking Pitfalls](#6-outcome-tracking-pitfalls)
7. [Railway Deployment Pitfalls](#7-railway-deployment-pitfalls)
8. [Telegram Integration Pitfalls](#8-telegram-integration-pitfalls)
9. [Database and Performance Pitfalls](#9-database-and-performance-pitfalls)
10. [Session Handling Pitfalls](#10-session-handling-pitfalls)
11. [System-Level / Architectural Pitfalls](#11-system-level--architectural-pitfalls)

---

## 1. Backtesting Pitfalls

### 1.1 Look-Ahead Bias

**What goes wrong:** The backtester uses data that would not have been available at the time a signal was generated. This is the single most common and most destructive bug in trading system development. Examples include using the close price of a candle to decide whether to enter during that candle, using future ATR values, or calculating indicators on an entire dataset before iterating through it.

**Warning signs:**
- Backtest results are dramatically better than live paper-trade results (> 30% divergence in win rate or profit factor).
- Strategies that "can't lose" in backtests — win rates above 75% on gold with 200+ pip targets are a red flag.
- Indicator calculations use `DataFrame` operations that implicitly include future rows (e.g., `df['sma'] = df['close'].rolling(20).mean()` calculated once before the loop, then shifting the entry logic forward by one bar but forgetting to shift the indicator too).

**Prevention strategy:**
- Use an event-driven backtester that processes one bar at a time, never exposing future data. Each bar's indicator values must be calculated using only data up to and including that bar's open (not close, since the candle is still forming).
- For every indicator, verify: "At the moment this signal fires, has this candle closed yet?" If the signal fires on a candle close, entry price must be the next candle's open (with slippage).
- Implement a "future leak detector": after backtesting, compare every signal's timestamp against the timestamps of data used to compute its indicators. Log any violation.
- **Phase:** Must be addressed in Phase 1 (backtesting engine build). Non-negotiable before any strategy evaluation.

### 1.2 Survivorship Bias in Strategy Selection

**What goes wrong:** Only strategies that performed well in development are carried forward. Failed strategy variants are forgotten, making the surviving strategies appear more robust than they are. With 3 rule-based strategies, if you tested 30 variants to arrive at 3, you have a 10:1 survivorship ratio.

**Warning signs:**
- No record of how many strategy variants were tested before settling on the final 3.
- The "best" strategy was found by tweaking parameters until results looked good.
- No out-of-sample holdout period was used during development.

**Prevention strategy:**
- Log every strategy variant tested, including parameters and results. Maintain a strategy graveyard.
- Use walk-forward analysis: divide historical data into in-sample (for parameter fitting) and out-of-sample (for validation). Never optimize on the out-of-sample set.
- Reserve the most recent 3-6 months of data as a final holdout that is never touched during development.
- Apply a Bonferroni-style correction: if you tested N variants, require statistical significance at the p < 0.05/N level, not p < 0.05.
- **Phase:** Phase 1 (strategy development). Document the full strategy search process.

### 1.3 Overfitting to Recent Data / Curve Fitting

**What goes wrong:** Strategy parameters are tuned to fit recent gold price action perfectly. Gold's regime changes frequently — the 2024-2025 bull run (1800 to 2900+) behaves nothing like 2021-2022 consolidation. A strategy tuned to trending conditions will fail in ranges and vice versa.

**Warning signs:**
- Strategy has more than 5-7 tunable parameters for a simple rule-based system.
- Backtest performance degrades sharply when the lookback window is extended by 6-12 months.
- Parameters are suspiciously precise (e.g., RSI threshold of 67.3 instead of 70).
- Strategy works on XAUUSD but fails on XAGUSD, XAUEUR, or other correlated instruments.

**Prevention strategy:**
- Limit each strategy to 3-5 core parameters maximum.
- Use round numbers for thresholds (RSI 70, not 67.3).
- Test on multiple timeframes and at least one correlated instrument (XAGUSD) as a sanity check.
- Require strategies to be profitable across at least 3 distinct market regimes (trending up, trending down, range-bound) even if profit varies.
- Use rolling walk-forward optimization (e.g., 6-month in-sample, 2-month out-of-sample, rolled forward monthly) rather than a single backtest period.
- **Phase:** Phase 1 (strategy development) and Phase 3 (rolling backtest implementation).

### 1.4 Ignoring Transaction Costs in Backtests

**What goes wrong:** Backtests assume zero spread, zero slippage, and instant fills. Gold spreads vary from 10-15 pips during London/NY to 40-80+ pips during Asian session or news events. With 200-500 pip targets, this seems minor but compounds — especially if stops are tight relative to targets.

**Warning signs:**
- Backtest win rate drops by more than 5 percentage points when realistic spread is added.
- Many trades are near-breakeven, meaning spread determines the outcome.
- No spread model exists in the backtester.

**Prevention strategy:**
- Model spread as a function of session: Asian = 30-50 pips, London = 10-20 pips, NY = 10-20 pips, News = 50-100+ pips. (These are for standard accounts; verify with your broker.)
- Add 5-10 pips of slippage per trade on top of spread as a conservative buffer.
- Include swap/rollover costs for positions held overnight.
- Run backtests at multiple spread assumptions (optimistic, realistic, pessimistic) and require profitability at the pessimistic level.
- **Phase:** Phase 1 (backtesting engine). Build spread modeling into the engine from day one.

---

## 2. Gold-Specific Market Pitfalls

### 2.1 Spread Blowout During High-Impact News

**What goes wrong:** NFP, FOMC, CPI releases cause gold spreads to widen to 100-300+ pips for seconds to minutes. A signal generated just before news will show a vastly different entry price than expected. Stop losses can be hit by spread alone, not by price movement.

**Warning signs:**
- Signals firing within 15 minutes of scheduled high-impact news releases.
- Outcome tracker shows disproportionate losses around news times.
- Live results diverge from backtests specifically around news events.

**Prevention strategy:**
- Maintain an economic calendar (ForexFactory API or similar). Implement a hard blackout window: no new signals within 30 minutes before and 15 minutes after NFP, FOMC rate decisions, CPI, PPI, and Fed speeches.
- For FOMC, extend the blackout to 60 minutes post-release (gold can whipsaw for extended periods).
- If a position is already open during news, do NOT move stops tighter — the spread spike will stop you out. Consider widening stops temporarily or simply accepting the risk with the original stop.
- Log all news-adjacent trades separately for performance analysis.
- **Phase:** Phase 2 (signal generation logic) — build the news filter early. Phase 4 (self-improvement) — analyze news-adjacent performance.

### 2.2 DXY / Real Yields Correlation Breakdown

**What goes wrong:** Gold has a historically strong inverse correlation with the US Dollar Index (DXY) and real yields (TIPS). Many strategies implicitly or explicitly rely on this. But the correlation breaks down periodically — in 2024-2025, gold rallied alongside a strong dollar due to central bank buying. Building strategies that assume the correlation is stable will fail during these regime shifts.

**Warning signs:**
- Strategy uses DXY or yield data as a filter/confirmation, and performance degrades when correlation inverts.
- Rolling 30-day correlation between XAUUSD and DXY flips positive for extended periods.
- Strategy has no mechanism to detect or adapt to correlation regime changes.

**Prevention strategy:**
- If using DXY/yields as inputs, make the correlation dynamic: calculate a rolling correlation coefficient and reduce signal confidence when correlation deviates from historical norms.
- Better yet: design strategies that work on gold's price action alone, using DXY/yields only as a secondary filter with a clear "off switch" when correlation breaks.
- Track the rolling XAUUSD-DXY correlation as a system health metric. Alert when it flips sign for >5 consecutive days.
- **Phase:** Phase 2 (strategy logic) and Phase 4 (self-improvement metrics).

### 2.3 Gap Risk (Weekend and Holiday Gaps)

**What goes wrong:** Gold trades nearly 24 hours on weekdays but closes Friday 5pm ET to Sunday 5pm ET. Weekend gaps of 200-500+ pips occur regularly during geopolitical events. Positions held over the weekend can blow through stop losses. Additionally, gold can gap on certain holidays when some markets are closed but others aren't (e.g., US holidays when Asian markets trade).

**Warning signs:**
- Backtests show trades that would have been stopped out by a weekend gap, but the backtester exits at the stop price rather than the gap-open price.
- No logic to handle Friday position management.
- Stop losses are tighter than typical weekend gap sizes.

**Prevention strategy:**
- Implement a Friday cutoff: no new signals after Friday 12:00 ET. For existing positions, either close before Friday 4:30 PM ET or widen stops to account for gap risk.
- In backtests, model weekend gaps correctly: if price opens Monday beyond the stop level, record the exit at the Monday open price, not the stop price.
- Track average and maximum weekend gaps in gold over the past 2 years to calibrate risk.
- **Phase:** Phase 1 (backtester must handle gaps correctly) and Phase 2 (signal timing logic).

### 2.4 Gold's Extreme Volatility Regime Shifts

**What goes wrong:** Gold can transition from 500-pip daily ranges to 1500+ pip daily ranges within days (as seen during March 2020, or the 2024-2025 rally). Fixed pip targets (200-500) that work in normal volatility may be too tight in high-vol regimes (stopped out before target) or too wide in low-vol regimes (never reached).

**Warning signs:**
- ATR(14) on 4H candles varies by more than 2x across the backtest period.
- Win rate varies dramatically across different volatility regimes.
- Fixed pip targets produce cluster of near-misses in low-vol periods and cluster of blown-stops in high-vol periods.

**Prevention strategy:**
- Use ATR-based targets and stops instead of fixed pips. Example: target = 2.5 * ATR(14), stop = 1.0 * ATR(14). This auto-scales with volatility.
- If fixed pips are preferred, implement a volatility filter: reduce position sizing or skip signals when ATR(14) is outside a defined band.
- Log volatility regime (low/normal/high based on ATR percentile) for every signal to analyze regime-specific performance.
- **Phase:** Phase 1 (strategy design) and Phase 3 (dynamic strategy selection should account for volatility regime).

---

## 3. Strategy Selection Pitfalls

### 3.1 Recency Bias in Rolling Backtest Selection

**What goes wrong:** The dynamic strategy selector weights recent performance too heavily. A strategy that had a lucky streak of 5 winning trades becomes the "selected" strategy, while a more robust strategy that had a normal drawdown is benched. This is essentially chasing performance — the same mistake retail traders make manually.

**Warning signs:**
- The selector frequently rotates between strategies (more than once per week).
- Selected strategy changes immediately after a single losing trade.
- Rolling backtest window is too short (less than 30 trades per strategy).

**Prevention strategy:**
- Require a minimum sample size before a strategy can be selected or deselected: at least 30-50 trades in the rolling window.
- Use a longer rolling window (60-90 days minimum) to smooth out short-term variance.
- Implement a "switching cost": a strategy must outperform the current selected strategy by a meaningful margin (e.g., 0.3 higher profit factor or 10%+ higher win rate) before a switch occurs. This prevents oscillation.
- Weight multiple metrics: don't select on win rate alone. Use a composite score including profit factor, max drawdown, Sharpe ratio, and consistency (e.g., percentage of profitable weeks).
- **Phase:** Phase 3 (strategy selector design). This is a critical design decision.

### 3.2 Strategy Switching Too Frequently (Whipsawing)

**What goes wrong:** The selector switches strategies so often that no strategy gets a fair evaluation period. Each strategy needs time to encounter its ideal market conditions. Switching every few days guarantees you'll always be using the wrong strategy — the one that just did well but is about to mean-revert.

**Warning signs:**
- Strategy switches more than 2-3 times per month.
- Performance of the selected strategy is worse than simply running all 3 strategies simultaneously.
- The system seems to always "just miss" the winning trades.

**Prevention strategy:**
- Implement a minimum holding period for strategy selection: once a strategy is selected, it stays selected for at least 1-2 weeks regardless of performance (unless it hits a hard drawdown limit).
- Compare system performance against a "naive ensemble" baseline that simply averages signals from all 3 strategies. If the selector can't beat the ensemble, the selector is adding negative value.
- Log every switch decision with the metrics that triggered it. Review monthly.
- **Phase:** Phase 3 (strategy selector) and Phase 4 (self-improvement — is the selector helping or hurting?).

### 3.3 Small Sample Size in Rolling Backtests

**What goes wrong:** With 1-2 signals per day and 3 strategies, each strategy generates roughly 10-20 signals per month. A 30-day rolling backtest gives you only 10-20 trades to evaluate each strategy. This is far too few for statistical significance. You cannot distinguish skill from luck with 15 trades.

**Warning signs:**
- Confidence intervals around strategy metrics are wider than the differences between strategies.
- Strategy rankings change dramatically when a single trade outcome changes.
- You cannot reject the null hypothesis that all 3 strategies have equal performance.

**Prevention strategy:**
- Extend the rolling window to 90-180 days to accumulate 50-100+ trades per strategy.
- Use a weighted approach: more recent trades weighted higher, but older trades still included.
- Calculate confidence intervals for all metrics. Only declare a strategy "better" when confidence intervals don't overlap.
- Consider running all 3 strategies in parallel (each generating signals independently) rather than selecting one, especially in early phases when sample sizes are small.
- **Phase:** Phase 3 (design the rolling backtest window size). Phase 4 (monitor statistical validity).

---

## 4. Data Quality Pitfalls

### 4.1 Missing Candles and Gaps in OHLCV Data

**What goes wrong:** Free and even paid data sources for gold regularly have missing candles — especially during Asian session low-liquidity periods, around rollovers (5pm ET), and during holidays. Missing candles cause indicator calculations to be wrong (SMA, RSI, ATR all shift) and can create phantom signals.

**Warning signs:**
- Candle timestamps are not evenly spaced (e.g., jumping from 02:00 to 02:30 on 15-minute data, skipping 02:15).
- Indicator values differ between your system and TradingView's charts.
- Sudden spikes in indicator values that don't correspond to real price moves.

**Prevention strategy:**
- Implement a data integrity check on every data fetch: verify expected candle count vs actual count for the time range. Log and alert on any gaps.
- Fill missing candles using the previous candle's close as OHLC with zero volume. This is imperfect but prevents indicator calculation errors.
- Cross-validate data against a second source (e.g., compare TradingView data with broker API data) at least weekly.
- Never generate a signal if more than 2% of candles in the indicator lookback period are missing.
- **Phase:** Phase 1 (data pipeline). Build validation into the data layer from the start.

### 4.2 Timezone Mismatches

**What goes wrong:** Different data sources use different timezones (UTC, ET, broker time which might be EET). A candle labeled "2026-02-17 00:00" in UTC is a different candle than "2026-02-17 00:00" in ET. If your backtester uses one timezone and your live system uses another, every indicator value will be wrong, every session filter will be wrong, and backtest results will be meaningless.

**Warning signs:**
- Daily candle OHLC values don't match between your system and TradingView.
- Session-based strategies (e.g., "trade only during London session") fire signals at the wrong times.
- Weekend candles appear in your data that shouldn't exist, or vice versa.

**Prevention strategy:**
- Standardize on UTC internally for all data storage and processing. Convert to local time only for display.
- Document the timezone of every data source explicitly.
- Write a test that verifies: "The daily candle for 2026-02-16 in our system has the same OHLC as TradingView's daily candle for the same date." Run this test in CI.
- For session filters, define sessions in UTC and convert: London = 08:00-16:00 UTC, NY = 13:00-22:00 UTC (adjust for DST).
- **Phase:** Phase 1 (data pipeline). Timezone bugs are the most insidious — they produce subtly wrong results that look plausible.

### 4.3 Tick Data vs. Candle Data Discrepancies

**What goes wrong:** Your backtester uses candle data (OHLCV) but your live system reacts to real-time price ticks. Within a candle, the high and low are visited in an unknown order. The backtester might assume a stop loss was hit before the target (or vice versa) when the opposite actually happened. This is called the "OHLC bar ambiguity" problem.

**Warning signs:**
- Trades where both the stop loss AND take profit were within the candle's range — the backtester had to guess which was hit first.
- Backtest results change significantly when you switch from "stop first" to "target first" assumptions.
- High-frequency of "ambiguous" trades (> 10% of total).

**Prevention strategy:**
- For ambiguous candles (where both stop and target are within the range), use the pessimistic assumption: stop was hit first. This gives conservative estimates.
- Alternatively, use tick data or 1-minute candles for order execution simulation even if the strategy operates on 15m/1H candles.
- Track and report the percentage of ambiguous trades in every backtest. If it exceeds 15%, the timeframe may be too coarse for the stop/target sizes.
- **Phase:** Phase 1 (backtester design). Choose the ambiguity resolution method before running any backtests.

### 4.4 Data Source Reliability and API Downtime

**What goes wrong:** Your live system depends on a data API (e.g., broker API, TradingView data feed, or a third-party provider). If the API goes down during a critical moment, you either miss signals or generate signals on stale data. Gold is particularly unforgiving — prices can move 200+ pips in minutes.

**Warning signs:**
- Signals generated on data that is more than 1 candle period old.
- No health check for data freshness.
- Single data source with no fallback.

**Prevention strategy:**
- Implement a data freshness check: if the latest candle is more than 2x the expected interval old, suppress all signal generation and alert.
- Have at least 2 data sources configured. Primary source is used by default; secondary is used if primary fails the freshness check.
- Log data source latency and availability metrics. Alert if availability drops below 99.5% over a rolling 24-hour period.
- **Phase:** Phase 2 (live data integration) and Phase 5 (production hardening).

---

## 5. Confidence Score Pitfalls

### 5.1 False Precision in Confidence Scores

**What goes wrong:** The system reports "82.3% confidence" on a signal, implying a level of precision that the underlying model cannot support. With small sample sizes and rule-based strategies, confidence scores are estimates with wide error bars. Presenting them as precise numbers creates a false sense of reliability and misleads users.

**Warning signs:**
- Confidence scores reported to 1+ decimal places.
- Users treat high-confidence signals as "guaranteed winners" and oversize positions.
- No calibration check: do 80% confidence signals actually win 80% of the time?

**Prevention strategy:**
- Report confidence in buckets: Low / Medium / High, or 60% / 70% / 80% — never with decimal precision.
- Implement calibration tracking: group historical signals by confidence bucket and verify actual win rates match. If "High confidence" signals win at 55%, the scoring model is broken.
- Include a disclaimer in Telegram alerts: confidence is based on historical pattern match, not a guarantee.
- Limit the maximum confidence to 85% — no trading signal should ever claim > 85% confidence. Markets are inherently uncertain.
- **Phase:** Phase 2 (signal scoring) and Phase 4 (calibration monitoring in self-improvement loop).

### 5.2 Overconfidence in Backtested Metrics

**What goes wrong:** The system uses backtest win rates directly as confidence scores. A strategy with an 80% backtest win rate does NOT mean the next signal has an 80% probability of winning. Backtests are optimistic by construction (survivorship bias, curve fitting, etc.). Live performance is typically 10-30% worse than backtested performance.

**Warning signs:**
- Live win rate is consistently below backtested win rate for the same strategy.
- Confidence scores haven't been adjusted downward after going live.
- No degradation factor applied to backtested metrics.

**Prevention strategy:**
- Apply a "reality haircut" to backtested metrics: multiply backtested win rate by 0.75-0.85 when converting to confidence scores.
- After accumulating 50+ live trades, transition confidence scoring from backtest-based to live-performance-based.
- Track the "backtest-to-live decay" ratio for each strategy and use it to calibrate future estimates.
- **Phase:** Phase 3 (confidence scoring model) and Phase 4 (recalibration).

---

## 6. Outcome Tracking Pitfalls

### 6.1 Slippage Between Signal and Fill

**What goes wrong:** The Telegram alert says "BUY XAUUSD at 2850.00" but by the time the user sees and acts on it, gold is at 2853.50. The outcome tracker records the result based on the signal price, not the fill price. Over time, tracked performance diverges from actual user performance, creating a misleading track record.

**Warning signs:**
- Users report worse results than the system's published track record.
- No mechanism to record actual entry prices.
- Signal-to-alert latency exceeds 5 seconds.

**Prevention strategy:**
- Track two sets of results: (a) theoretical (signal price-based) and (b) estimated real (signal price + estimated slippage of 10-30 pips depending on session).
- Minimize signal-to-alert latency: signal generation, confidence scoring, and Telegram delivery should complete in < 3 seconds total.
- Publish both theoretical and "after estimated slippage" track records. Be honest with users about the gap.
- Consider implementing a "price valid for" window: signal is only valid if price is within 30 pips of the signal price when the user sees it.
- **Phase:** Phase 2 (outcome tracking design) and Phase 5 (production performance reporting).

### 6.2 Spread Not Accounted for in Outcome Calculation

**What goes wrong:** The outcome tracker marks a trade as a "win" because price hit the take-profit level. But the user's actual execution included spread — so the user's fill was 20 pips worse on entry and 20 pips worse on exit (round-trip cost). A "200-pip win" in the tracker is actually a 160-pip win for the user.

**Warning signs:**
- No spread model in the outcome tracker.
- Published average win size doesn't match user-reported average win size.
- Small wins (< 50 pips) are reported frequently, which after spread may actually be losses.

**Prevention strategy:**
- Always subtract round-trip spread from every trade result in the tracker. Use the session-appropriate spread model (same as in backtesting).
- Set a minimum target threshold: never generate signals with targets less than 3x the expected spread. For gold with 20-pip spread, minimum target should be 60+ pips (your 200-500 pip range is safe here, but verify for partial targets).
- **Phase:** Phase 2 (outcome tracker). Build spread accounting in from the start.

### 6.3 Partial Fill and Manual Exit Tracking

**What goes wrong:** The system issues a signal with TP at 2870 and SL at 2830. The user manually closes at 2855 for a partial profit. The outcome tracker doesn't know about the manual exit and eventually records a loss when price later hits the SL. Now the track record shows a loss while the user actually made money (or vice versa).

**Warning signs:**
- No mechanism for users to report actual exit prices.
- Outcome tracker assumes all trades run to TP or SL — no partial close tracking.
- Track record diverges from user experience.

**Prevention strategy:**
- Design the outcome tracker to mark outcomes based on what price did, not what the user did. Clearly label it as "signal outcome" not "trading outcome."
- Alternatively, implement a feedback mechanism: users can reply to the Telegram alert with their actual exit price, and the system records both the signal outcome and the user's outcome.
- Track both "TP/SL hit" outcomes and "time-expired" outcomes (what happened after X hours if neither TP nor SL was reached).
- **Phase:** Phase 2 (outcome tracker design) and Phase 5 (user feedback integration).

---

## 7. Railway Deployment Pitfalls

### 7.1 Cold Start Latency

**What goes wrong:** Railway's free and Hobby tiers spin down idle services. When a cron job or webhook triggers the service, it must cold start — loading Python, installing dependencies, initializing the app. This can take 10-30+ seconds. For a time-sensitive trading signal, a 30-second delay can mean a 50+ pip price difference.

**Warning signs:**
- First signal of the day takes noticeably longer to generate.
- Intermittent timeouts on webhook endpoints.
- Cron jobs fire but the handler function times out before completing.

**Prevention strategy:**
- Keep the service warm: implement a health-check endpoint that Railway's built-in health checks hit every 5 minutes, or use an external ping service (UptimeRobot, cron-job.org).
- Use Railway Pro plan which doesn't spin down services.
- Optimize cold start: minimize dependencies, use slim Docker images, lazy-load heavy libraries (pandas, numpy) only when needed.
- Implement a startup readiness check: the application signals "ready" only after all models and data are loaded. Don't accept requests before ready.
- **Phase:** Phase 5 (deployment and production hardening).

### 7.2 Memory Limits and OOM Kills

**What goes wrong:** Railway limits memory per service (512MB-8GB depending on plan). Loading historical OHLCV data for rolling backtests + pandas DataFrames + indicator calculations can easily exceed 512MB. When memory is exceeded, Railway kills the process without warning, losing any in-flight signal generation.

**Warning signs:**
- Service crashes with no error logs (OOM kills often don't produce application-level logs).
- Service restarts at seemingly random times, especially during backtest calculations.
- Memory usage grows over time (memory leak in data accumulation).

**Prevention strategy:**
- Profile memory usage during development: load the full dataset and calculate all indicators, measure peak memory.
- Use chunked processing for backtests: process data in rolling windows rather than loading the entire history into memory.
- Implement memory monitoring: log memory usage at key points (startup, after data load, after backtest, after signal generation). Alert if usage exceeds 70% of limit.
- Set Railway memory limit explicitly and test at that limit locally using Docker `--memory` flag.
- Clear DataFrames and large objects explicitly after use (`del df; gc.collect()`).
- **Phase:** Phase 5 (deployment). Test memory limits before going live.

### 7.3 Cron Reliability on Railway

**What goes wrong:** Railway's cron jobs are not guaranteed to fire at exact times. They can be delayed by 1-30+ seconds, and if the service is cold, add cold-start time on top. For a system that needs to check for signals at specific market times (e.g., London open at 08:00 UTC), a 30-second delay might mean stale data or missed entry prices.

**Warning signs:**
- Cron jobs fire late (check Railway logs for actual execution times vs scheduled times).
- Signals are generated with timestamps that don't match the expected schedule.
- Some cron executions are skipped entirely during high-load periods.

**Prevention strategy:**
- Don't rely on cron precision for signal timing. Instead, use cron to trigger a process that then checks market conditions — the exact second doesn't matter if the strategy is based on candle closes.
- Implement idempotency: if a cron job fires twice, the second execution should detect "already processed this candle" and skip.
- Log actual cron execution times and compare to scheduled times. Alert if drift exceeds 60 seconds.
- For critical timing (e.g., news blackout checks), use the candle timestamp, not the cron execution timestamp.
- Consider using Railway's always-on service with an internal scheduler (APScheduler) instead of Railway cron for more reliability.
- **Phase:** Phase 5 (deployment architecture).

### 7.4 WebSocket Limitations on Railway

**What goes wrong:** If the system uses WebSocket connections (e.g., to receive real-time price feeds), Railway may terminate long-lived WebSocket connections during deployments or scaling events. The system loses its real-time data feed and doesn't reconnect, generating signals on stale data.

**Warning signs:**
- Data feed gaps coinciding with Railway deployment events.
- WebSocket connection drops without automatic reconnection.
- Silent stale data: the system thinks it has live data but the WebSocket died minutes ago.

**Prevention strategy:**
- Implement exponential backoff reconnection for all WebSocket connections.
- Use a heartbeat mechanism: if no data is received for 2x the expected interval, force reconnect.
- Prefer REST polling over WebSockets for Railway deployment. REST is more robust on ephemeral infrastructure. Poll every 15-60 seconds rather than maintaining a persistent connection.
- If using WebSockets, implement a "last data timestamp" check before every signal generation. Refuse to generate signals if data is stale.
- **Phase:** Phase 2 (data integration architecture decision) and Phase 5 (production hardening).

---

## 8. Telegram Integration Pitfalls

### 8.1 API Rate Limits

**What goes wrong:** Telegram's Bot API has rate limits: ~30 messages per second to different chats, ~1 message per second to the same chat. If the system tries to send multiple signals, updates, and chart images simultaneously, messages get queued or dropped. Telegram returns 429 errors which, if not handled, cause the entire alert pipeline to fail.

**Warning signs:**
- HTTP 429 responses from Telegram API in logs.
- Messages arriving out of order or with significant delays.
- Some alerts never delivered (silently dropped).

**Prevention strategy:**
- Implement a message queue with rate limiting: max 1 message per second per chat, with exponential backoff on 429 errors.
- Batch related information into a single message rather than sending multiple messages (e.g., signal + confidence + chart in one message, not three).
- Implement delivery confirmation: after sending, verify the message was delivered by checking the API response. Retry on failure with a maximum of 3 retries.
- For bulk operations (e.g., daily summary to multiple users), spread messages over time.
- **Phase:** Phase 2 (Telegram integration). Build rate limiting from the start.

### 8.2 Message Formatting Edge Cases

**What goes wrong:** Telegram supports MarkdownV2 and HTML formatting, but both have strict escaping rules. MarkdownV2 requires escaping of characters like `.`, `-`, `(`, `)`, `!`, etc. Gold prices contain periods (2850.50) and pips use hyphens. A single unescaped character causes the entire message to fail with a 400 error, and the alert is never sent.

**Warning signs:**
- Intermittent 400 errors from Telegram API, especially when prices have certain digit patterns.
- Messages that work for some signals but fail for others.
- Using `parse_mode='MarkdownV2'` without thorough escaping.

**Prevention strategy:**
- Use HTML parse mode instead of MarkdownV2 — it has far fewer escaping issues. `<b>`, `<i>`, `<code>` cover most formatting needs.
- If using MarkdownV2, create a robust escape function that handles ALL special characters: `_`, `*`, `[`, `]`, `(`, `)`, `~`, `` ` ``, `>`, `#`, `+`, `-`, `=`, `|`, `{`, `}`, `.`, `!`.
- Write unit tests with edge-case prices (e.g., 2850.00, 1999.99, 3000.00) and verify message formatting doesn't break.
- Implement a fallback: if formatted message fails, send a plain-text version.
- **Phase:** Phase 2 (Telegram integration). Test formatting exhaustively before launch.

### 8.3 Chart Image Delivery Failures

**What goes wrong:** Sending TradingView chart images via Telegram requires either generating the chart server-side or using TradingView's chart URL API. Server-side chart generation (e.g., with Selenium/Playwright) is slow and memory-intensive on Railway. URL-based approaches may break if TradingView changes their URL format. Chart delivery adds latency to signal alerts.

**Warning signs:**
- Chart generation takes > 5 seconds, delaying the signal alert.
- Charts fail to generate intermittently (headless browser crashes).
- Memory spikes during chart generation.

**Prevention strategy:**
- Decouple chart delivery from signal delivery: send the text signal immediately, then follow up with the chart image 5-30 seconds later. Never delay the signal alert for a chart.
- Use TradingView's lightweight chart widget URLs or Mini Chart Widget rather than screenshotting full charts.
- Cache chart generation resources (browser instance, chart templates).
- Implement a "chart optional" mode: if chart generation fails, the signal still goes out with a note that the chart is unavailable.
- **Phase:** Phase 5 (nice-to-have, not blocking for MVP).

---

## 9. Database and Performance Pitfalls

### 9.1 Unbounded Table Growth

**What goes wrong:** Every candle, every indicator value, every signal, and every outcome is stored in the database. With 1-minute data on XAUUSD, that's ~1,440 candles/day = ~525,600/year. Add indicator values and that's millions of rows within months. Query performance degrades, storage costs increase, and backtest queries that scan the full table become unacceptably slow.

**Warning signs:**
- Query times for backtests increase linearly with data age.
- Database storage exceeding expectations.
- "SELECT * FROM candles WHERE ..." queries taking > 5 seconds.

**Prevention strategy:**
- Implement data retention policies: keep 1-minute data for 30 days, aggregate to 15-minute for 6 months, aggregate to 1-hour for 2+ years. Archive raw data to cold storage (S3/R2) if needed.
- Add indexes on frequently queried columns: `(symbol, timeframe, timestamp)` as a composite index.
- Partition tables by date range if using PostgreSQL.
- Monitor query performance weekly. Set alerts if any query exceeds 2 seconds.
- Use `EXPLAIN ANALYZE` on all backtest queries during development to verify index usage.
- **Phase:** Phase 1 (database schema design). Plan for growth from the start.

### 9.2 N+1 Query Problems in Signal Generation

**What goes wrong:** The signal generation pipeline fetches data inefficiently — first querying for candles, then looping to fetch indicators for each candle, then querying conditions one by one. This creates hundreds of database queries per signal evaluation cycle.

**Warning signs:**
- Signal generation takes > 10 seconds despite simple rule-based logic.
- Database CPU spikes during signal evaluation.
- Log shows hundreds of queries per evaluation cycle.

**Prevention strategy:**
- Fetch all required data in a single query (or minimal queries): join candles with pre-computed indicators, or compute indicators in-memory from the candle data.
- For rule-based strategies with simple conditions, compute everything in-memory using pandas — no mid-pipeline database queries.
- Use query logging during development to count queries per signal cycle. Target < 5 queries per cycle.
- **Phase:** Phase 2 (signal generation pipeline architecture).

### 9.3 Migration and Schema Evolution

**What goes wrong:** As the system evolves (new strategies, new metrics, new fields), database schema changes are needed. Without proper migration tooling, schema changes either break production, lose data, or require manual intervention on Railway.

**Warning signs:**
- Schema changes are applied by manually running SQL scripts.
- No migration history — you can't tell what schema version production is running.
- Adding a column breaks existing queries that use `SELECT *`.

**Prevention strategy:**
- Use Alembic (with SQLAlchemy) or equivalent migration tool from day one.
- Every schema change goes through a migration file that is version-controlled.
- Migrations must be reversible (include both `upgrade()` and `downgrade()`).
- Test migrations on a staging database before applying to production.
- Never use `SELECT *` — always specify columns explicitly.
- **Phase:** Phase 1 (project setup). Set up Alembic before writing any schema code.

---

## 10. Session Handling Pitfalls

### 10.1 Asian Session Thin Liquidity Traps

**What goes wrong:** During Asian session (00:00-08:00 UTC approximately), gold liquidity drops significantly. Spreads widen, price movements can be erratic (sudden 100-pip spikes on low volume that immediately reverse), and technical levels are less reliable. Strategies that work well during London/NY sessions produce false signals during Asian session.

**Warning signs:**
- Disproportionate losses on signals generated during 00:00-07:00 UTC.
- Signals triggered by low-volume spikes that don't follow through.
- Asian session win rate is 15%+ lower than London/NY session win rate.

**Prevention strategy:**
- Analyze performance by session from the earliest backtests. If a strategy underperforms in Asian session, add a session filter.
- Consider disabling signal generation entirely during Asian session (00:00-07:00 UTC) for the initial launch. Add it back only if backtests demonstrate consistent edge.
- If trading Asian session, require additional confirmation (e.g., higher confidence threshold, additional indicator alignment) before generating signals.
- Track session-specific metrics as first-class analytics, not an afterthought.
- **Phase:** Phase 2 (signal generation filters) and Phase 4 (performance analysis by session).

### 10.2 London/NY Overlap Volatility Mispricing

**What goes wrong:** The London/NY overlap (13:00-16:00 UTC) is the highest-liquidity, highest-volatility period for gold. Many significant moves start here. Strategies may generate signals at the start of the overlap that get immediately run over by the surge in volume and volatility. Entry timing during this period is critical — being 5 minutes early or late can mean 50+ pips difference.

**Warning signs:**
- Signals during 13:00-14:00 UTC have worse performance than other London session signals.
- Many signals are "nearly right" but entry timing was off.
- Stops hit during initial overlap volatility spike before the real move occurs.

**Prevention strategy:**
- During the overlap, wait for the first 15-30 minutes of price action to establish before generating signals. Don't signal at 13:00 sharp.
- Use slightly wider stops during overlap to account for increased volatility.
- Consider time-specific strategy parameters: overlap period uses ATR * 1.5 for stops instead of ATR * 1.0.
- **Phase:** Phase 2 (strategy parameters) and Phase 4 (session-specific tuning).

### 10.3 DST (Daylight Saving Time) Session Shifts

**What goes wrong:** When the US or UK shifts between DST and standard time, session boundaries shift by 1 hour relative to UTC. The London open shifts from 08:00 UTC (winter) to 07:00 UTC (summer). If session filters use hardcoded UTC times, they'll be wrong for half the year.

**Warning signs:**
- Performance degrades for 1-2 weeks around DST transitions (March and November).
- Session filters don't account for DST.
- Signals fire an hour earlier or later than expected after DST change.

**Prevention strategy:**
- Define session boundaries in local market time (London session = 08:00-16:30 GMT/BST), then convert to UTC dynamically using `pytz` or `zoneinfo`.
- Test session logic for dates on both sides of DST transitions.
- Schedule a system review for the week of each DST change (second Sunday in March and first Sunday in November for US).
- **Phase:** Phase 2 (session filter implementation). Include DST in unit tests.

---

## 11. System-Level / Architectural Pitfalls

### 11.1 No Paper Trading Phase

**What goes wrong:** The system goes from backtesting directly to live signals without a paper-trading validation phase. Backtests always overstate performance. Without paper trading, the first real signals users receive may be from a system that doesn't work in live conditions. Trust is lost immediately and is very hard to rebuild.

**Warning signs:**
- Pressure to launch quickly ("let's just go live and see").
- No mechanism for generating signals without publishing them.
- First published signal is a loss, and there's no track record to fall back on.

**Prevention strategy:**
- Implement a mandatory paper-trading phase of at least 4-6 weeks (50+ signals minimum) before publishing signals to real users.
- During paper trading, generate signals and track outcomes exactly as in production, but only publish to a private "dev" Telegram channel.
- Compare paper-trading results to backtest expectations. If paper trading win rate is more than 15% below backtest win rate, investigate before going live.
- Use paper trading to validate the entire pipeline: data freshness, signal timing, Telegram delivery, outcome tracking, and database storage.
- **Phase:** Phase 4 (validation phase — must happen before any public launch).

### 11.2 Self-Improvement Loop Creates Positive Feedback Loops

**What goes wrong:** The self-improvement loop detects that Strategy A is underperforming, switches to Strategy B, which then also underperforms (because market conditions changed, not because Strategy A was bad), switches back to Strategy A... and so on. Or worse: the loop adjusts parameters in response to recent losses, which actually worsens future performance (overfitting to noise).

**Warning signs:**
- Parameters change frequently (more than once per week).
- The system's performance gets worse over time despite continuous "improvements."
- No distinction between "the strategy is broken" and "the market is in a drawdown."

**Prevention strategy:**
- Constrain the self-improvement loop: it can adjust strategy selection but NOT individual strategy parameters. Parameter changes require manual review.
- Implement a "circuit breaker": if the system detects more than 5 consecutive losses or a drawdown exceeding 2x historical max drawdown, halt signal generation entirely and alert the operator for manual review.
- Set a minimum observation period between adjustments: 2 weeks or 20 trades, whichever is longer.
- Log every automated adjustment with the metrics that triggered it. Require that all adjustments are reversible.
- Run a "control" version that never self-adjusts alongside the self-improving version. Compare performance monthly.
- **Phase:** Phase 4 (self-improvement loop design). This is the hardest part of the system to get right.

### 11.3 No Graceful Degradation

**What goes wrong:** A single component failure (data API down, database unreachable, Telegram API error) cascades into a total system failure. The system either generates wrong signals or generates no signals with no notification.

**Warning signs:**
- No error handling around external service calls.
- A database connection timeout causes a 500 error on the health check, which causes Railway to restart the service, which causes a cold start, which causes missed signals.
- No alerting for component failures.

**Prevention strategy:**
- Implement health checks for every external dependency: data API, database, Telegram API.
- Design for graceful degradation: if the database is down, buffer signals in memory/file and write them when it recovers. If Telegram is down, queue messages and retry. If data API is down, use the last known good data and flag signals as "stale data."
- Implement a separate alerting channel (e.g., email or a second Telegram bot to a personal chat) for system failures. Don't rely on the same Telegram bot that might be failing.
- Use structured error logging with severity levels. Monitor for ERROR-level logs.
- **Phase:** Phase 5 (production hardening). Design the failure modes during Phase 2.

### 11.4 Insufficient Logging and Observability

**What goes wrong:** When something goes wrong in production (wrong signal, missed signal, incorrect confidence), there's no way to reconstruct what happened. Without detailed logs, debugging requires reproducing the issue — which is often impossible because market conditions have changed.

**Warning signs:**
- "I don't know why it generated that signal" moments.
- Unable to answer "what data did the system have at the time of this signal?"
- Post-mortems take hours because of missing information.

**Prevention strategy:**
- Log every step of the signal generation pipeline with structured logging (JSON format): data fetch timestamp, data quality check result, indicator values, strategy rule evaluations, confidence calculation, and final signal decision.
- For every signal generated, store a snapshot of the exact data and indicator values used. This enables perfect reproduction of any signal.
- Implement log levels: DEBUG for indicator values, INFO for signals, WARNING for data quality issues, ERROR for failures.
- Use Railway's built-in logging, but also consider shipping logs to a persistent service (Logflare, Papertrail) since Railway logs are ephemeral.
- **Phase:** Phase 1 (set up logging framework from the start). Expand in every subsequent phase.

---

## Summary: Critical Pitfall Priority Matrix

| Priority | Pitfall | Impact if Ignored | Phase to Address |
|----------|---------|-------------------|------------------|
| P0 | Look-ahead bias (1.1) | Entire system is invalid | Phase 1 |
| P0 | Timezone mismatches (4.2) | All indicators wrong | Phase 1 |
| P0 | No paper trading phase (11.1) | Launch with broken system | Phase 4 |
| P1 | Overfitting / curve fitting (1.3) | False confidence in strategies | Phase 1 |
| P1 | Ignoring transaction costs (1.4) | Profitable backtest, losing live | Phase 1 |
| P1 | News blackout missing (2.1) | Catastrophic losses around events | Phase 2 |
| P1 | Missing candle handling (4.1) | Phantom signals | Phase 1 |
| P1 | Self-improvement feedback loops (11.2) | System degrades over time | Phase 4 |
| P2 | Recency bias in selection (3.1) | Strategy whipsaw | Phase 3 |
| P2 | Small sample sizes (3.3) | Statistically meaningless selection | Phase 3 |
| P2 | Cold start latency (7.1) | Delayed/missed signals | Phase 5 |
| P2 | Memory limits (7.2) | Random crashes | Phase 5 |
| P2 | No graceful degradation (11.3) | Cascade failures | Phase 5 |
| P3 | Confidence score precision (5.1) | Misleading users | Phase 2 |
| P3 | Slippage tracking (6.1) | Misleading track record | Phase 2 |
| P3 | Table growth (9.1) | Slow queries after months | Phase 1 |
| P3 | Telegram formatting (8.2) | Silent alert failures | Phase 2 |
| P3 | DST handling (10.3) | Biannual session errors | Phase 2 |

---

## Key Takeaways

1. **The three deadliest pitfalls are all in the backtester.** Look-ahead bias, overfitting, and ignoring transaction costs. If the backtester is wrong, every downstream decision (strategy selection, confidence scoring, self-improvement) is built on a lie. Invest disproportionate time in backtester correctness.

2. **Gold is not forex.** It trades like a commodity with currency characteristics. Spread behavior, volatility regimes, and session dynamics are unique. Don't copy forex trading system patterns without gold-specific adaptations.

3. **The self-improvement loop is a double-edged sword.** It can make the system better over time, or it can create a negative feedback loop that degrades performance. Constrain it heavily, monitor it carefully, and always keep a non-adaptive control for comparison.

4. **Paper trade before you go live. No exceptions.** Four to six weeks minimum with 50+ signals. If paper trading doesn't match backtested expectations, investigate before launching.

5. **Railway is fine for this use case, but respect its constraints.** Keep services warm, monitor memory, don't rely on cron precision, and prefer REST over WebSockets. Design for the platform's characteristics, don't fight them.
