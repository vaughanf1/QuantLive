# Phase 6: Outcome Tracking and Feedback - Research

**Researched:** 2026-02-17
**Domain:** Real-time price monitoring, trade outcome detection, rolling performance metrics, strategy feedback loop
**Confidence:** HIGH (all core patterns verified against existing codebase; Twelve Data API capabilities verified via official docs)

## Summary

Phase 6 closes the loop between signal generation and performance measurement. It requires three core subsystems: (1) an **outcome detector** that periodically polls current price and compares it against active signal SL/TP levels, (2) a **performance metrics recalculator** that updates rolling strategy metrics after each outcome, and (3) a **feedback controller** that degrades/restores strategies and implements a circuit breaker to halt signal generation during losing streaks.

The system already has all the database models needed (Signal, Outcome, StrategyPerformance), all the notification plumbing (TelegramNotifier.notify_outcome()), a spread model (SessionSpreadModel), a drawdown tracker (RiskManager.get_drawdown_metrics()), and degradation detection logic (StrategySelector._check_degradation()). The remaining work is to build the price monitoring loop, outcome detection logic, performance recalculation, degradation alerting, auto-recovery, and the circuit breaker.

The critical design decision is how to get "current price" for outcome checking. The system uses Twelve Data, which provides a lightweight REST `/price` endpoint (1 credit, returns latest price as a single float). On the free plan (8 credits/minute), polling every 30 seconds for 1 symbol uses 2 credits/minute -- well within limits. This is vastly simpler and more reliable than WebSocket (which requires Pro plan) and is the recommended approach.

**Primary recommendation:** Build an `OutcomeDetector` service class that queries active signals from DB, fetches current price via Twelve Data `/price` endpoint, compares against SL/TP levels with spread adjustment, logs outcomes, triggers performance recalculation, and sends Telegram notifications. Schedule it as an APScheduler IntervalTrigger job running every 30 seconds.

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| APScheduler | 3.10.4+ (already installed) | IntervalTrigger for 30-second outcome checking | Already used for all background jobs in the project |
| SQLAlchemy 2.0 async | 2.0.36+ (already installed) | Query active signals, insert outcomes, update signal status | Already the project's ORM |
| httpx | 0.28+ (already installed) | Call Twelve Data `/price` REST endpoint | Already used by TelegramNotifier; async-native |
| Twelve Data Python SDK | 1.2.5+ (already installed) | Alternative to raw httpx for price fetching (has `.price()` method) | Already integrated for candle ingestion |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| tenacity | 9.0+ (already installed) | Retry logic for price API calls | Wrap price fetch to handle transient failures |
| loguru | 0.7.3+ (already installed) | Structured logging for outcome events | Already the project's logging framework |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| REST `/price` polling every 30s | Twelve Data WebSocket streaming | WebSocket requires Pro plan ($29+/mo), adds connection management complexity, but gives ~170ms latency vs 30s polling. Polling is sufficient for SL/TP checks on H1 signals. |
| REST `/price` polling every 30s | Using latest M15 candle close from DB | Zero API credits, but max staleness is 15 minutes. Acceptable fallback if API is down, but misses intra-candle hits. |
| APScheduler IntervalTrigger | asyncio.create_task with sleep loop | APScheduler provides misfire handling, coalescing, max_instances control. Raw asyncio loop lacks these. |

**Installation:** No new packages needed. All dependencies already in `requirements.txt`.

## Architecture Patterns

### Recommended Project Structure
```
app/
├── services/
│   ├── outcome_detector.py     # NEW: OutcomeDetector class (TRACK-01 through TRACK-05)
│   ├── performance_tracker.py  # NEW: PerformanceTracker class (FEED-01, FEED-02)
│   ├── feedback_controller.py  # NEW: FeedbackController class (FEED-03, FEED-04, FEED-05)
│   ├── strategy_selector.py    # MODIFY: integrate live performance metrics into scoring
│   ├── risk_manager.py         # MODIFY: add circuit breaker check
│   ├── telegram_notifier.py    # MODIFY: add notify_degradation() method
│   └── spread_model.py         # REUSE: no changes needed
├── workers/
│   ├── jobs.py                 # MODIFY: add check_outcomes() job, wire feedback
│   └── scheduler.py            # MODIFY: register new IntervalTrigger job
├── models/
│   ├── signal.py               # REUSE: status field already supports needed transitions
│   ├── outcome.py              # REUSE: already has all needed columns
│   └── strategy_performance.py # REUSE: already has is_degraded, periods, metrics
└── tests/
    ├── test_outcome_detector.py    # NEW
    ├── test_performance_tracker.py # NEW
    └── test_feedback_controller.py # NEW
```

### Pattern 1: Outcome Detector (Price Comparison Engine)
**What:** A service that fetches current price and compares against all active signals' SL/TP levels, accounting for spread and direction.
**When to use:** Every 30 seconds via APScheduler IntervalTrigger.
**Critical logic:**

```python
# Source: Existing trade_simulator.py patterns, adapted for live detection

class OutcomeDetector:
    PIP_VALUE = 0.10  # XAUUSD: $0.10 price movement per pip

    async def check_outcomes(self, session: AsyncSession) -> list[Outcome]:
        """Check all active signals against current price."""
        # 1. Query all active signals
        active_signals = await self._get_active_signals(session)
        if not active_signals:
            return []

        # 2. Fetch current price (single API call)
        current_price = await self._fetch_current_price()
        if current_price is None:
            return []

        # 3. Get current spread
        spread = self.spread_model.get_spread(datetime.now(timezone.utc))

        # 4. Check each signal
        outcomes = []
        for signal in active_signals:
            result = self._evaluate_signal(signal, current_price, spread)
            if result is not None:
                outcome = await self._record_outcome(session, signal, result)
                outcomes.append(outcome)

        return outcomes

    def _evaluate_signal(
        self, signal: Signal, price: float, spread: Decimal
    ) -> str | None:
        """Check if price has hit SL, TP1, TP2, or if signal expired.

        SL ALWAYS takes priority (decision [03-01]).
        """
        entry = float(signal.entry_price)
        sl = float(signal.stop_loss)
        tp1 = float(signal.take_profit_1)
        tp2 = float(signal.take_profit_2)
        spread_f = float(spread)
        is_buy = signal.direction == "BUY"

        # Check expiry first (time-based)
        if signal.expires_at and datetime.now(timezone.utc) >= signal.expires_at:
            return "expired"

        # SL check (priority over TP per decision [03-01])
        if is_buy:
            # BUY: SL hit when price drops to/below SL level
            if price <= sl:
                return "sl_hit"
        else:
            # SELL: SL hit when ask price (price + spread) rises to/above SL
            if (price + spread_f) >= sl:
                return "sl_hit"

        # TP2 check (higher priority than TP1 since it's further)
        if is_buy:
            if price >= tp2:
                return "tp2_hit"
        else:
            if price <= tp2:
                return "tp2_hit"

        # TP1 check
        if is_buy:
            if price >= tp1:
                return "tp1_hit"
        else:
            if price <= tp1:
                return "tp1_hit"

        return None  # No outcome yet
```

### Pattern 2: Signal Status State Machine
**What:** Signals transition through states: `active` -> `tp1_hit` -> `tp2_hit` or `active` -> `sl_hit` or `active` -> `expired`.
**When to use:** When recording outcomes. The signal status must be updated atomically with the outcome insertion.
**Key design decisions:**
- When TP1 is hit, the signal status changes to `tp1_hit` but remains **monitorable** for TP2. The outcome is NOT recorded yet -- it's only a status transition.
- When TP2 is hit (after TP1), or when SL is hit (from any state), or when the signal expires, the **final** outcome is recorded and the signal is no longer monitored.
- This means the Outcome record represents the FINAL result only.

**Alternative (simpler) approach:** Record outcome immediately on first hit (TP1 or SL). This is simpler and aligns with the existing Outcome model which has a 1:1 relationship with Signal (unique constraint on signal_id). The requirement says "TP1 hit, TP2 hit, SL hit, or time expiry" as separate outcomes, not a multi-stage process. **Recommended: record on first terminal hit.**

```python
# Status transitions (recommended simple model):
# active -> tp1_hit (outcome recorded, monitoring stops)
# active -> tp2_hit (outcome recorded, monitoring stops) -- if price jumps past TP1
# active -> sl_hit  (outcome recorded, monitoring stops)
# active -> expired (outcome recorded, monitoring stops)
```

### Pattern 3: Performance Recalculation on Outcome
**What:** After recording an outcome, recalculate rolling performance metrics for the strategy.
**When to use:** Triggered by each new outcome.

```python
class PerformanceTracker:
    ROLLING_PERIODS = ["7d", "30d"]  # Match StrategyPerformance.period values

    async def recalculate(
        self, session: AsyncSession, strategy_id: int
    ) -> list[StrategyPerformance]:
        """Recalculate rolling metrics for a strategy across all periods."""
        results = []
        for period in self.ROLLING_PERIODS:
            days = int(period.rstrip("d"))
            cutoff = datetime.now(timezone.utc) - timedelta(days=days)

            # Query outcomes for this strategy within the period
            outcomes = await self._get_outcomes_for_strategy(
                session, strategy_id, since=cutoff
            )

            if not outcomes:
                continue

            # Compute metrics
            win_rate = self._compute_win_rate(outcomes)
            profit_factor = self._compute_profit_factor(outcomes)
            avg_rr = self._compute_avg_rr(outcomes)

            # Upsert StrategyPerformance row
            perf = await self._upsert_performance(
                session, strategy_id, period, win_rate, profit_factor, avg_rr
            )
            results.append(perf)

        return results
```

### Pattern 4: Circuit Breaker
**What:** Halts signal generation after N consecutive losses or excessive drawdown.
**When to use:** Checked at the START of the signal pipeline, before strategy selection.

```python
class FeedbackController:
    CONSECUTIVE_LOSS_LIMIT = 5
    DRAWDOWN_MULTIPLIER = 2.0  # 2x historical max drawdown

    async def is_circuit_broken(self, session: AsyncSession) -> tuple[bool, str | None]:
        """Check if signal generation should be halted."""
        # Check 1: Consecutive losses
        consecutive = await self._count_consecutive_losses(session)
        if consecutive >= self.CONSECUTIVE_LOSS_LIMIT:
            return True, f"Circuit breaker: {consecutive} consecutive losses"

        # Check 2: Drawdown exceeds 2x historical max
        drawdown_metrics = await self.risk_manager.get_drawdown_metrics(session)
        historical_max = drawdown_metrics["max_drawdown"]
        running_dd = drawdown_metrics["running_drawdown"]

        if historical_max > 0 and running_dd >= self.DRAWDOWN_MULTIPLIER * historical_max:
            return True, (
                f"Circuit breaker: drawdown {running_dd:.1f} pips >= "
                f"2x historical max {historical_max:.1f}"
            )

        return False, None
```

### Pattern 5: Scheduled Job (30-second interval)
**What:** APScheduler IntervalTrigger job for outcome checking.
**When to use:** Registered alongside existing cron jobs in scheduler.py.

```python
# In scheduler.py
from apscheduler.triggers.interval import IntervalTrigger

scheduler.add_job(
    check_outcomes,
    trigger=IntervalTrigger(seconds=30),
    id="check_outcomes",
    name="Check signal outcomes",
    replace_existing=True,
    max_instances=1,   # Prevent overlap if check takes > 30s
    coalesce=True,     # Skip missed runs
)
```

### Anti-Patterns to Avoid
- **Anti-pattern: Polling inside asyncio.sleep loop.** Use APScheduler's IntervalTrigger instead. It handles misfire grace periods, coalescing, and max_instances properly.
- **Anti-pattern: Checking M15 candle close price instead of real-time price.** The requirement says 15-30 second monitoring. M15 candle data is up to 15 minutes stale. Use the `/price` REST endpoint for current price.
- **Anti-pattern: Recording intermediate TP1 hits as separate Outcomes.** The Outcome model has `UNIQUE(signal_id)` -- only one outcome per signal. Record the final outcome only.
- **Anti-pattern: Calculating metrics from scratch every time.** Query only outcomes within the rolling window, not all historical outcomes. Use `WHERE created_at >= cutoff` for efficiency.
- **Anti-pattern: Modifying backtest_results for live feedback.** BacktestResult is for historical backtests only. Live performance goes in StrategyPerformance table.
- **Anti-pattern: Blocking the event loop with synchronous Twelve Data SDK calls.** The TDClient SDK's `.price()` method is synchronous. Use `httpx` AsyncClient to call the REST endpoint directly, or wrap in `asyncio.to_thread()`.

## Don't Hand-Roll

Problems that look simple but have existing solutions:

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Spread calculation | Custom spread lookup table | `SessionSpreadModel.get_spread()` | Already handles session overlap (tightest spread), matches backtest behavior exactly |
| Drawdown tracking | Custom cumulative P&L tracker | `RiskManager.get_drawdown_metrics()` | Already computes running_drawdown, max_drawdown, running_pnl, peak_pnl from Outcome table |
| Strategy degradation detection | Custom win rate comparison logic | `StrategySelector._check_degradation()` | Already checks win_rate drop >15% and profit_factor < 1.0; needs to be adapted to use live StrategyPerformance data |
| Outcome notification | Custom Telegram message builder | `TelegramNotifier.format_outcome()` + `notify_outcome()` | Already built and tested in Phase 5, including emoji map and HTML formatting |
| Signal expiry detection | Custom datetime comparison | `SignalGenerator.expire_stale_signals()` | Already handles active -> expired transition; Phase 6 can reuse or extend |
| SL/TP hit logic | Custom comparison logic | Adapt from `TradeSimulator.simulate_trade()` | Same SL-priority, spread-adjustment logic -- just applied to live price instead of candle bars |
| PnL calculation | Custom pip math | Use `PIP_VALUE = 0.10` constant and same formula as trade_simulator | `pnl = (exit - entry) / PIP_VALUE` for BUY, `(entry - exit) / PIP_VALUE` for SELL |

**Key insight:** Phase 6 is fundamentally the "live version" of the backtesting trade simulator. The `TradeSimulator` already has the correct SL/TP/spread logic -- the outcome detector is just applying the same logic to a single live price point instead of walking through OHLC bars.

## Common Pitfalls

### Pitfall 1: Race Condition Between Outcome Check and Signal Expiry
**What goes wrong:** The expire_stale_signals job (runs hourly at :02) and the outcome checker (runs every 30s) both modify signal status. They could conflict: expire_stale_signals marks a signal as "expired" while the outcome checker is mid-evaluation.
**Why it happens:** Two independent scheduled jobs operating on the same rows without coordination.
**How to avoid:** Have the outcome detector handle expiry itself (check `expires_at` before SL/TP comparison). Remove or guard the existing `expire_stale_signals` to avoid double-processing. Alternatively, use a SELECT FOR UPDATE when reading active signals in the outcome detector to prevent concurrent modification.
**Warning signs:** Signals with status="expired" but no Outcome row; or signals with two status transitions in rapid succession.

### Pitfall 2: Twelve Data API Rate Limit Exhaustion
**What goes wrong:** On the free plan (8 credits/minute), polling every 30 seconds for price = 2 credits/minute. But if other jobs (candle refresh) run simultaneously, total credits could exceed 8/minute.
**Why it happens:** M15 candle refresh runs at :01, :16, :31, :46 -- each consuming 1 credit. H1 at :01 = 1 credit. That's 5 credits just for candle refresh in a busy minute. Add 2 for price polling = 7. Close to the limit.
**How to avoid:** (a) Track API credit usage with a rate limiter (e.g., asyncio.Semaphore with time-based release). (b) Skip price polling during candle refresh minutes. (c) Use latest M15 candle close as fallback when price API returns 429. (d) Upgrade to Grow plan (377/min) if free plan is insufficient.
**Warning signs:** HTTP 429 responses from Twelve Data; missing outcome checks; gaps in monitoring.

### Pitfall 3: Spread Not Accounted For Correctly
**What goes wrong:** BUY signals show false TP hits or missed SL hits because spread wasn't applied correctly.
**Why it happens:** The `/price` endpoint returns the **bid** price. For BUY orders: entry is at ask (bid + spread), SL is checked against bid, TP is checked against bid. For SELL orders: entry is at bid, SL is checked against ask (bid + spread), TP is checked against bid.
**How to avoid:** Follow the exact same spread logic as `TradeSimulator`:
- BUY: `adjusted_entry = entry + spread`, SL hit if `price <= sl` (bid), TP hit if `price >= tp` (bid)
- SELL: `adjusted_entry = entry`, SL hit if `(price + spread) >= sl` (ask), TP hit if `price <= tp` (bid)
**Warning signs:** BUY signals showing TP hits that shouldn't have triggered; SELL signals missing SL hits.

### Pitfall 4: TP1 vs TP2 State Tracking Complexity
**What goes wrong:** Trying to implement a multi-stage tracking (TP1 hit -> continue monitoring for TP2) with the existing Outcome model creates complexity because `Outcome.signal_id` has a UNIQUE constraint.
**Why it happens:** The Outcome model was designed for 1:1 final outcomes, not multi-stage tracking.
**How to avoid:** Keep it simple -- record the FIRST terminal event. If price hits TP1, that's the outcome. The signal stops being monitored. TP2 would only be recorded if price jumps past TP1 directly to TP2 in a single check interval (unlikely but possible). If multi-stage tracking is desired later, it would require a schema change (remove unique constraint, add an `outcomes_history` table, or add a `partial_tp_at` column to Signal).
**Warning signs:** Attempts to insert duplicate outcome for same signal_id; unmonitored signals in "tp1_hit" status forever.

### Pitfall 5: Circuit Breaker Never Resetting
**What goes wrong:** After 5 consecutive losses trigger the circuit breaker, there's no mechanism to resume signal generation.
**Why it happens:** The circuit breaker halts signal generation, but since no new signals are generated, no new wins can occur to reset the counter.
**How to avoid:** Implement a time-based cooldown (e.g., circuit breaker auto-resets after 24 hours). Log the circuit break event with timestamp, check if cooldown has elapsed before enforcing the break. Also: consecutive loss count should only consider recent outcomes (e.g., last 48 hours), not all-time.
**Warning signs:** System completely silent for days; no signals generated despite market activity.

### Pitfall 6: Performance Metrics Recalculation Overhead
**What goes wrong:** Recalculating rolling metrics after every single outcome becomes expensive as the outcome table grows.
**Why it happens:** Querying all outcomes for a strategy within a 30-day window and computing aggregates on every 30-second check.
**How to avoid:** Only recalculate when a NEW outcome is recorded (not on every price check). Use indexed queries with `WHERE strategy_id = ? AND created_at >= ?`. Consider adding an index on `(strategy_id, created_at)` to the outcomes table. The current table has no such index.
**Warning signs:** Increasing latency on outcome checks; database CPU spikes.

## Code Examples

### Fetching Current Price via Twelve Data REST API

```python
# Source: Twelve Data API docs (https://twelvedata.com/docs#price)
# Using httpx (already in requirements.txt) for async HTTP

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

TWELVE_DATA_PRICE_URL = "https://api.twelvedata.com/price"

@retry(
    stop=stop_after_attempt(2),
    wait=wait_exponential(multiplier=0.5, max=3),
    retry=retry_if_exception_type((httpx.ConnectError, httpx.ReadTimeout)),
)
async def fetch_current_price(api_key: str, symbol: str = "XAU/USD") -> float | None:
    """Fetch the latest price for a symbol from Twelve Data.

    Returns None on API error (graceful degradation).
    Costs 1 API credit per call.
    """
    async with httpx.AsyncClient(timeout=5.0) as client:
        response = await client.get(
            TWELVE_DATA_PRICE_URL,
            params={"symbol": symbol, "apikey": api_key},
        )
        response.raise_for_status()
        data = response.json()

        if "price" not in data:
            # API returned an error object
            logger.warning("Price API error: {}", data)
            return None

        return float(data["price"])
```

### Recording an Outcome and Updating Signal Status

```python
# Source: Existing codebase patterns (trade_simulator.py, signal_generator.py)

async def _record_outcome(
    self,
    session: AsyncSession,
    signal: Signal,
    result: str,
    current_price: float,
) -> Outcome:
    """Record outcome, update signal status, trigger recalculation."""
    entry = float(signal.entry_price)
    is_buy = signal.direction == "BUY"

    # Calculate exit price based on result
    if result == "sl_hit":
        exit_price = float(signal.stop_loss)
    elif result == "tp1_hit":
        exit_price = float(signal.take_profit_1)
    elif result == "tp2_hit":
        exit_price = float(signal.take_profit_2)
    else:  # expired
        exit_price = current_price

    # Calculate PnL in pips
    if is_buy:
        pnl_pips = (exit_price - entry) / PIP_VALUE
    else:
        pnl_pips = (entry - exit_price) / PIP_VALUE

    # Calculate duration
    duration_minutes = int(
        (datetime.now(timezone.utc) - signal.created_at).total_seconds() / 60
    )

    # Create outcome
    outcome = Outcome(
        signal_id=signal.id,
        result=result,
        exit_price=Decimal(str(round(exit_price, 2))),
        pnl_pips=Decimal(str(round(pnl_pips, 2))),
        duration_minutes=duration_minutes,
    )
    session.add(outcome)

    # Update signal status
    signal.status = result  # "tp1_hit", "tp2_hit", "sl_hit", "expired"
    session.add(signal)

    await session.commit()
    return outcome
```

### Counting Consecutive Losses for Circuit Breaker

```python
# Source: Standard SQL pattern for consecutive sequence detection

async def _count_consecutive_losses(self, session: AsyncSession) -> int:
    """Count consecutive SL hits from the most recent outcome backwards.

    Stops counting at the first non-loss outcome.
    """
    stmt = (
        select(Outcome.result)
        .order_by(Outcome.created_at.desc())
        .limit(20)  # Check last 20 at most
    )
    result = await session.execute(stmt)
    results = result.scalars().all()

    consecutive = 0
    for r in results:
        if r == "sl_hit":
            consecutive += 1
        else:
            break

    return consecutive
```

### Degradation Alert via Telegram

```python
# Source: Extending existing TelegramNotifier pattern

async def notify_degradation(self, strategy_name: str, reason: str) -> None:
    """Send a degradation alert via Telegram. Never raises."""
    if not self.enabled:
        return

    try:
        text = (
            "\u26A0\uFE0F <b>Strategy Degradation Alert</b>\n\n"
            f"<b>Strategy:</b> {strategy_name}\n"
            f"<b>Reason:</b> {reason}\n\n"
            "<i>Strategy has been auto-deprioritized.</i>"
        )
        await self._send_message(text)
    except Exception:
        logger.exception("Telegram degradation alert failed for '{}'", strategy_name)
```

### Auto-Recovery Check

```python
# Source: Requirement FEED-04

async def check_recovery(
    self, session: AsyncSession, strategy_id: int
) -> bool:
    """Check if a degraded strategy has recovered over 7+ days.

    Recovery criteria:
    - Strategy has been marked degraded for at least 7 days
    - Recent 7-day win rate is within normal range (not >15% below baseline)
    - Recent 7-day profit factor >= 1.0
    """
    perf = await self._get_latest_performance(session, strategy_id, period="7d")
    if perf is None or not perf.is_degraded:
        return False

    # Check if degraded for at least 7 days
    days_degraded = (datetime.now(timezone.utc) - perf.calculated_at).days
    if days_degraded < 7:
        return False

    # Check recovery metrics
    if float(perf.win_rate) >= 0.45 and float(perf.profit_factor) >= 1.0:
        return True

    return False
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Poll OHLC candle data for SL/TP checks | Use `/price` REST endpoint for near-real-time checks | N/A (first implementation) | Sub-minute detection vs 15-min+ candle delay |
| Manual outcome tracking | Automated outcome detection with DB logging | N/A (first implementation) | Zero manual input required |
| Static strategy weights | Performance-adaptive scoring with degradation/recovery | N/A (first implementation) | System self-corrects based on live results |
| No risk circuit breaker | Consecutive loss + drawdown circuit breaker | N/A (first implementation) | Prevents catastrophic loss streaks |

**Deprecated/outdated:**
- The `expire_stale_signals()` method in `SignalGenerator` currently marks expired signals but does NOT record an Outcome. Phase 6 should handle expiry through the outcome detector to ensure outcomes are always recorded.

## Open Questions

1. **TP1 vs TP2 tracking model**
   - What we know: Outcome model has UNIQUE on signal_id (1:1). Current requirement lists "TP1 hit" and "TP2 hit" as separate results.
   - What's unclear: Should the system continue monitoring after TP1 hit (waiting for TP2), or stop monitoring on first TP hit?
   - Recommendation: Stop on first hit (simplest, matches UNIQUE constraint). Record "tp1_hit" if TP1 is breached but not TP2, "tp2_hit" if TP2 is breached. This matches the backtest simulator behavior where the simulator checks TP2 before TP1 in each bar -- if both are hit in the same check, TP2 wins.

2. **Twelve Data API plan and credit budget**
   - What we know: Free plan = 8 credits/minute, 800/day. Price endpoint = 1 credit. Candle refresh uses credits too.
   - What's unclear: What plan the user is on. Free plan is tight but workable for single-symbol monitoring.
   - Recommendation: Build with graceful degradation. If price fetch fails (429 rate limit), fall back to latest M15 candle close from DB. Log a warning. The system should never crash due to API limits.

3. **Circuit breaker reset mechanism**
   - What we know: Requirement says halt after 5 consecutive losses or 2x historical max drawdown.
   - What's unclear: How/when the circuit breaker resets. No requirement specifies this.
   - Recommendation: Auto-reset after 24-hour cooldown period. Also reset when consecutive loss count drops below threshold (which can happen when the system resumes and produces a win). Log circuit breaker activations and resets.

4. **Degradation detection source: backtest vs live performance**
   - What we know: `StrategySelector._check_degradation()` currently uses BacktestResult (historical backtests). Phase 6 introduces live StrategyPerformance data.
   - What's unclear: Should degradation use backtest data, live performance data, or both?
   - Recommendation: Phase 6's feedback loop should use StrategyPerformance (live data) for degradation detection, since that reflects actual signal outcomes. BacktestResult continues to be the baseline for the selector's composite scoring. Degradation triggers when live metrics diverge from backtest baseline.

5. **What price does `/price` return -- bid, ask, or mid?**
   - What we know: Twelve Data `/price` returns a single float. For forex/commodities, this is typically the bid price (or mid price depending on data provider).
   - What's unclear: Exact semantics of the returned value for XAU/USD on Twelve Data.
   - Recommendation: Assume bid price (most common for data APIs). Apply spread adjustment for SELL SL checks (add spread to get ask). Validate during integration testing by comparing `/price` output with known broker spreads.

## Database Considerations

### Indexes Needed
The following indexes should be added for Phase 6 query patterns:

```sql
-- Outcome queries by strategy and time (for rolling performance calculation)
CREATE INDEX idx_outcomes_signal_created ON outcomes(signal_id, created_at);

-- Signal queries for active signals (outcome detector's main query)
-- Note: signals table already has a status column; consider partial index
CREATE INDEX idx_signals_active ON signals(status) WHERE status = 'active';

-- Strategy performance lookups
CREATE INDEX idx_stratperf_strategy_period ON strategy_performance(strategy_id, period, calculated_at DESC);
```

### Schema Notes
- No new tables needed. All models exist: Signal, Outcome, StrategyPerformance.
- Signal.status needs expanded valid values: currently "active" and "expired". Phase 6 adds "tp1_hit", "tp2_hit", "sl_hit". These are just string values in a VARCHAR(20) column -- no schema migration needed.
- Consider adding a `circuit_breaker_active_since` column to a settings/state table, or track in memory (acceptable since APScheduler uses MemoryJobStore already).

## Sources

### Primary (HIGH confidence)
- Existing codebase analysis: All models, services, and patterns verified by direct file reading
- Twelve Data API docs (https://twelvedata.com/docs) - `/price` endpoint, credit costs
- Twelve Data pricing page (https://twelvedata.com/pricing) - Plan limits verified
- APScheduler 3.x docs (https://apscheduler.readthedocs.io/en/3.x/modules/triggers/interval.html) - IntervalTrigger API
- Twelve Data Python SDK (https://github.com/twelvedata/twelvedata-python) - `.price()` method availability

### Secondary (MEDIUM confidence)
- Twelve Data support article on credits (https://support.twelvedata.com/en/articles/5615854-credits) - credit weight system
- Trading system circuit breaker patterns from multiple algo trading sources

### Tertiary (LOW confidence)
- Exact return value semantics of Twelve Data `/price` for XAU/USD (bid vs mid) -- needs integration testing to confirm

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH - All libraries already in project; no new dependencies needed
- Architecture: HIGH - All patterns derived directly from existing codebase (trade_simulator, risk_manager, strategy_selector); Phase 6 is the "live version" of existing backtest infrastructure
- Pitfalls: HIGH - Race conditions, rate limits, and spread accounting are well-understood domain problems; specific to this codebase's architecture
- Price API: MEDIUM - Twelve Data `/price` endpoint verified via docs, but bid/ask semantics need integration testing

**Research date:** 2026-02-17
**Valid until:** 2026-03-17 (stable domain; no fast-moving library concerns)
