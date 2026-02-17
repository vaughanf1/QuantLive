# Phase 4: Signal Pipeline - Research

**Researched:** 2026-02-17
**Domain:** Trading signal generation, strategy selection, risk management, gold market intelligence
**Confidence:** HIGH

## Summary

Phase 4 builds the runtime signal pipeline: scoring and selecting the best strategy, generating validated trade signals, enforcing risk management rules, and adding gold-specific market intelligence. This is primarily a domain-logic phase that builds on top of the existing data foundation (Phase 1), strategy engine (Phase 2), and backtesting engine (Phase 3). No new external libraries are required -- the stack is pure Python business logic using pandas, numpy, and the existing SQLAlchemy async ORM.

The key architectural challenge is wiring together five subsystems (strategy selector, signal generator, signal validator, risk manager, gold intelligence) into a coherent pipeline that runs on a schedule. The existing codebase provides all the building blocks: `BacktestResult` records with metrics, `CandidateSignal` pydantic model, `Signal` DB model, session filtering helpers, ATR computation via pandas-ta-classic, and APScheduler with AsyncIOScheduler. The phase adds new service modules and a new scheduled job.

**Primary recommendation:** Build each subsystem as an independent service class with clear interfaces, then compose them in a `SignalPipeline` orchestrator that is invoked by a new APScheduler job on the H1 candle schedule.

## Standard Stack

### Core (already installed -- no new dependencies)
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| pandas | >=2.0 | DataFrame operations for metrics, ATR, rolling correlation | Already used throughout Phase 1-3 |
| numpy | >=1.26 | Numerical operations for scoring, regime detection | Already used in strategies |
| pandas-ta-classic | >=0.3.59 | ATR computation for position sizing and regime detection | Already used in indicator helpers |
| SQLAlchemy | >=2.0.36 | Async ORM for BacktestResult/Signal/Outcome queries | Already established |
| APScheduler | >=3.10.4,<4.0 | Scheduled scanner loop job | Already configured with AsyncIOScheduler |
| pydantic | >=2.10.0 | Validation models for pipeline configuration | Already used for CandidateSignal |
| loguru | >=0.7.3 | Structured logging throughout pipeline | Already used project-wide |

### Supporting (already installed)
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| httpx | >=0.28.0 | DXY data fetching (if external API needed) | DXY correlation monitoring |
| tenacity | >=9.0.0 | Retry logic for external API calls | DXY data fetching |
| scipy | >=1.12 | Statistical functions if needed for correlation | Rolling correlation verification |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| Custom composite scoring | ML-based ranking (sklearn) | Overkill for 3 strategies; explicit weights are more transparent and debuggable for prop-firm trading |
| pandas rolling corr for DXY | scipy.stats.pearsonr | pandas rolling is simpler for time-series correlation windows |
| Custom position sizing | External risk library | No standard Python library exists for this; custom is appropriate and simple |

**Installation:**
```bash
# No new dependencies needed -- all packages already in requirements.txt
```

## Architecture Patterns

### Recommended Project Structure
```
app/
  services/
    strategy_selector.py     # Composite scoring, volatility regime, strategy ranking
    signal_generator.py      # Signal generation, validation, dedup, expiry
    risk_manager.py          # Position sizing, concurrent limits, daily loss tracking
    gold_intelligence.py     # Session identification, DXY correlation, volatility profiles
    signal_pipeline.py       # Orchestrator: wires selector -> generator -> validator -> risk
    # Existing:
    backtester.py
    metrics_calculator.py
    trade_simulator.py
    spread_model.py
    candle_ingestor.py
  workers/
    jobs.py                  # Add run_signal_scanner() job function
    scheduler.py             # Add scanner loop registration
```

### Pattern 1: Pipeline Orchestrator
**What:** A `SignalPipeline` class that composes individual service classes into a sequential flow: select strategy -> generate signals -> validate signals -> check risk -> persist signals.
**When to use:** When multiple independent subsystems must execute in a defined order with early-exit (rejection) at each stage.
**Example:**
```python
# Source: Domain pattern derived from codebase analysis
class SignalPipeline:
    """Orchestrates the full signal generation pipeline."""

    def __init__(
        self,
        selector: StrategySelector,
        generator: SignalGenerator,
        risk_manager: RiskManager,
        gold_intel: GoldIntelligence,
    ) -> None:
        self.selector = selector
        self.generator = generator
        self.risk_manager = risk_manager
        self.gold_intel = gold_intel

    async def run(self, session: AsyncSession) -> list[Signal]:
        """Execute the full pipeline, returning persisted signals."""
        # 1. Select best strategy based on backtest metrics + current regime
        strategy_name = await self.selector.select_best(session)
        if strategy_name is None:
            return []

        # 2. Run strategy.analyze() on latest candle data
        candidates = await self.generator.generate(session, strategy_name)

        # 3. Validate: R:R filter, confidence filter, dedup, expiry
        validated = await self.generator.validate(session, candidates)

        # 4. Risk checks: concurrent limit, daily loss, position sizing
        approved = await self.risk_manager.check(session, validated)

        # 5. Enrich with gold intelligence (session metadata, DXY)
        enriched = self.gold_intel.enrich(approved)

        # 6. Persist to signals table
        persisted = await self._persist(session, enriched)
        return persisted
```

### Pattern 2: Composite Scoring with Weighted Metrics
**What:** Strategy ranking using weighted linear combination of normalized backtest metrics. Each metric is normalized to [0,1] range, multiplied by its weight, and summed for a composite score.
**When to use:** Strategy selection (SEL-01 through SEL-07).
**Example:**
```python
# Source: Domain pattern for strategy scoring
# Weights aligned with user decision: win rate primary, profit factor close behind
METRIC_WEIGHTS = {
    "win_rate": 0.30,         # Primary -- consistency
    "profit_factor": 0.25,    # Close second -- profitability
    "sharpe_ratio": 0.15,     # Risk-adjusted returns
    "expectancy": 0.15,       # Average trade expectation
    "max_drawdown": 0.15,     # Inverted -- lower is better
}

def compute_composite_score(
    win_rate: float,
    profit_factor: float,
    sharpe_ratio: float,
    expectancy: float,
    max_drawdown: float,
) -> float:
    """Compute weighted composite score from backtest metrics.

    All inputs should be pre-normalized to [0, 1] range.
    max_drawdown is inverted: lower drawdown = higher score.
    """
    return (
        METRIC_WEIGHTS["win_rate"] * win_rate
        + METRIC_WEIGHTS["profit_factor"] * profit_factor
        + METRIC_WEIGHTS["sharpe_ratio"] * sharpe_ratio
        + METRIC_WEIGHTS["expectancy"] * expectancy
        + METRIC_WEIGHTS["max_drawdown"] * (1.0 - max_drawdown)
    )
```

### Pattern 3: Volatility Regime Detection via ATR Percentile
**What:** Classify current market volatility as LOW/MEDIUM/HIGH by comparing the current ATR to a rolling window of ATR values using percentile thresholds.
**When to use:** SEL-02 (regime detection) and SEL-03 (regime-aware selection).
**Example:**
```python
# Source: Domain pattern for volatility regime classification
from enum import Enum

class VolatilityRegime(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"

def classify_regime(
    current_atr: float,
    atr_series: pd.Series,
    low_pct: float = 25.0,
    high_pct: float = 75.0,
) -> VolatilityRegime:
    """Classify current volatility regime using ATR percentile rank.

    Args:
        current_atr: Current ATR value.
        atr_series: Historical ATR values (e.g., 30-day rolling).
        low_pct: Percentile below which is LOW regime.
        high_pct: Percentile above which is HIGH regime.
    """
    percentile = (atr_series < current_atr).sum() / len(atr_series) * 100
    if percentile <= low_pct:
        return VolatilityRegime.LOW
    elif percentile >= high_pct:
        return VolatilityRegime.HIGH
    return VolatilityRegime.MEDIUM
```

### Pattern 4: Stale Data Guard (No-Op Scanner)
**What:** Before running the pipeline, check if new candle data has arrived since the last scan. If no new data, no-op to avoid generating duplicate signals.
**When to use:** SIG-08 -- scanner loop runs on schedule, no-ops if no new candle data.
**Example:**
```python
# Source: Domain pattern for stale data detection
async def has_new_candle(
    session: AsyncSession, symbol: str, timeframe: str, since: datetime
) -> bool:
    """Check if a candle exists with timestamp > since."""
    stmt = (
        select(func.count())
        .select_from(Candle)
        .where(
            Candle.symbol == symbol,
            Candle.timeframe == timeframe,
            Candle.timestamp > since,
        )
    )
    result = await session.execute(stmt)
    return result.scalar_one() > 0
```

### Anti-Patterns to Avoid
- **God function pipeline:** Do not write a single monolithic function that does selection + generation + validation + risk + persistence. Split into composable service classes.
- **Floating-point comparison for prices:** Always use `Decimal(str(round(x, 2)))` at the persistence boundary. Internal float math is fine (established pattern from Phase 2-3).
- **Direct strategy import in pipeline module:** Import strategies inside the job function (not module level) to trigger auto-registration and avoid circular imports (established pattern from Phase 3).
- **Blocking I/O in async context:** All database queries must use async session. DXY data fetching should use httpx (async) or be run in a thread pool if using a sync client.
- **Hardcoded thresholds scattered throughout:** Use a central configuration dataclass or pydantic Settings sub-model for all pipeline thresholds (R:R minimum, confidence minimum, risk percentages, etc.).

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| ATR computation | Custom ATR formula | `pandas_ta_classic.atr()` via `compute_atr()` helper | Already wrapped and tested in Phase 2 |
| Session identification | New session detection code | `get_active_sessions()` from `app.strategies.helpers.session_filter` | Already implemented with midnight-wrap handling |
| Spread lookup | New spread calculation | `SessionSpreadModel.get_spread()` | Already implements tightest-spread-during-overlap logic |
| Rolling correlation | Manual Pearson calculation | `pandas.Series.rolling(window).corr(other)` | Handles NaN, window management, edge cases |
| Signal pydantic validation | Manual field validation | Extend `CandidateSignal` pydantic model or create new validator | Pydantic handles type coercion, range checks |
| Metric normalization | Manual min-max scaling | `(value - min) / (max - min)` with clamping to [0, 1] | Simple formula but must handle division-by-zero and single-strategy edge case |

**Key insight:** Phase 4 is primarily business logic and orchestration. The compute-heavy, tricky-to-implement components (ATR, session detection, trade simulation, backtesting) were all built in Phases 1-3. Phase 4 consumes their outputs.

## Common Pitfalls

### Pitfall 1: Insufficient Backtest Data for Strategy Scoring
**What goes wrong:** Strategy selector queries BacktestResult but finds fewer than 50 trades, leading to unreliable composite scores that swing wildly based on small samples.
**Why it happens:** New strategies or short rolling windows produce few trades. The system is new and may not have enough historical data yet.
**How to avoid:** Enforce `total_trades >= 50` minimum before including a strategy in scoring (SEL-07). If no strategy meets the threshold, log a warning and skip signal generation rather than selecting a poorly-validated strategy.
**Warning signs:** Composite scores changing dramatically between runs; strategies with <50 trades getting high scores.

### Pitfall 2: Decimal vs Float Confusion at Boundaries
**What goes wrong:** Mixing Decimal and float comparisons produces subtle bugs. For example, `Decimal("1.50") > 1.5` works in Python but `Decimal("1.50") > float("inf")` does not behave as expected with some operations.
**Why it happens:** The codebase uses float internally for math and Decimal at the DB/model boundary (established in Phase 2).
**How to avoid:** Convert to float at the start of any computation function. Convert to `Decimal(str(round(x, 2)))` only at the persistence boundary. Document this pattern in each new service module.
**Warning signs:** Type errors in comparisons; signals with unexpectedly precise prices.

### Pitfall 3: Race Condition in Concurrent Signal Check
**What goes wrong:** Two scanner runs executing concurrently both see 0 active signals, both generate a signal, resulting in >2 concurrent signals (violating RISK-02).
**Why it happens:** APScheduler has `max_instances=1` configured (preventing concurrent runs of the same job), but if the scanner is triggered manually while a scheduled run is in progress, it could still race.
**How to avoid:** Rely on APScheduler's `max_instances=1` (already configured). Additionally, use a DB-level check within a transaction: query active signal count, validate, and insert in one atomic commit.
**Warning signs:** More than 2 active signals in the signals table at the same time.

### Pitfall 4: Dedup Window Too Short or Too Long
**What goes wrong:** Too short: multiple signals for the same direction generated within minutes. Too long: legitimate new setups blocked because the window hasn't expired.
**Why it happens:** Gold can form the same setup on different timeframes or at different session opens.
**How to avoid:** Use a 4-hour dedup window for same-direction signals from the same strategy. This is long enough to prevent duplicate signals on consecutive H1 bars but short enough to allow new setups after market conditions change meaningfully.
**Warning signs:** Signal log showing clusters of identical-direction signals; or long gaps with no signals despite changing market conditions.

### Pitfall 5: Daily Loss Tracking Not Resetting
**What goes wrong:** Daily loss counter accumulates across days, permanently suppressing signals after one bad day.
**Why it happens:** The daily loss limit check uses a running total that is not reset at the correct UTC boundary.
**How to avoid:** Track daily P&L by querying outcomes for signals created since midnight UTC today. Use `datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)` as the day boundary. Do not maintain a persistent counter -- derive it from the DB each scan.
**Warning signs:** No signals generated for multiple consecutive days after a losing day.

### Pitfall 6: Importing Strategies at Module Level in Pipeline
**What goes wrong:** Circular import error when `signal_pipeline.py` imports from `app.strategies` which imports from `app.strategies.base` which may reference models.
**Why it happens:** Phase 3 already solved this by importing strategies inside the job function body.
**How to avoid:** Follow the Phase 3 pattern: import strategies inside `run_signal_scanner()` function body in `jobs.py`, not at module level. The pipeline service itself should accept a strategy instance, not import strategies directly.
**Warning signs:** `ImportError` or `AttributeError` at import time.

### Pitfall 7: DXY Data Source Unavailability
**What goes wrong:** DXY correlation monitoring fails because the external data source is down, causing the entire pipeline to crash.
**Why it happens:** External API dependency without graceful degradation.
**How to avoid:** Make DXY correlation a non-blocking enrichment step. If DXY data is unavailable, log a warning and proceed with signal generation without DXY enrichment. Never let DXY failure block signal generation.
**Warning signs:** Pipeline job failing with HTTP errors; no signals generated despite valid market conditions.

## Code Examples

### Position Sizing Calculator (RISK-01, RISK-03, RISK-06)
```python
# Source: Standard forex position sizing formula, ATR-adjusted
from decimal import Decimal

PIP_VALUE = 0.10  # XAUUSD: $0.10 per pip (price movement)

def calculate_position_size(
    account_balance: float,
    risk_pct: float,         # e.g., 0.01 for 1%
    sl_distance_price: float,  # SL distance in price units (e.g., 3.50)
    current_atr: float,
    baseline_atr: float,     # e.g., median ATR over 30 days
) -> Decimal:
    """Calculate volatility-adjusted position size in lots.

    Formula:
        risk_amount = account_balance * risk_pct
        atr_factor = baseline_atr / current_atr  (shrinks in high vol)
        position_size = (risk_amount / sl_distance_price) * atr_factor

    Args:
        account_balance: Current account equity.
        risk_pct: Risk percentage per trade (0.01 = 1%).
        sl_distance_price: Stop loss distance in price units.
        current_atr: Current ATR(14) value.
        baseline_atr: Median ATR(14) over rolling 30 days.

    Returns:
        Position size as Decimal with 2 decimal places.
    """
    if sl_distance_price <= 0 or current_atr <= 0 or baseline_atr <= 0:
        return Decimal("0.01")  # Minimum lot size

    risk_amount = account_balance * risk_pct
    sl_pips = sl_distance_price / PIP_VALUE

    # Volatility adjustment: reduce size in high volatility
    atr_factor = min(baseline_atr / current_atr, 1.5)  # Cap upward adjustment
    atr_factor = max(atr_factor, 0.5)  # Floor at 50% of normal size

    raw_size = (risk_amount / sl_distance_price) * atr_factor
    # Gold lots: 1 lot = 100 oz. Pip value per lot varies by broker.
    # For simplicity, size in "units" that the broker expects.
    return Decimal(str(round(raw_size, 2)))
```

### Signal Deduplication Check (SIG-05)
```python
# Source: Domain pattern for signal dedup
from datetime import datetime, timedelta, timezone
from sqlalchemy import select, and_

DEDUP_WINDOW_HOURS = 4  # Same-direction dedup window

async def is_duplicate_signal(
    session: AsyncSession,
    strategy_name: str,
    direction: str,
    symbol: str = "XAUUSD",
) -> bool:
    """Check if an active signal exists for same direction within dedup window.

    Returns True if a duplicate exists (signal should be suppressed).
    """
    cutoff = datetime.now(timezone.utc) - timedelta(hours=DEDUP_WINDOW_HOURS)
    stmt = (
        select(Signal.id)
        .where(
            and_(
                Signal.symbol == symbol,
                Signal.direction == direction,
                Signal.status == "active",
                Signal.created_at >= cutoff,
            )
        )
        .limit(1)
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none() is not None
```

### Daily Loss Limit Check (RISK-04)
```python
# Source: Domain pattern for daily loss tracking
from sqlalchemy import select, func, and_

async def get_daily_loss(session: AsyncSession) -> float:
    """Get total realized P&L for today (UTC) from outcomes.

    Returns negative value if in drawdown, positive if in profit.
    """
    today_start = datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    stmt = (
        select(func.coalesce(func.sum(Outcome.pnl_pips), 0))
        .join(Signal, Signal.id == Outcome.signal_id)
        .where(Signal.created_at >= today_start)
    )
    result = await session.execute(stmt)
    return float(result.scalar_one())
```

### Volatility Regime Detection (SEL-02)
```python
# Source: Domain pattern for ATR-based regime classification
import numpy as np

def detect_volatility_regime(
    atr_values: pd.Series,
    lookback: int = 30 * 24,  # 30 days of H1 bars
) -> VolatilityRegime:
    """Classify current volatility regime from H1 ATR history.

    Uses percentile rank of the most recent ATR value within
    the lookback window.

    Thresholds:
        LOW:    ATR below 25th percentile
        MEDIUM: ATR between 25th and 75th percentile
        HIGH:   ATR above 75th percentile
    """
    if len(atr_values) < lookback:
        return VolatilityRegime.MEDIUM  # Default when insufficient data

    window = atr_values.iloc[-lookback:]
    current = float(atr_values.iloc[-1])

    p25 = float(np.percentile(window, 25))
    p75 = float(np.percentile(window, 75))

    if current <= p25:
        return VolatilityRegime.LOW
    elif current >= p75:
        return VolatilityRegime.HIGH
    return VolatilityRegime.MEDIUM
```

### Strategy Degradation Detection (SEL-05, SEL-06)
```python
# Source: Domain pattern for strategy health monitoring
def check_degradation(
    current_win_rate: float,
    baseline_win_rate: float,
    current_profit_factor: float,
    win_rate_drop_threshold: float = 0.15,  # 15% absolute drop
    min_profit_factor: float = 1.0,
) -> tuple[bool, str]:
    """Check if a strategy has degraded significantly.

    Returns (is_degraded, reason).
    """
    reasons = []

    win_rate_drop = baseline_win_rate - current_win_rate
    if win_rate_drop > win_rate_drop_threshold:
        reasons.append(
            f"Win rate dropped {win_rate_drop:.1%} "
            f"(from {baseline_win_rate:.1%} to {current_win_rate:.1%})"
        )

    if current_profit_factor < min_profit_factor:
        reasons.append(
            f"Profit factor {current_profit_factor:.2f} below {min_profit_factor:.2f}"
        )

    is_degraded = len(reasons) > 0
    reason = "; ".join(reasons) if reasons else "healthy"
    return is_degraded, reason
```

### DXY Rolling Correlation (GOLD-04)
```python
# Source: pandas official documentation for rolling correlation
def compute_dxy_correlation(
    gold_prices: pd.Series,
    dxy_prices: pd.Series,
    window: int = 30,
) -> pd.Series:
    """Compute 30-period rolling correlation between gold and DXY.

    Returns correlation series where -1.0 = perfect inverse,
    +1.0 = perfect positive, 0 = no correlation.

    Typical gold-DXY correlation: -0.7 to -0.9 (inverse).
    Divergence signal: correlation > -0.3 (weakening inverse).
    """
    return gold_prices.rolling(window).corr(dxy_prices)
```

### Signal Expiry Logic (SIG-06)
```python
# Source: Domain pattern for signal expiry
from datetime import datetime, timedelta, timezone

# Expiry times by timeframe type
EXPIRY_HOURS = {
    "M15": 4,    # Scalp: 4 hours
    "H1": 8,     # Intraday: 8 hours
    "H4": 24,    # Intraday/swing: 24 hours
    "D1": 48,    # Swing: 48 hours
}

def compute_expiry(timeframe: str, created_at: datetime) -> datetime:
    """Compute signal expiry timestamp based on timeframe."""
    hours = EXPIRY_HOURS.get(timeframe, 8)  # Default 8h
    return created_at + timedelta(hours=hours)
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Single strategy, no selection | Composite-scored multi-strategy selection | Current best practice | Adapts to changing market conditions |
| Fixed position sizing | ATR-adjusted volatility-scaled sizing | Standard since ~2015 | Reduces risk in high-volatility, captures more in low-volatility |
| Manual signal review | Automated validation pipeline with filters | Standard in algo trading | Removes emotional bias, enforces consistency |
| Session-blocking (no Asian trades) | Session-metadata tracking without blocking | User decision | Captures Asian session opportunities while tracking session performance |

**Deprecated/outdated:**
- Fixed R:R ratios (not ATR-adjusted): Modern practice uses ATR-based dynamic SL/TP distances, which this system already does in Phase 2 strategies.
- Profit-factor-only strategy ranking: Composite scoring with multiple metrics is standard; single-metric ranking is fragile.

## Claude's Discretion Recommendations

Based on research, here are prescriptive recommendations for areas marked as Claude's discretion:

### Metric Weight Percentages
- **Win rate: 0.30** (primary, per user decision)
- **Profit factor: 0.25** (close second, per user decision)
- **Sharpe ratio: 0.15** (risk-adjusted returns -- important for prop firm)
- **Expectancy: 0.15** (average pip gain per trade)
- **Max drawdown: 0.15** (inverted -- critical for prop firm capital protection)
- **Rationale:** Win rate and profit factor together account for 55% of the score, reflecting the user's emphasis on consistency with profitability. The remaining 45% is split equally among risk-adjusted metrics, which are critical for prop firm compliance.

### Volatility Regime Thresholds
- **LOW:** ATR below 25th percentile of 30-day rolling window
- **MEDIUM:** ATR between 25th and 75th percentile
- **HIGH:** ATR above 75th percentile
- **Regime-strategy mapping:** In HIGH volatility, deprioritize breakout strategies (prone to false breakouts). In LOW volatility, deprioritize trend continuation (insufficient momentum). Apply a +10% / -10% modifier to composite score based on regime suitability.

### Multi-Timeframe Confluence Scoring
- **Approach:** When generating a signal on H1, also check if the H4 timeframe shows agreement in direction (EMA trend alignment). If both agree, add a +5 confidence boost.
- **Implementation:** Query latest H4 candle data, compute EMA-50 vs EMA-200 direction, compare with H1 signal direction. Simple boolean check, not a separate signal generation pass.
- **Rationale:** Keeps implementation simple while capturing the most impactful confluence -- higher timeframe trend alignment.

### Dedup Window Duration
- **4 hours** for same-direction signals from any strategy
- **Rationale:** Gold on H1 bars means ~4 bars per window. This prevents the same setup from generating duplicate signals on consecutive bars while allowing new signals after meaningful price action.

### Signal Expiry Times
- **H1 signals: 8 hours** (approximately one full trading session)
- **H4 signals: 24 hours** (allows signal to develop over a day)
- **Rationale:** Aligned with typical gold price movement horizons. H1 intraday setups should resolve within one major session. H4 swing setups need a full day.

### Position Sizing Volatility Adjustment
- **ATR-scaled model:** `position_size = base_size * (baseline_atr / current_atr)` with floor at 0.5x and cap at 1.5x.
- **baseline_atr:** Median ATR(14) over the trailing 30 days.
- **Rationale:** Simple, transparent, and well-established in forex risk management. Automatically reduces position size during volatile periods (prop firm safety) and increases during calm periods.

### DXY Correlation Monitoring
- **Approach:** Store DXY daily close prices alongside gold candles. Compute 30-day rolling Pearson correlation. When correlation weakens beyond -0.3 (from typical -0.7 to -0.9), flag as a "divergence" condition.
- **Impact on signals:** Add a note to signal reasoning when divergence detected. Do not suppress signals based on DXY alone -- informational only.
- **Data source:** Use Twelve Data API (already integrated) to fetch DXY daily data. Fallback: skip DXY enrichment if data unavailable.
- **Rationale:** The gold-DXY inverse correlation is a well-known relationship but is not ironclad (both can rise together in crisis scenarios). Using it as informational metadata rather than a hard filter is more robust.

### Session Volatility Profiles
- **Assessment: Moderate value.** Session volatility profiles provide useful context (Asian = lower vol, London/NY overlap = highest vol) but the user explicitly decided against session-based suppression. Recommendation: Track session metadata on each signal for later analysis, but do not adjust confidence or suppress signals based on session alone.
- **London/NY overlap boost:** Add +5 confidence points to signals generated during London/NY overlap (12:00-16:00 UTC). This is a mild boost reflecting the higher liquidity and historical significance, not a hard filter.

## Open Questions

1. **DXY Data via Twelve Data API**
   - What we know: Twelve Data supports index data including DXY. The project already has a Twelve Data integration.
   - What's unclear: Whether the free tier includes DXY data, and the exact API endpoint/symbol for DXY.
   - Recommendation: Use `symbol="DXY"` or `symbol="DX-Y.NYB"` in the Twelve Data API. If not available on free tier, skip DXY monitoring entirely (it's informational-only).

2. **StrategyPerformance Model vs BacktestResult for Scoring**
   - What we know: `StrategyPerformance` has `win_rate`, `profit_factor`, `avg_rr`, `total_signals`, `is_degraded`. `BacktestResult` has `win_rate`, `profit_factor`, `sharpe_ratio`, `max_drawdown`, `expectancy`, `total_trades`.
   - What's unclear: Which model the strategy selector should query. `BacktestResult` has all 5 metrics needed for composite scoring. `StrategyPerformance` was designed for rolling performance tracking and lacks `sharpe_ratio`, `max_drawdown`, and `expectancy`.
   - Recommendation: Use `BacktestResult` as the primary data source for composite scoring (it has all 5 metrics). The `StrategyPerformance` model may need schema migration to add the missing columns, or the selector can query `BacktestResult` directly (filtering for the most recent non-walk-forward result per strategy). **Prefer querying BacktestResult directly** to avoid schema migration in this phase.

3. **Account Balance for Position Sizing**
   - What we know: Position sizing requires current account balance (RISK-01: 1% of account). The system doesn't currently track account balance.
   - What's unclear: Where account balance comes from -- manual configuration, API query, or hardcoded.
   - Recommendation: Add `account_balance` as a Settings field (environment variable). For a prop firm account, balance is relatively static and can be configured at deployment. No need for real-time balance queries in this phase.

4. **Signal Status Lifecycle**
   - What we know: `Signal.status` has a default of "active". Outcome tracking is in Phase 5+.
   - What's unclear: What transitions the signal from "active" to "expired" or "filled".
   - Recommendation: For Phase 4, signals start as "active". Add an `expire_stale_signals()` function that runs before each scanner cycle, marking signals past their `expires_at` as "expired". Outcome tracking (filled/sl_hit/tp_hit) will be handled in a later phase.

## Sources

### Primary (HIGH confidence)
- Existing codebase analysis: `app/models/signal.py`, `app/models/backtest_result.py`, `app/models/strategy_performance.py`, `app/strategies/base.py`, `app/services/backtester.py`, `app/services/metrics_calculator.py`, `app/workers/jobs.py`, `app/workers/scheduler.py` -- All code patterns verified by direct file reading
- Existing session filter: `app/strategies/helpers/session_filter.py` -- Session definitions with UTC hours and midnight-wrap handling
- APScheduler 3.x documentation -- IntervalTrigger and CronTrigger configuration verified
- pandas documentation -- `rolling().corr()` method for DXY correlation

### Secondary (MEDIUM confidence)
- [NordFX Gold Session Volatility Guide](https://nordfx.com/en/traders-guide/best-time-to-trade-gold-xauusd-sessions-volatility-news) -- Qualitative session volatility levels (Low/Moderate/High/Very High)
- [AlphaEx Capital: Volatility-Based Position Sizing](https://www.alphaexcapital.com/forex/forex-risk-management-and-psychology/forex-risk-management-basics/volatility-based-position-sizing/) -- ATR-scaled position sizing formula
- [FundedNext: Prop Firm Trading Rules](https://fundednext.com/blog/prop-firm-trading-rules) -- Daily loss limit calculation models
- [MasterFunders: Prop Firm Rules Explained](https://masterfunders.com/prop-firm-rules/) -- Risk-per-trade best practices (0.5-1%)
- [ForexGDP: Gold-DXY Correlation](https://www.forexgdp.com/analysis/xauusd/gold-dxy-correlation/) -- Inverse correlation approximately -0.73 to -0.95
- [Statology: Rolling Correlation in Pandas](https://www.statology.org/rolling-correlation-pandas/) -- `rolling().corr()` usage patterns

### Tertiary (LOW confidence)
- Gold session-specific ATR values (exact pip ranges per session) -- Could not find authoritative quantitative data. Qualitative consensus: Asian ~40-60% of London volatility, London/NY overlap is highest. Recommend deriving session volatility profiles from the system's own historical data rather than hardcoding external values.
- DXY availability on Twelve Data free tier -- Not verified. Recommend testing during implementation.

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH -- No new libraries needed; all dependencies already installed and tested
- Architecture: HIGH -- Pipeline pattern is straightforward composition of existing building blocks; all interfaces verified against existing code
- Pitfalls: HIGH -- Identified from direct codebase analysis (circular imports, Decimal/float boundaries, APScheduler config) and domain knowledge (dedup windows, daily loss resets)
- Code examples: MEDIUM -- Patterns are well-established but have not been run against this specific codebase; implementations may need minor adjustments
- Gold intelligence (DXY, session profiles): MEDIUM -- Qualitative patterns well-documented, but exact quantitative values should be derived from the system's own data rather than external sources

**Research date:** 2026-02-17
**Valid until:** 2026-03-17 (30 days -- stable domain, no fast-moving library dependencies)
