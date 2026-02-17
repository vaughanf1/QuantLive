# Phase 3: Backtesting Engine - Research

**Researched:** 2026-02-17
**Domain:** Rolling-window backtesting, trade simulation, walk-forward validation, metric calculation, session-aware spread modeling
**Confidence:** HIGH (core architecture verified against codebase; vectorbt decision backed by official docs and PyPI)

## Summary

Phase 3 builds a backtesting engine that evaluates each strategy's historical performance by running the **exact same** `strategy.analyze(candles)` code used for live signal generation on rolling windows of historical data, then simulating the resulting `CandidateSignal` trades against subsequent price action to determine outcomes (SL hit, TP1 hit, TP2 hit). The engine calculates five core metrics (win rate, profit factor, Sharpe ratio, max drawdown, expectancy), performs 80/20 walk-forward validation to detect overfitting, accounts for session-appropriate spread costs, and persists all results to the existing `backtest_results` database table.

The critical architectural decision is whether to use vectorbt or a manual pandas-based trade simulator. After thorough analysis, **the recommendation is a hybrid approach**: use a lightweight manual pandas trade simulator for the trade-level simulation (because our strategies produce `CandidateSignal` objects with predefined entry/SL/TP prices, which do not naturally map to vectorbt's boolean signal arrays), but use vectorbt for portfolio-level metric calculation where its optimized `pf.stats()` pipeline provides verified Sharpe ratio, max drawdown, and other metrics. If vectorbt integration proves too cumbersome for the predefined-price trade pattern, fall back to pure manual pandas metric calculation -- the formulas are simple and well-defined.

**Primary recommendation:** Build a `BacktestRunner` service that (1) queries candle windows from the database, (2) calls `strategy.analyze()` to get CandidateSignals, (3) simulates each trade by walking forward through subsequent OHLC bars checking SL/TP hits with spread-adjusted prices, (4) calculates metrics from the list of simulated trades, (5) performs walk-forward validation by splitting the window 80/20, and (6) persists results. Schedule as a daily APScheduler CronTrigger job.

## Standard Stack

### Core

| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| pandas | `>=2.0` | Data manipulation, rolling windows, trade simulation | Already in project; native DataFrame operations for candle slicing and trade iteration |
| numpy | `>=1.26` | Numeric computation for metrics | Already in project; fast vectorized operations for Sharpe, drawdown calculations |
| vectorbt | `0.28.4` | Portfolio metrics (optional) | Latest free version; provides verified `pf.stats()` with Sharpe, profit factor, max drawdown, win rate, expectancy. Supports Python 3.12. Fair-code license (Apache 2.0 + Commons Clause) |
| APScheduler | `>=3.10.4,<4.0` | Background job scheduling | Already in project; AsyncIOScheduler with CronTrigger for daily backtest execution |
| SQLAlchemy | `>=2.0.36` (async) | Database access for candle queries and result persistence | Already in project; async queries for candle windows, bulk inserts for results |

### Supporting

| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| scipy | `>=1.12` | Statistical functions | Already in project; may be useful for advanced statistical tests on walk-forward results |

### Alternatives Considered

| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| Manual trade simulator + vectorbt metrics | Pure vectorbt `from_signals()` | vectorbt expects boolean entry/exit arrays aligned with price series. Our strategies produce `CandidateSignal` with absolute entry/SL/TP prices. Converting requires building boolean arrays + sl_stop/tp_stop, but vectorbt only supports ONE TP target per entry (not TP1 + TP2). Manual simulation is simpler and more accurate for our use case |
| Manual trade simulator + manual metrics | Pure vectorbt | Full manual control avoids vectorbt dependency entirely. Formulas for win rate, profit factor, Sharpe, max drawdown, expectancy are straightforward. This is the fallback if vectorbt adds friction |
| vectorbt `from_orders()` | Manual simulation | `from_orders()` takes order arrays (size, price, direction) which could encode our trades. However, dual TP targets and spread modeling still require workarounds. The impedance mismatch exceeds the benefit |
| backtesting.py | vectorbt or manual | backtesting.py requires subclassing its `Strategy` class with `next()` method -- fundamentally incompatible with our `BaseStrategy.analyze()` interface. Would require duplicating strategy logic (violates BACK-02) |

**Installation:**
```bash
pip install vectorbt==0.28.4
```

Note: vectorbt 0.28.4 uses `pandas-ta-classic` (same dependency already in our project) and supports Python 3.10-3.13. It brings in numba as a dependency which is a ~100MB install but provides significant computation speed.

## Architecture Patterns

### Recommended Project Structure
```
app/
├── services/
│   └── backtester.py          # BacktestRunner service (core engine)
├── services/
│   └── trade_simulator.py     # TradeSimulator (walks CandidateSignals through OHLC)
├── services/
│   └── metrics_calculator.py  # MetricsCalculator (computes 5 required metrics)
├── services/
│   └── spread_model.py        # SessionSpreadModel (session-aware spread costs)
├── workers/
│   └── jobs.py                # Add run_backtests() job function
│   └── scheduler.py           # Add daily backtest CronTrigger registration
├── models/
│   └── backtest_result.py     # Already exists -- may need migration for new fields
└── strategies/
    └── base.py                # Unchanged -- analyze() is reused directly
```

### Pattern 1: Trade Simulation from CandidateSignals (Core Pattern)

**What:** Given a list of `CandidateSignal` objects and subsequent OHLC data, simulate each trade to determine its outcome.

**When to use:** This is the heart of the backtester. Each CandidateSignal has predefined entry_price, stop_loss, take_profit_1, and take_profit_2. The simulator walks forward bar-by-bar through subsequent candles to determine which level is hit first.

**Example:**
```python
# Source: Manual implementation based on project architecture
from dataclasses import dataclass
from decimal import Decimal
from enum import Enum

class TradeOutcome(str, Enum):
    TP1_HIT = "tp1_hit"
    TP2_HIT = "tp2_hit"
    SL_HIT = "sl_hit"
    EXPIRED = "expired"  # no hit within lookback window

@dataclass
class SimulatedTrade:
    signal: CandidateSignal
    outcome: TradeOutcome
    exit_price: Decimal
    pnl_pips: Decimal       # in gold, 1 pip = $0.10 price movement
    bars_held: int
    spread_cost: Decimal     # spread applied at entry

class TradeSimulator:
    """Simulate CandidateSignal trades against OHLC candle data."""

    MAX_BARS_FORWARD = 72  # 72 H1 bars = 3 days max hold

    def simulate_trade(
        self,
        signal: CandidateSignal,
        candles: pd.DataFrame,
        signal_bar_idx: int,
        spread: Decimal,
    ) -> SimulatedTrade:
        """Walk forward from signal bar, checking SL/TP hits on each bar.

        For BUY trades:
          - Entry is adjusted UP by spread (buying at ask = bid + spread)
          - SL is checked against bar low (bid side)
          - TP is checked against bar high (bid side, but entry was at ask)

        For SELL trades:
          - Entry is at bid (no spread adjustment on entry)
          - SL is checked against bar high + spread (ask side)
          - TP is checked against bar low (bid side)
        """
        entry = float(signal.entry_price)
        sl = float(signal.stop_loss)
        tp1 = float(signal.take_profit_1)
        tp2 = float(signal.take_profit_2)

        # Apply spread to entry
        if signal.direction == Direction.BUY:
            entry += float(spread)
        # For SELL, spread is already accounted in exit check

        for j in range(signal_bar_idx + 1,
                       min(signal_bar_idx + 1 + self.MAX_BARS_FORWARD, len(candles))):
            bar_high = candles.iloc[j]["high"]
            bar_low = candles.iloc[j]["low"]

            if signal.direction == Direction.BUY:
                # Check SL first (conservative: SL hit takes priority)
                if bar_low <= sl:
                    pnl = sl - entry
                    return SimulatedTrade(
                        signal=signal, outcome=TradeOutcome.SL_HIT,
                        exit_price=Decimal(str(round(sl, 2))),
                        pnl_pips=Decimal(str(round(pnl * 10, 2))),
                        bars_held=j - signal_bar_idx,
                        spread_cost=spread,
                    )
                # Check TP2 first (if bar reaches TP2, it also hit TP1)
                if bar_high >= tp2:
                    pnl = tp2 - entry
                    return SimulatedTrade(...)
                if bar_high >= tp1:
                    pnl = tp1 - entry
                    return SimulatedTrade(...)
            else:  # SELL
                # Mirror logic for shorts
                ...

        # Expired -- no hit within MAX_BARS_FORWARD
        last_close = candles.iloc[min(signal_bar_idx + self.MAX_BARS_FORWARD,
                                      len(candles) - 1)]["close"]
        ...
```

### Pattern 2: Rolling Window Backtest

**What:** Slide a window across historical data, running strategy.analyze() on each window position, then simulating the resulting trades.

**When to use:** For BACK-01. Primary window is 30 days, secondary is 60 days.

**Example:**
```python
# Source: Standard rolling window pattern
from datetime import datetime, timedelta

class BacktestRunner:
    """Orchestrates rolling-window backtests for all strategies."""

    async def run_rolling_backtest(
        self,
        strategy: BaseStrategy,
        candles: pd.DataFrame,
        window_days: int,
        step_days: int = 1,
    ) -> list[SimulatedTrade]:
        """Run strategy over rolling windows, collect all simulated trades.

        Args:
            strategy: Strategy instance (same code as live)
            candles: Full historical DataFrame sorted by timestamp
            window_days: Analysis window size (30 or 60)
            step_days: How far to advance the window each step
        """
        all_trades: list[SimulatedTrade] = []

        # Convert window to candle count (H1 = 24 candles/day, ~5 trading days/week)
        candles_per_day = 24  # H1 timeframe
        window_candles = window_days * candles_per_day

        for start_idx in range(0, len(candles) - window_candles,
                               step_days * candles_per_day):
            end_idx = start_idx + window_candles
            window = candles.iloc[start_idx:end_idx].reset_index(drop=True)

            # Run SAME analyze() as live -- BACK-02 compliance
            try:
                signals = strategy.analyze(window)
            except InsufficientDataError:
                continue

            # Simulate each signal against bars AFTER the analysis window
            for signal in signals:
                # Find signal bar index within full candle set
                # Simulate against subsequent bars
                trade = self.simulator.simulate_trade(
                    signal, candles, end_idx,
                    spread=self.spread_model.get_spread(signal.timestamp)
                )
                all_trades.append(trade)

        return all_trades
```

### Pattern 3: Walk-Forward Validation (80/20 Split)

**What:** Split data into 80% in-sample (training) and 20% out-of-sample (testing). Run backtests on both halves independently. Compare metrics to detect overfitting.

**When to use:** For BACK-04. A strategy is flagged as potentially overfitted if out-of-sample performance degrades significantly vs in-sample.

**Example:**
```python
class WalkForwardValidator:
    """Detect overfitting via in-sample / out-of-sample comparison."""

    # If OOS metric is below this fraction of IS metric, flag as overfitting
    DEGRADATION_THRESHOLD = 0.5  # OOS win_rate < 50% of IS win_rate = red flag

    def validate(
        self,
        candles: pd.DataFrame,
        strategy: BaseStrategy,
    ) -> WalkForwardResult:
        """Run walk-forward validation with 80/20 split."""
        split_idx = int(len(candles) * 0.8)

        is_candles = candles.iloc[:split_idx]  # In-sample (80%)
        oos_candles = candles.iloc[split_idx:]  # Out-of-sample (20%)

        is_trades = self.runner.run_backtest(strategy, is_candles)
        oos_trades = self.runner.run_backtest(strategy, oos_candles)

        is_metrics = self.calculator.compute(is_trades)
        oos_metrics = self.calculator.compute(oos_trades)

        # Walk Forward Efficiency: OOS performance / IS performance
        # WFE > 0.5 generally indicates robustness
        is_overfitted = False

        if is_metrics.win_rate > 0:
            wfe_win_rate = oos_metrics.win_rate / is_metrics.win_rate
            if wfe_win_rate < self.DEGRADATION_THRESHOLD:
                is_overfitted = True

        if is_metrics.profit_factor > 0:
            wfe_pf = oos_metrics.profit_factor / is_metrics.profit_factor
            if wfe_pf < self.DEGRADATION_THRESHOLD:
                is_overfitted = True

        return WalkForwardResult(
            is_metrics=is_metrics,
            oos_metrics=oos_metrics,
            is_overfitted=is_overfitted,
            wfe_win_rate=wfe_win_rate,
            wfe_profit_factor=wfe_pf,
        )
```

### Pattern 4: Session-Aware Spread Model

**What:** Apply realistic spread costs based on the trading session active at signal time.

**When to use:** For BACK-05. Gold spreads vary dramatically by session -- Asian session spreads can be 4x wider than London/NY overlap.

**Example:**
```python
class SessionSpreadModel:
    """Session-appropriate spread costs for XAUUSD backtesting.

    Spread values in price units (not pips). For gold, 1 pip = $0.10.
    These are conservative estimates representing typical retail broker spreads.
    """

    # Spread in price units ($) -- conservative fixed values per session
    SESSION_SPREADS: dict[str, Decimal] = {
        "overlap": Decimal("0.20"),    # London/NY overlap: tightest spreads (~2 pips)
        "london": Decimal("0.30"),     # London session: tight (~3 pips)
        "new_york": Decimal("0.30"),   # NY session: tight (~3 pips)
        "asian": Decimal("0.50"),      # Asian session: wider (~5 pips)
    }
    DEFAULT_SPREAD = Decimal("0.50")   # Off-session / unknown: conservative

    def get_spread(self, timestamp: datetime) -> Decimal:
        """Return spread for the trading session active at timestamp."""
        from app.strategies.helpers.session_filter import get_active_sessions

        sessions = get_active_sessions(timestamp)
        if not sessions:
            return self.DEFAULT_SPREAD

        # Use tightest spread if multiple sessions active
        spreads = [self.SESSION_SPREADS.get(s, self.DEFAULT_SPREAD) for s in sessions]
        return min(spreads)
```

### Anti-Patterns to Avoid

- **Separate backtest strategy code:** Never create strategy implementations that only exist for backtesting. The `analyze()` method used in backtesting MUST be the identical code path used in live signal generation. This is requirement BACK-02 and prevents a common source of live/backtest divergence.

- **Zero-spread backtesting:** Never run backtests without spread costs. Gold spreads are significant (3-5 pips retail) and can turn a marginally profitable strategy into a losing one. Always use the session-appropriate spread model (BACK-05).

- **Look-ahead bias:** When simulating trades from CandidateSignals, never use data from before the signal timestamp to determine the outcome. The simulator must only walk forward from the signal bar.

- **Float for financial metrics in DB:** Use `Numeric(10, 4)` for stored metrics (already correctly defined in the existing `BacktestResult` model). Never use `Float` columns for financial data.

- **Running backtests in API request path:** Backtests are computationally expensive (scanning 720+ candles per 30-day window for each strategy). They must run as background jobs (BACK-06), not in response to HTTP requests.

## Don't Hand-Roll

Problems that look simple but have existing solutions:

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Sharpe ratio calculation | Custom formula with subtle annualization bugs | `numpy` mean/std with `sqrt(252)` annualization factor, or vectorbt `pf.sharpe_ratio()` | Annualization factor (sqrt of trading days) is commonly miscalculated. Use 252 trading days for forex. Standard formula: `(mean_daily_return / std_daily_return) * sqrt(252)` |
| Max drawdown tracking | Manual peak tracking with off-by-one errors | `numpy` cumulative max approach: `drawdown = (cumulative_returns - cummax) / cummax` | Edge cases around empty trade lists, single-trade histories, and sign conventions |
| Session time classification | Hardcoded hour checks | `app.strategies.helpers.session_filter.get_active_sessions()` | Already exists in the codebase (Phase 2). Handles midnight wrapping and multi-session overlap |
| Candle data querying | Raw SQL per window | SQLAlchemy async query with `between()` filter on timestamp, reuse across windows | Database query patterns already established in `candle_ingestor.py` |
| Background scheduling | Custom task queue | APScheduler `CronTrigger` with `AsyncIOScheduler` | Already in project with proven patterns from Phase 1 candle refresh jobs |

**Key insight:** The trade simulation itself is the one part that genuinely needs custom code. Strategies produce CandidateSignals with absolute price levels (entry, SL, TP1, TP2), and no existing library naturally simulates "which of these three price levels does the market hit first" against OHLC bars. This ~50 lines of simulation logic is the true custom work; everything else has existing solutions.

## Common Pitfalls

### Pitfall 1: Look-Ahead Bias in Trade Simulation

**What goes wrong:** The simulator uses information from the signal bar itself (or earlier bars) to determine the trade outcome, or the strategy `analyze()` is inadvertently given future data.

**Why it happens:** When slicing candle windows, off-by-one errors can include the "next" bar's data in the analysis window, or the trade simulation can start checking from the signal bar instead of the next bar.

**How to avoid:** The simulator must start checking SL/TP from `signal_bar_idx + 1`. The analysis window must end at the signal bar, not after it. Write explicit tests that verify the simulator never accesses a bar at or before the signal timestamp.

**Warning signs:** Suspiciously high win rates (>80%) on all strategies. Perfect Sharpe ratios (>3). Backtests that perform dramatically better than expected.

### Pitfall 2: SL/TP Priority Within a Single Bar

**What goes wrong:** On a volatile bar, both SL and TP could theoretically be hit. The simulator must have a deterministic priority rule.

**Why it happens:** OHLC bars compress intra-bar price action. If bar low < SL and bar high > TP1, we cannot know which was hit first from OHLC data alone.

**How to avoid:** Use a conservative approach: **SL takes priority over TP** when both could be hit in the same bar. This prevents overestimating strategy performance. Document this assumption clearly.

**Warning signs:** Inconsistent results when changing bar resolution. Profit factor that seems too high for the win rate.

### Pitfall 3: Insufficient Candles After Window

**What goes wrong:** The rolling window reaches the end of available data and there are not enough subsequent bars to simulate trades (need MAX_BARS_FORWARD bars after the window ends).

**Why it happens:** The last few windows in the rolling backtest don't have enough future data for trade simulation.

**How to avoid:** Stop the rolling window early enough that `window_end + MAX_BARS_FORWARD <= total_candles`. Mark trades that couldn't be fully resolved as "expired" rather than discarding them silently.

**Warning signs:** Trade counts dropping unexpectedly for the last few window positions. Total trade count varying between runs on slightly different date ranges.

### Pitfall 4: Spread Model Invalidating All Trades

**What goes wrong:** Spread costs are set too high, causing nearly all trades to be immediately stopped out (entry + spread > SL distance).

**Why it happens:** XAUUSD spreads are expressed in dollar terms (e.g., $0.30), while gold prices are ~$2650. A $0.30 spread is only ~0.01% of price, but it's 3 pips which matters for tight stops.

**How to avoid:** Validate that the spread is less than 50% of the risk distance (entry to SL) for each trade. Log warnings for trades where spread exceeds 30% of risk distance. Use realistic spread values based on actual broker data.

**Warning signs:** Win rate near 0%. All trades showing SL_HIT outcome. Negative expectancy despite the strategy having good theoretical risk/reward.

### Pitfall 5: Mixing Decimal and Float in Metric Calculation

**What goes wrong:** Python `Decimal` objects from CandidateSignal prices get mixed with `float` operations in the simulator, causing `TypeError` or silent precision loss.

**Why it happens:** CandidateSignal uses `Decimal` for prices (enforced by Pydantic), but pandas DataFrames store candle data as `float64` (converted by `candles_to_dataframe()`).

**How to avoid:** Follow the existing project convention: use `float` for all internal calculations in the simulator, and convert to `Decimal(str(round(x, 2)))` only at the boundary when constructing `SimulatedTrade` or `BacktestResult` objects. The `candles_to_dataframe()` function already handles ORM-to-float conversion.

**Warning signs:** `TypeError: unsupported operand type(s)` exceptions. Decimal precision warnings. Slightly different metric values between runs.

### Pitfall 6: Walk-Forward Split That Leaves Too Few Trades

**What goes wrong:** The 20% out-of-sample slice produces too few trades for meaningful metric comparison, leading to noisy overfitting detection.

**Why it happens:** Some strategies (like liquidity_sweep with min_candles=100) require significant warmup data, and with only 20% of a 30-day window, there may be only 1-2 signals.

**How to avoid:** Set a minimum trade count threshold (e.g., >= 5 trades) for both IS and OOS results before comparing. If OOS has fewer trades, log a warning and skip overfitting detection for that strategy/window combination rather than making unreliable claims.

**Warning signs:** Walk-forward results flagging all strategies as "overfitted" or "not overfitted" uniformly. Wildly different WFE ratios between consecutive runs.

## Code Examples

### Metric Calculation (Manual Implementation)

```python
# Source: Standard trading metric formulas, verified against multiple references
import numpy as np
from dataclasses import dataclass
from decimal import Decimal

@dataclass
class BacktestMetrics:
    win_rate: Decimal       # 0-1 range (0.65 = 65%)
    profit_factor: Decimal  # gross_profit / gross_loss (>1 = profitable)
    sharpe_ratio: Decimal   # annualized risk-adjusted return
    max_drawdown: Decimal   # worst peak-to-trough decline (0-1 range)
    expectancy: Decimal     # average expected pnl per trade
    total_trades: int

class MetricsCalculator:
    """Calculate backtest performance metrics from simulated trades."""

    TRADING_DAYS_PER_YEAR = 252  # Standard for forex

    def compute(self, trades: list[SimulatedTrade]) -> BacktestMetrics:
        if not trades:
            return BacktestMetrics(
                win_rate=Decimal("0"), profit_factor=Decimal("0"),
                sharpe_ratio=Decimal("0"), max_drawdown=Decimal("0"),
                expectancy=Decimal("0"), total_trades=0,
            )

        pnls = [float(t.pnl_pips) for t in trades]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]

        # Win Rate
        win_rate = len(wins) / len(pnls) if pnls else 0

        # Profit Factor = sum(winning trades) / abs(sum(losing trades))
        gross_profit = sum(wins) if wins else 0
        gross_loss = abs(sum(losses)) if losses else 0
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else float('inf')
        # Cap at 99.99 for db storage
        profit_factor = min(profit_factor, 9999.9999)

        # Expectancy = (avg_win * win_rate) - (avg_loss * loss_rate)
        avg_win = np.mean(wins) if wins else 0
        avg_loss = abs(np.mean(losses)) if losses else 0
        loss_rate = 1 - win_rate
        expectancy = (avg_win * win_rate) - (avg_loss * loss_rate)

        # Sharpe Ratio (annualized from per-trade returns)
        if len(pnls) > 1:
            mean_return = np.mean(pnls)
            std_return = np.std(pnls, ddof=1)
            if std_return > 0:
                # Annualize: multiply by sqrt(trades_per_year_estimate)
                # For H1 strategies: ~5 trades per 30-day window
                # Conservative: use sqrt(N) where N = actual trade count
                sharpe = (mean_return / std_return) * np.sqrt(
                    min(len(pnls), self.TRADING_DAYS_PER_YEAR)
                )
            else:
                sharpe = 0
        else:
            sharpe = 0

        # Max Drawdown (from cumulative PnL curve)
        cumulative = np.cumsum(pnls)
        peak = np.maximum.accumulate(cumulative)
        drawdown = peak - cumulative
        max_dd = np.max(drawdown) / peak[np.argmax(drawdown)] if peak[np.argmax(drawdown)] > 0 else 0
        # Alternative: absolute drawdown in pips
        max_dd_pips = np.max(drawdown) if len(drawdown) > 0 else 0

        return BacktestMetrics(
            win_rate=Decimal(str(round(win_rate, 4))),
            profit_factor=Decimal(str(round(profit_factor, 4))),
            sharpe_ratio=Decimal(str(round(sharpe, 4))),
            max_drawdown=Decimal(str(round(max_dd, 4))),
            expectancy=Decimal(str(round(expectancy, 4))),
            total_trades=len(pnls),
        )
```

### Database Persistence for Backtest Results

```python
# Source: Existing BacktestResult model in app/models/backtest_result.py
# The model already exists with all required fields:
#   strategy_id, timeframe, window_days, start_date, end_date,
#   win_rate, profit_factor, sharpe_ratio, max_drawdown, expectancy,
#   total_trades, created_at

async def persist_backtest_result(
    session: AsyncSession,
    strategy_id: int,
    timeframe: str,
    window_days: int,
    start_date: datetime,
    end_date: datetime,
    metrics: BacktestMetrics,
) -> BacktestResult:
    """Persist backtest result to database."""
    result = BacktestResult(
        strategy_id=strategy_id,
        timeframe=timeframe,
        window_days=window_days,
        start_date=start_date,
        end_date=end_date,
        win_rate=metrics.win_rate,
        profit_factor=metrics.profit_factor,
        sharpe_ratio=metrics.sharpe_ratio,
        max_drawdown=metrics.max_drawdown,
        expectancy=metrics.expectancy,
        total_trades=metrics.total_trades,
    )
    session.add(result)
    await session.commit()
    return result
```

### Scheduling Daily Backtests with APScheduler

```python
# Source: Existing pattern from app/workers/scheduler.py
from apscheduler.triggers.cron import CronTrigger

def register_jobs() -> None:
    # ... existing candle refresh jobs ...

    # Daily backtests at 02:00 UTC (after all candle refreshes complete)
    scheduler.add_job(
        run_daily_backtests,
        trigger=CronTrigger(hour=2, minute=0, timezone="UTC"),
        id="run_daily_backtests",
        name="Run daily strategy backtests",
        replace_existing=True,
    )
    logger.info("Registered job: run_daily_backtests (daily at 02:00 UTC)")
```

### Querying Candle Windows from Database

```python
# Source: SQLAlchemy async pattern from existing codebase
from sqlalchemy import select
from app.models.candle import Candle

async def get_candle_window(
    session: AsyncSession,
    symbol: str,
    timeframe: str,
    start: datetime,
    end: datetime,
) -> pd.DataFrame:
    """Query candles for a date range and convert to DataFrame."""
    stmt = (
        select(Candle)
        .where(Candle.symbol == symbol)
        .where(Candle.timeframe == timeframe)
        .where(Candle.timestamp.between(start, end))
        .order_by(Candle.timestamp.asc())
    )
    result = await session.execute(stmt)
    candles = result.scalars().all()
    return candles_to_dataframe(candles)
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| vectorbt + numba for everything | Hybrid: manual simulation + vectorbt metrics (or pure manual) | 2024-2025 | vectorbt's free version (0.28.x) is maintenance-mode; community using it for metrics but building custom simulators for complex trade logic |
| Fixed spread backtesting | Session-aware variable spread | 2023-2024 | Gold market microstructure research shows spread varies 2-5x across sessions; fixed spread backtests are unreliable |
| Single train/test split | Walk-forward rolling validation | 2020+ | Single split is statistically weak; rolling walk-forward is now standard for detecting overfitting |
| Float for all financial data | Decimal for DB storage, float for computation | 2020+ | Industry standard; prevents precision loss accumulation in stored results while allowing fast computation |

**Deprecated/outdated:**
- **vectorbt.pro only features:** Walk-forward optimization is better documented in vectorbt.pro (paid), but the free 0.28.x version lacks deep walk-forward API. Manual implementation is needed regardless.
- **APScheduler 4.x:** Full rewrite, still in alpha/beta. Stay on 3.x as already decided in Phase 1.

## Open Questions

1. **vectorbt dependency: include or skip?**
   - What we know: vectorbt 0.28.4 is on PyPI, supports Python 3.12, provides verified metric calculations. But it adds ~100MB of dependencies (numba). Our project may not need it because all 5 required metrics have simple, well-known formulas.
   - What's unclear: Whether the project's deployment target (Railway) has constraints on package size that make numba problematic.
   - Recommendation: **Start without vectorbt.** Implement metrics manually (formulas are in Code Examples above). Add vectorbt only if metric calculation complexity grows in later phases. This keeps the dependency footprint small and avoids the numba compilation overhead.

2. **BacktestResult schema: additional fields needed?**
   - What we know: The existing `backtest_results` table has the 5 required metric columns plus strategy_id, timeframe, window_days, start_date, end_date, total_trades, created_at.
   - What's unclear: Whether walk-forward validation results (IS metrics, OOS metrics, WFE ratio, overfitting flag) should be stored in the same table or a new table. Also whether spread model parameters should be persisted.
   - Recommendation: Add columns to `backtest_results` via Alembic migration: `is_walk_forward` (boolean), `is_overfitted` (boolean), `walk_forward_efficiency` (Numeric(10,4)), `spread_model` (String). Alternatively, create a separate `walk_forward_results` table if the schema gets too wide.

3. **Sharpe ratio annualization for trade-based (non-daily) returns**
   - What we know: Standard Sharpe calculation uses daily returns multiplied by sqrt(252). Our strategies produce discrete trades, not daily return series.
   - What's unclear: The correct annualization factor for per-trade Sharpe when trades have variable holding periods.
   - Recommendation: Convert per-trade PnL into a daily return series by distributing trade PnL across the holding period, then use standard sqrt(252) annualization. Alternative: use per-trade Sharpe with sqrt(estimated_trades_per_year). Document the chosen approach.

4. **XAUUSD pip definition**
   - What we know: In gold trading, "pip" definition varies by convention. Most retail brokers define 1 pip = $0.10 (1 point in the price), but some use $0.01.
   - What's unclear: Which pip convention the project should use.
   - Recommendation: Use $0.10 as 1 pip (standard retail convention for XAUUSD). Store `pnl_pips` using this convention. Document this in the spread model.

5. **How many candle days are currently in the database?**
   - What we know: Phase 1 set up candle ingestion, but we don't know how much historical data has been accumulated.
   - What's unclear: Whether there are 30+ days of H1 candles available for a meaningful backtest.
   - Recommendation: The backtest runner should check available data before running and log a warning if fewer than `window_days` of candles exist. Early runs may need to use smaller windows or skip backtesting until sufficient data accumulates.

## Sources

### Primary (HIGH confidence)
- Project codebase: `app/strategies/base.py` -- BaseStrategy.analyze() interface and CandidateSignal model (verified by reading)
- Project codebase: `app/models/backtest_result.py` -- BacktestResult schema with Numeric(10,4) metric columns (verified by reading)
- Project codebase: `app/workers/scheduler.py` -- APScheduler CronTrigger pattern for background jobs (verified by reading)
- Project codebase: `app/strategies/helpers/session_filter.py` -- Session time definitions and helpers (verified by reading)
- [PyPI: vectorbt 0.28.4](https://pypi.org/project/vectorbt/) -- Latest version, Python 3.10-3.13 support, released 2026-01-26
- [vectorbt Portfolio API docs](https://vectorbt.dev/api/portfolio/base/) -- from_signals, from_orders, stats() methods
- [APScheduler 3.x User Guide](https://apscheduler.readthedocs.io/en/3.x/userguide.html) -- CronTrigger, coalesce, misfire_grace_time

### Secondary (MEDIUM confidence)
- [PyQuant News: Walk Forward Analysis](https://www.pyquantnews.com/free-python-resources/the-future-of-backtesting-a-deep-dive-into-walk-forward-analysis) -- Walk-forward methodology overview
- [QuantStart: Sharpe Ratio](https://www.quantstart.com/articles/Sharpe-Ratio-for-Algorithmic-Trading-Performance-Measurement/) -- Annualized Sharpe formula with sqrt(252)
- [BacktestBase: Profit Factor](https://www.backtestbase.com/education/win-rate-vs-profit-factor) -- Profit factor = gross_profit / gross_loss
- [vectorbt GitHub Discussion #230](https://github.com/polakowo/vectorbt/discussions/230) -- Confirmed vectorbt does NOT support multiple TP targets per entry
- [LiquidityFinder: Gold Backtesting Guide](https://liquidityfinder.com/news/the-ultimate-guide-to-backtesting-and-trading-gold-xau-usd-using-smart-money-concepts-smc-c33b2) -- Session-specific spread modeling for gold
- [ACY: Best Time to Trade Gold](https://acy.com/en/market-news/education/best-time-trade-gold-xauusd-sessions-news-091755/) -- Session spread characteristics

### Tertiary (LOW confidence)
- [Medium: Backtesting with VectorBT](https://medium.com/@trading.dude/backtesting-with-vectorbt-a-beginners-guide-8b9c0e6a0167) -- General vectorbt usage patterns
- [MQL5 Forum: XAUUSD Spread](https://www.mql5.com/en/forum/295511) -- Community discussion on gold spread values (broker-dependent)
- Gold session spread values (Asian: ~5 pips, London/NY: ~3 pips, Overlap: ~2 pips) -- Derived from multiple sources but actual values are broker-specific; these are conservative estimates

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH -- All core libraries already in project; vectorbt verified on PyPI; metric formulas are textbook
- Architecture: HIGH -- Trade simulation pattern is straightforward; rolling window pattern is well-established; project codebase architecture fully understood from reading existing code
- Pitfalls: HIGH -- Common backtesting pitfalls (look-ahead bias, SL/TP priority, Decimal/float mixing) are well-documented in quantitative finance literature and verified against project's specific architecture
- Spread model: MEDIUM -- Session spread values are estimates from multiple sources but actual broker spreads vary; the model is conservative

**Research date:** 2026-02-17
**Valid until:** 2026-03-17 (30 days -- stable domain, no fast-moving API changes expected)
