# Phase 2: Strategy Engine - Research

**Researched:** 2026-02-17
**Domain:** Python strategy pattern, technical analysis indicators, XAUUSD rule-based trading, registry/plugin architecture
**Confidence:** HIGH

## Summary

Phase 2 builds three rule-based trading strategies (Liquidity Sweep Reversal, Trend Continuation, Breakout Expansion) that consume XAUUSD candle data from Phase 1's PostgreSQL store and produce standardized `CandidateSignal` outputs. The strategies are pure analysis functions -- they take candle data in and return signal structures out, with no database writes (that happens downstream in Phase 4).

The standard approach is: define an abstract `BaseStrategy` class using Python's `abc.ABC` with `__init_subclass__` for automatic registry, use `pandas-ta` for technical indicator calculations (EMA, ATR, VWAP), implement swing high/low detection using `scipy.signal.argrelextrema`, and define `CandidateSignal` as a Pydantic `BaseModel` for validation. Each strategy is a single file that implements `analyze(candles) -> list[CandidateSignal]`, declares its required timeframes and minimum history, and registers itself automatically on import.

**Primary recommendation:** Use `abc.ABC` with `__init_subclass__` for zero-boilerplate registry; use `pandas-ta` (not hand-rolled) for all standard indicators; keep strategies as pure functions operating on DataFrames; define `CandidateSignal` as a Pydantic model matching the existing `Signal` DB model fields; convert Decimal OHLCV to float only at the pandas boundary and back to Decimal for output.

## Standard Stack

### Core

| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| pandas-ta | `>=0.3.59` (pandas-ta-classic) | EMA, ATR, VWAP, RSI, and 150+ indicators | Industry standard for Python TA; numba-accelerated; avoids hand-rolling indicator math |
| pandas | `>=2.0` | DataFrame operations for candle data manipulation | Required by pandas-ta; vectorized operations for efficient candle analysis |
| numpy | `>=1.26` | Numerical operations | Dependency of pandas and pandas-ta; needed for array operations |
| scipy | `>=1.12` | `argrelextrema` for swing high/low pivot detection | Standard for signal processing / peak detection in time series |
| pydantic | `>=2.10.0` (already installed) | CandidateSignal schema validation | Already in stack from Phase 1; validates signal outputs |

### Supporting

| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| smartmoneyconcepts | `>=0.0.26` | BOS/CHoCH detection, liquidity zones, swing highs/lows | Reference implementation for ICT concepts; evaluate during implementation -- may hand-roll subset instead to avoid dependency on small package |

### Alternatives Considered

| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| pandas-ta-classic | pandas-ta 0.4.71b0 | 0.4.x is pre-release (beta), requires Python >=3.12 and numba; classic is stable, production-ready, Python 3.9-3.13 |
| pandas-ta-classic | TA-Lib (C wrapper) | TA-Lib is faster but requires C library installation (brew install ta-lib); pandas-ta-classic is pure Python, easier to deploy |
| pandas-ta-classic | Hand-rolled indicators | Hand-rolling EMA/ATR/VWAP is error-prone; off-by-one in EMA warmup, incorrect ATR smoothing are common bugs |
| smartmoneyconcepts | Hand-rolled BOS/liquidity | smartmoneyconcepts is small (0.0.26), limited maintenance history; hand-rolling gives full control over detection sensitivity |
| scipy argrelextrema | Custom pivot detection | argrelextrema is battle-tested for peak/trough detection; custom code risks lookahead bias |

**Installation:**
```bash
pip install "pandas-ta-classic>=0.3.59" "pandas>=2.0" "numpy>=1.26" "scipy>=1.12"
```

**Note:** `pydantic` is already installed from Phase 1. `smartmoneyconcepts` is optional -- evaluate whether to use or hand-roll during implementation.

## Architecture Patterns

### Recommended Project Structure

```
app/
├── strategies/
│   ├── __init__.py              # Imports all strategies to trigger registration
│   ├── base.py                  # BaseStrategy ABC + CandidateSignal schema + registry
│   ├── registry.py              # StrategyRegistry class (or in base.py)
│   ├── liquidity_sweep.py       # STRAT-01: Liquidity Sweep Reversal
│   ├── trend_continuation.py    # STRAT-02: Trend Continuation
│   ├── breakout_expansion.py    # STRAT-03: Breakout Expansion
│   └── helpers/
│       ├── __init__.py
│       ├── indicators.py        # Thin wrappers around pandas-ta for project conventions
│       ├── swing_detection.py   # Swing high/low detection using scipy
│       ├── session_filter.py    # London/NY/Asian session time filters
│       └── market_structure.py  # BOS/CHoCH/structure shift detection
├── schemas/
│   └── signal.py                # CandidateSignal Pydantic model (or in strategies/base.py)
```

### Pattern 1: BaseStrategy ABC with `__init_subclass__` Registry

**What:** Abstract base class that auto-registers all subclasses into a dict registry on definition.
**When to use:** When you need zero-boilerplate plugin discovery (STRAT-07 requirement).

```python
# app/strategies/base.py
from abc import ABC, abstractmethod
from decimal import Decimal
from datetime import datetime
from enum import Enum
from typing import Optional
import pandas as pd
from pydantic import BaseModel, Field


class Direction(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class CandidateSignal(BaseModel):
    """Standardized output from any strategy's analyze() method."""
    strategy_name: str
    symbol: str = "XAUUSD"
    timeframe: str
    direction: Direction
    entry_price: Decimal
    stop_loss: Decimal
    take_profit_1: Decimal
    take_profit_2: Decimal
    risk_reward: Decimal
    confidence: Decimal = Field(ge=Decimal("0"), le=Decimal("100"))
    reasoning: str
    timestamp: datetime  # candle timestamp that triggered the signal


class BaseStrategy(ABC):
    """Abstract base for all trading strategies.

    Subclasses auto-register via __init_subclass__.
    Adding a new strategy = one file + import in __init__.py.
    """
    _registry: dict[str, type["BaseStrategy"]] = {}

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        # Only register concrete classes (not intermediate ABCs)
        if not getattr(cls, "__abstractmethods__", None):
            BaseStrategy._registry[cls.name] = cls

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique strategy identifier."""
        ...

    @property
    @abstractmethod
    def required_timeframes(self) -> list[str]:
        """Timeframes this strategy needs (e.g. ['H1', 'M15'])."""
        ...

    @property
    @abstractmethod
    def min_candles(self) -> int:
        """Minimum candle history required for analysis."""
        ...

    @abstractmethod
    def analyze(self, candles: pd.DataFrame) -> list[CandidateSignal]:
        """Run strategy logic on candle data, return candidate signals.

        Args:
            candles: DataFrame with columns [timestamp, open, high, low, close, volume]
                     sorted ascending by timestamp.

        Returns:
            List of CandidateSignal (may be empty if no setups found).

        Raises:
            InsufficientDataError: If len(candles) < self.min_candles.
        """
        ...

    def validate_data(self, candles: pd.DataFrame) -> None:
        """Check candle data meets strategy requirements. Call at start of analyze()."""
        if len(candles) < self.min_candles:
            raise InsufficientDataError(
                f"{self.name} requires {self.min_candles} candles, got {len(candles)}"
            )
        required_cols = {"timestamp", "open", "high", "low", "close"}
        missing = required_cols - set(candles.columns)
        if missing:
            raise ValueError(f"Missing columns: {missing}")

    @classmethod
    def get_registry(cls) -> dict[str, type["BaseStrategy"]]:
        return dict(cls._registry)

    @classmethod
    def get_strategy(cls, name: str) -> "BaseStrategy":
        if name not in cls._registry:
            raise KeyError(f"Strategy '{name}' not registered. Available: {list(cls._registry.keys())}")
        return cls._registry[name]()


class InsufficientDataError(Exception):
    """Raised when strategy does not have enough candle history."""
    pass
```

### Pattern 2: Strategy Implementation (Concrete Example)

**What:** How each strategy file looks.
**When to use:** Every strategy follows this template.

```python
# app/strategies/trend_continuation.py
import pandas as pd
import pandas_ta as ta

from app.strategies.base import BaseStrategy, CandidateSignal, Direction, InsufficientDataError


class TrendContinuationStrategy(BaseStrategy):
    """STRAT-02: EMA/VWAP trend filter with pullback entry on momentum confirmation."""

    name = "trend_continuation"
    required_timeframes = ["H1"]
    min_candles = 200  # Need 200 candles for EMA-200 warmup

    def analyze(self, candles: pd.DataFrame) -> list[CandidateSignal]:
        self.validate_data(candles)
        signals = []

        # Calculate indicators using pandas-ta
        candles = candles.copy()
        candles["ema_50"] = ta.ema(candles["close"], length=50)
        candles["ema_200"] = ta.ema(candles["close"], length=200)
        candles["atr"] = ta.atr(candles["high"], candles["low"], candles["close"], length=14)

        # ... strategy logic producing CandidateSignal instances ...

        return signals
```

### Pattern 3: Registry Discovery via `__init__.py` Imports

**What:** Importing strategy modules triggers `__init_subclass__` registration.
**When to use:** Always -- this is what makes STRAT-07 work.

```python
# app/strategies/__init__.py
"""Import all strategies to trigger auto-registration."""
from app.strategies.base import BaseStrategy, CandidateSignal, InsufficientDataError
from app.strategies.liquidity_sweep import LiquiditySweepStrategy
from app.strategies.trend_continuation import TrendContinuationStrategy
from app.strategies.breakout_expansion import BreakoutExpansionStrategy

__all__ = [
    "BaseStrategy",
    "CandidateSignal",
    "InsufficientDataError",
    "LiquiditySweepStrategy",
    "TrendContinuationStrategy",
    "BreakoutExpansionStrategy",
]
```

### Pattern 4: Decimal/Float Boundary Management

**What:** Convert Decimal (from DB) to float (for pandas-ta) and back to Decimal (for output).
**When to use:** At the boundary between DB queries and strategy analysis.

```python
# Converting Candle ORM objects to a pandas DataFrame for strategy consumption
def candles_to_dataframe(candles: list) -> pd.DataFrame:
    """Convert list of Candle ORM objects to a float-based DataFrame for pandas-ta."""
    records = [
        {
            "timestamp": c.timestamp,
            "open": float(c.open),
            "high": float(c.high),
            "low": float(c.low),
            "close": float(c.close),
            "volume": float(c.volume) if c.volume else 0.0,
        }
        for c in candles
    ]
    df = pd.DataFrame(records)
    df = df.sort_values("timestamp").reset_index(drop=True)
    return df

# Converting output prices back to Decimal
# In CandidateSignal, entry_price/stop_loss/take_profit are Decimal fields.
# Strategy creates them with: Decimal(str(round(float_price, 2)))
```

### Anti-Patterns to Avoid

- **Strategies that write to the database:** Strategies are pure analysis functions. They return `CandidateSignal` lists. Persistence happens in Phase 4 (Signal Pipeline).
- **Strategies that import database sessions:** No SQLAlchemy imports in strategy files. Candle data arrives as a DataFrame.
- **Storing indicator state between calls:** Each `analyze()` call is stateless. Recalculate indicators from the full candle history passed in.
- **Using float for price fields in CandidateSignal:** Entry/SL/TP prices must be `Decimal` to match the existing `Signal` DB model (Numeric(10,2)).
- **Tight coupling to specific timeframes in the runner:** The caller should read `strategy.required_timeframes` and provide the right data, not hardcode timeframes.
- **Forgetting EMA warmup period:** EMA-200 needs 200+ candles to stabilize. Strategy must declare `min_candles` accordingly and validate.

## Don't Hand-Roll

Problems that look simple but have existing solutions:

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| EMA calculation | Custom exponential smoothing | `pandas_ta.ema()` | Off-by-one warmup errors; incorrect smoothing factor is the #1 bug in custom EMA code |
| ATR (Average True Range) | Manual true range + smoothing | `pandas_ta.atr()` | ATR requires RMA (Wiley smoothing) by default, not SMA; pandas-ta matches TradingView's calculation |
| VWAP | Manual cumulative volume-price sum | `pandas_ta.vwap()` | VWAP resets daily and needs DatetimeIndex; pandas-ta handles this correctly |
| RSI | Custom relative strength calc | `pandas_ta.rsi()` | RSI uses Wilder's smoothing (RMA), not standard EMA; subtle but critical difference |
| Swing high/low detection | Custom loop with lookback | `scipy.signal.argrelextrema()` | Lookahead bias is the #1 error in custom pivot code; argrelextrema uses `order` parameter correctly |
| Risk/reward calculation | Manual pip distance math | Utility function with `Decimal` | Gold pip = $0.01; R:R = |entry - TP| / |entry - SL|; must use Decimal for accuracy |

**Key insight:** Technical indicator math has subtle edge cases (warmup periods, smoothing methods, reset anchors) that are well-solved by pandas-ta. Hand-rolling indicators is the fastest path to incorrect signals and untraceable bugs.

## Common Pitfalls

### Pitfall 1: Lookahead Bias in Signal Generation

**What goes wrong:** Strategy "sees" future candle data when generating signals, producing unrealistically good results.
**Why it happens:** Using the current candle's close to decide an entry that should only be known after the candle closes. Or using scipy peak detection which needs N future bars to confirm.
**How to avoid:** Signals must be generated using only data up to and including the signal bar. Use `argrelextrema` with explicit `order` parameter and offset results by `order` bars into the past. Entry decisions use the *previous* closed candle, not the current forming one.
**Warning signs:** Strategy produces signals on every bar; backtests show implausibly high win rates (>75%).

### Pitfall 2: Decimal/Float Precision Boundary Errors

**What goes wrong:** Prices lose precision when converting between Decimal (DB) and float (pandas) and back.
**Why it happens:** `float(Decimal("2645.50"))` works fine, but `Decimal(2645.5000000000001)` introduces garbage digits.
**How to avoid:** Always convert float back to Decimal via string: `Decimal(str(round(price, 2)))`. Never `Decimal(float_value)` directly.
**Warning signs:** Entry prices with 15+ decimal places; stop-loss levels that don't match expected values.

### Pitfall 3: EMA Warmup Period Ignored

**What goes wrong:** Strategy produces signals during the first N candles where EMA hasn't stabilized, generating garbage entries.
**Why it happens:** EMA-200 needs ~200 candles to converge. Using it after 50 candles gives meaningless values.
**How to avoid:** Each strategy declares `min_candles` that accounts for its longest indicator. `validate_data()` enforces this. Additionally, skip signal generation for the first `min_candles` rows even within a valid dataset.
**Warning signs:** Signals concentrated at the start of the dataset; wildly different EMA values compared to TradingView charts.

### Pitfall 4: VWAP Without DatetimeIndex

**What goes wrong:** pandas-ta's VWAP returns NaN or incorrect values.
**Why it happens:** VWAP calculation requires a DatetimeIndex on the DataFrame to determine session boundaries for daily reset.
**How to avoid:** Set `df.index = pd.DatetimeIndex(df["timestamp"])` before calling `ta.vwap()`. Or pass the anchor parameter explicitly.
**Warning signs:** VWAP column is all NaN; VWAP value grows unboundedly instead of tracking price.

### Pitfall 5: Strategy Registry Not Populated

**What goes wrong:** `BaseStrategy.get_registry()` returns empty dict.
**Why it happens:** Strategy modules were never imported, so `__init_subclass__` never fired.
**How to avoid:** The `app/strategies/__init__.py` must explicitly import every strategy module. Verify with a test that checks registry length matches expected count.
**Warning signs:** No strategies available when running analysis; registry dict is empty.

### Pitfall 6: Session Filter Timezone Confusion

**What goes wrong:** Strategies filter for London session but get Asian session candles, or miss valid trading windows.
**Why it happens:** Candle timestamps are UTC (from Phase 1), but session times are often documented in local time (GMT, EST). GMT != UTC during DST transitions.
**How to avoid:** Define all session windows in UTC. London session: 07:00-16:00 UTC (no DST adjustment for now -- forex uses fixed GMT reference). New York: 12:00-21:00 UTC. Asian: 23:00-08:00 UTC. Use `candle.timestamp.hour` for filtering after confirming timezone.
**Warning signs:** Different signal counts in summer vs winter; signals appearing outside expected market hours.

### Pitfall 7: Modifying Input DataFrame In-Place

**What goes wrong:** Strategy adds indicator columns to the original DataFrame, causing side effects for other strategies or subsequent calls.
**Why it happens:** pandas operations modify DataFrames in place by default.
**How to avoid:** Always call `candles = candles.copy()` at the start of `analyze()` before adding indicator columns.
**Warning signs:** Running the same strategy twice on the same data produces different results; columns unexpectedly present from a previous strategy's run.

## Code Examples

### Example 1: CandidateSignal Pydantic Model

```python
# Source: Aligned with existing Signal ORM model fields (app/models/signal.py)
from decimal import Decimal
from datetime import datetime
from enum import Enum
from pydantic import BaseModel, Field

class Direction(str, Enum):
    BUY = "BUY"
    SELL = "SELL"

class CandidateSignal(BaseModel):
    """Output from strategy.analyze(). Maps to Signal DB model in Phase 4."""
    strategy_name: str
    symbol: str = "XAUUSD"
    timeframe: str                          # "M15", "H1", "H4", "D1"
    direction: Direction
    entry_price: Decimal = Field(decimal_places=2)
    stop_loss: Decimal = Field(decimal_places=2)
    take_profit_1: Decimal = Field(decimal_places=2)
    take_profit_2: Decimal = Field(decimal_places=2)
    risk_reward: Decimal = Field(decimal_places=2)
    confidence: Decimal = Field(ge=Decimal("0"), le=Decimal("100"), decimal_places=2)
    reasoning: str
    timestamp: datetime
    invalidation_price: Decimal | None = Field(default=None, decimal_places=2)
    session: str | None = None              # "london", "new_york", "asian"
```

### Example 2: Liquidity Sweep Detection Logic Skeleton

```python
# Source: Derived from ICT Smart Money Concepts literature + scipy peak detection
import numpy as np
import pandas as pd
from scipy.signal import argrelextrema

def detect_swing_highs(highs: pd.Series, order: int = 5) -> np.ndarray:
    """Find indices of swing highs (local maxima) in price data.

    Args:
        highs: Series of high prices.
        order: Number of bars on each side to confirm swing. Higher = fewer, stronger swings.

    Returns:
        Array of indices where swing highs occur.
    """
    indices = argrelextrema(highs.values, np.greater_equal, order=order)[0]
    return indices

def detect_swing_lows(lows: pd.Series, order: int = 5) -> np.ndarray:
    """Find indices of swing lows (local minima) in price data."""
    indices = argrelextrema(lows.values, np.less_equal, order=order)[0]
    return indices

def detect_liquidity_sweep(
    candles: pd.DataFrame,
    swing_lows: np.ndarray,
    lookback: int = 3,
) -> list[dict]:
    """Detect candles that wick below swing lows (stop hunt) then close above.

    A liquidity sweep occurs when:
    1. Price dips below a previous swing low (takes out stops)
    2. Price closes back above the swing low level (rejection)
    3. This suggests institutional accumulation
    """
    sweeps = []
    for i in range(len(candles)):
        if i < lookback:
            continue
        candle = candles.iloc[i]
        # Check if this candle wicked below any recent swing low
        for sl_idx in swing_lows:
            if sl_idx >= i:  # Don't look ahead
                continue
            swing_low_price = candles.iloc[sl_idx]["low"]
            # Sweep condition: wick below, close above
            if candle["low"] < swing_low_price and candle["close"] > swing_low_price:
                sweeps.append({
                    "bar_index": i,
                    "sweep_level": swing_low_price,
                    "candle_low": candle["low"],
                    "candle_close": candle["close"],
                })
    return sweeps
```

### Example 3: Session Time Filter

```python
# Source: Standard forex session times (UTC-based, no DST adjustment)
from datetime import datetime

# Forex session windows in UTC hours
SESSIONS = {
    "asian":   (23, 8),   # 23:00 - 08:00 UTC (wraps midnight)
    "london":  (7, 16),   # 07:00 - 16:00 UTC
    "new_york": (12, 21), # 12:00 - 21:00 UTC
    "overlap": (12, 16),  # London + NY overlap (highest volume)
}

def get_active_sessions(timestamp: datetime) -> list[str]:
    """Return list of active forex sessions for a given UTC timestamp."""
    hour = timestamp.hour
    active = []
    for session_name, (start, end) in SESSIONS.items():
        if start <= end:
            if start <= hour < end:
                active.append(session_name)
        else:  # Wraps midnight (asian session)
            if hour >= start or hour < end:
                active.append(session_name)
    return active

def is_in_session(timestamp: datetime, session: str) -> bool:
    """Check if a timestamp falls within a specific trading session."""
    if session not in SESSIONS:
        raise ValueError(f"Unknown session: {session}. Valid: {list(SESSIONS.keys())}")
    start, end = SESSIONS[session]
    hour = timestamp.hour
    if start <= end:
        return start <= hour < end
    return hour >= start or hour < end
```

### Example 4: Consolidation Range Detection (Breakout Strategy)

```python
# Source: Standard ATR-based consolidation detection
import pandas as pd
import pandas_ta as ta

def detect_consolidation_range(
    candles: pd.DataFrame,
    atr_length: int = 14,
    range_threshold: float = 0.5,
    min_bars: int = 10,
) -> list[dict]:
    """Detect periods where price is range-bound (low ATR relative to recent history).

    A consolidation range forms when:
    1. ATR contracts below threshold * recent_avg_atr
    2. This persists for min_bars or more candles
    3. High-low range stays within a narrow band

    Returns list of dicts with range_high, range_low, start_idx, end_idx.
    """
    df = candles.copy()
    df["atr"] = ta.atr(df["high"], df["low"], df["close"], length=atr_length)
    df["atr_ma"] = df["atr"].rolling(window=50).mean()

    # ATR compression = current ATR is notably below average
    df["compressed"] = df["atr"] < (df["atr_ma"] * range_threshold)

    ranges = []
    in_range = False
    start_idx = 0

    for i in range(len(df)):
        if df.iloc[i]["compressed"] and not in_range:
            in_range = True
            start_idx = i
        elif not df.iloc[i]["compressed"] and in_range:
            if i - start_idx >= min_bars:
                range_slice = df.iloc[start_idx:i]
                ranges.append({
                    "start_idx": start_idx,
                    "end_idx": i - 1,
                    "range_high": range_slice["high"].max(),
                    "range_low": range_slice["low"].min(),
                    "duration_bars": i - start_idx,
                })
            in_range = False

    return ranges
```

### Example 5: Testing Strategy Without Database

```python
# Source: Standard pytest pattern for pure-function strategy testing
import pytest
import pandas as pd
from decimal import Decimal
from datetime import datetime, timezone, timedelta

from app.strategies.trend_continuation import TrendContinuationStrategy
from app.strategies.base import CandidateSignal, InsufficientDataError


def make_candles(count: int, base_price: float = 2650.0) -> pd.DataFrame:
    """Generate synthetic candle data for testing."""
    records = []
    price = base_price
    for i in range(count):
        price += (i % 7 - 3) * 0.5  # oscillating price
        records.append({
            "timestamp": datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(hours=i),
            "open": price,
            "high": price + 2.0,
            "low": price - 1.5,
            "close": price + 0.5,
            "volume": 1000.0 + i * 10,
        })
    return pd.DataFrame(records)


class TestTrendContinuation:
    def test_insufficient_data_raises(self):
        strategy = TrendContinuationStrategy()
        candles = make_candles(50)  # Less than min_candles
        with pytest.raises(InsufficientDataError):
            strategy.analyze(candles)

    def test_analyze_returns_candidate_signals(self):
        strategy = TrendContinuationStrategy()
        candles = make_candles(300)  # Enough for EMA-200
        signals = strategy.analyze(candles)
        assert isinstance(signals, list)
        for sig in signals:
            assert isinstance(sig, CandidateSignal)
            assert sig.strategy_name == "trend_continuation"
            assert sig.direction in ("BUY", "SELL")
            assert sig.risk_reward > 0

    def test_registry_contains_strategy(self):
        from app.strategies.base import BaseStrategy
        registry = BaseStrategy.get_registry()
        assert "trend_continuation" in registry
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Metaclass-based registry | `__init_subclass__` registry | Python 3.6+ (2017) | Simpler, no metaclass conflicts, same auto-discovery |
| TA-Lib C wrapper required | pandas-ta pure Python | pandas-ta 0.3.x (2023+) | No C compilation needed; easier deployment; same indicator accuracy |
| SMA-based ATR | RMA/Wilder smoothing ATR (default in pandas-ta) | pandas-ta alignment with TradingView | Matches what traders see on TradingView charts |
| Manual swing detection loops | `scipy.signal.argrelextrema` | Long established | Eliminates lookahead bias with `order` parameter |
| Custom Pydantic v1 models | Pydantic v2 `BaseModel` | Pydantic 2.0 (2023) | 5-50x faster validation; `model_validate()` instead of `from_orm()` |

**Deprecated/outdated:**
- TA-Lib Python wrapper still works but requires system-level C library installation. Use pandas-ta-classic instead for pure Python.
- Pydantic v1 `validator` decorators are replaced by v2 `field_validator` / `model_validator`.

## Strategy-Specific Research

### STRAT-01: Liquidity Sweep Reversal

**Concept:** Detects stop hunts below/above key levels, waits for market structure shift (BOS/CHoCH), enters on confirmation.

**Required indicators/detection:**
- Swing high/low detection (`scipy.signal.argrelextrema`, order=5-10 for H1)
- Liquidity sweep detection (wick beyond swing, close inside)
- Market structure shift (break of previous swing high/low after sweep)
- Rejection candle patterns (pin bar, engulfing)

**Entry logic:**
1. Identify swing lows/highs as liquidity pools
2. Detect when price wicks below a swing low (bullish sweep) or above a swing high (bearish sweep)
3. Wait for price to close back inside the range (rejection)
4. Confirm market structure shift (higher high for bullish, lower low for bearish)
5. Enter on next candle open after confirmation

**Stop placement:** Below the sweep wick (bullish) or above the sweep wick (bearish) + small buffer (0.5 * ATR)
**TP logic:** TP1 at 1.5:1 R:R, TP2 at 3:1 R:R from entry
**Session filter:** London and New York sessions (highest liquidity for valid sweeps)
**Timeframe:** Primary H1, can use M15 for entry refinement
**Min candles:** ~100 (need enough history for swing detection)

### STRAT-02: Trend Continuation

**Concept:** EMA/VWAP trend filter, pullback in established trend, enter on momentum confirmation.

**Required indicators:**
- EMA-50, EMA-200 for trend direction
- VWAP for intraday value reference
- ATR-14 for stop placement and volatility filter
- Optional: RSI for pullback depth

**Entry logic:**
1. Confirm trend: EMA-50 > EMA-200 (bullish) or EMA-50 < EMA-200 (bearish)
2. Price above VWAP for bullish, below for bearish
3. Wait for pullback: price retraces to EMA-50 zone (within 1 ATR of EMA-50)
4. Confirm momentum resumption: candle closes back in trend direction
5. Enter on next candle open

**Stop placement:** Below the pullback low (bullish) or above pullback high (bearish), minimum 1.5 * ATR
**TP logic:** TP1 at 2:1 R:R, TP2 at previous swing high/low
**Session filter:** London and New York sessions
**Timeframe:** Primary H1
**Min candles:** 200 (EMA-200 warmup)

### STRAT-03: Breakout Expansion

**Concept:** Detect consolidation ranges, identify volatility expansion, optional retest entry.

**Required indicators:**
- ATR-14 for range compression detection
- ATR-50 rolling average for relative compression
- Bollinger Bands (optional) for squeeze detection
- Volume (if available) for breakout confirmation

**Entry logic:**
1. Detect consolidation: ATR < 0.5 * ATR_MA_50 for 10+ bars
2. Define range: high/low of consolidation zone
3. Breakout: candle closes above range high (bullish) or below range low (bearish)
4. Volume confirmation (optional): breakout bar volume > 1.5 * avg volume
5. Entry: on breakout close, OR wait for retest of range boundary

**Stop placement:** Inside the consolidation range -- opposite side of breakout, or midpoint of range
**TP logic:** TP1 = range height projected from breakout, TP2 = 2x range height
**Session filter:** Any session (breakouts can happen anytime, but prioritize London open)
**Timeframe:** Primary H1, can use H4 for larger ranges
**Min candles:** ~70 (need ATR-50 + consolidation detection window)

## Open Questions

1. **smartmoneyconcepts as dependency vs hand-roll**
   - What we know: `smartmoneyconcepts==0.0.26` (March 2025) provides BOS/CHoCH/swing/liquidity detection. Small package, 7 contributors, MIT licensed.
   - What's unclear: Reliability in production, edge cases, accuracy vs TradingView.
   - Recommendation: Hand-roll the subset of features needed (swing detection, liquidity sweep) using scipy + pandas. This avoids a small-package dependency risk and gives full control over detection sensitivity tuning. Reference `smartmoneyconcepts` source code for logic verification.

2. **VWAP applicability for XAUUSD**
   - What we know: VWAP is meaningful for exchange-traded assets with centralized volume. Forex/gold volume from Twelve Data is indicative (not real exchange volume).
   - What's unclear: Whether VWAP calculated from Twelve Data volume is reliable enough for trading decisions.
   - Recommendation: Include VWAP as a secondary confirming indicator (not primary), with a note that volume data quality varies by provider. If volume is frequently null/zero, fall back to EMA-only trend filter.

3. **Multi-timeframe analysis coordination**
   - What we know: STRAT-01 mentions using M15 for entry refinement on H1 setups. Each strategy declares `required_timeframes`.
   - What's unclear: Whether Phase 2 should implement multi-timeframe data passing or keep it single-timeframe.
   - Recommendation: For Phase 2, keep each strategy single-timeframe (analyze one DataFrame at a time). Multi-timeframe coordination (passing H1 + M15 data to one strategy) can be added later. The `required_timeframes` property documents intent without requiring immediate implementation.

4. **Confidence score calculation**
   - What we know: CandidateSignal has a `confidence` field (Decimal, 0-100). The existing Signal model also has this.
   - What's unclear: How to compute a meaningful confidence score from rule-based criteria.
   - Recommendation: Use a simple additive scoring system per strategy: each confirmation criterion (trend aligned, session active, volume confirmed, etc.) adds points. Document the scoring rubric per strategy. Don't over-engineer -- this is rule-based, not ML.

## Sources

### Primary (HIGH confidence)
- Python `abc` module docs: https://docs.python.org/3/library/abc.html - ABC pattern, abstractmethod usage
- Python `__init_subclass__` docs: https://docs.python.org/3/reference/datamodel.html#object.__init_subclass__ - auto-registration hook
- scipy argrelextrema docs: https://docs.scipy.org/doc/scipy/reference/generated/scipy.signal.argrelextrema.html - peak detection
- pandas-ta-classic PyPI: https://pypi.org/project/pandas-ta-classic/ - v0.3.59, Nov 2025, Python 3.9-3.13, 150+ indicators
- Pydantic v2 docs: https://docs.pydantic.dev/latest/ - BaseModel, field validation
- Existing codebase analysis: app/models/signal.py, app/models/strategy.py, app/models/candle.py

### Secondary (MEDIUM confidence)
- zetcode `__init_subclass__` guide: https://zetcode.com/python/dunder-init_subclass/ - verified registry pattern with code examples
- pandas-ta PyPI (0.4.71b0 pre-release): https://pypi.org/project/pandas-ta/ - confirmed Python >=3.12, numba dependency
- Backtrader XAUUSD pullback strategy: https://github.com/ilahuerta-IA/backtrader-pullback-window-xauusd - 4-phase state machine architecture, ATR risk management
- XS Liquidity Sweep guide: https://www.xs.com/en/blog/liquidity-sweep/ - sweep detection fundamentals
- smartmoneyconcepts GitHub: https://github.com/joshyattridge/smart-money-concepts - BOS/CHoCH/liquidity API reference

### Tertiary (LOW confidence)
- Blog posts on EMA/VWAP trading strategy patterns (multiple Medium articles) - general approach verification
- Forex session times from babypips.com/oanda.com - session window UTC hours (well-established but verify against broker data)

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH - pandas-ta-classic verified on PyPI (v0.3.59, Nov 2025, Python 3.9-3.13); scipy is established; pydantic already in stack
- Architecture: HIGH - `__init_subclass__` registry is documented Python stdlib pattern; strategy/ABC pattern is textbook design patterns
- Pitfalls: HIGH - Decimal/float boundary, EMA warmup, lookahead bias are well-documented issues in financial Python
- Strategy logic: MEDIUM - Based on published trading methodologies (ICT/SMC for liquidity sweeps, standard EMA trend following, ATR breakout); specific parameters (EMA lengths, ATR multipliers, swing order) need tuning during implementation

**Research date:** 2026-02-17
**Valid until:** 2026-03-17 (stable domain; indicator libraries change slowly)
