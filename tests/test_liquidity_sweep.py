"""Unit tests for LiquiditySweepStrategy (STRAT-01).

All tests use synthetic candle data -- no database fixtures required.
"""

import math
from datetime import datetime, timezone, timedelta
from decimal import Decimal

import numpy as np
import pandas as pd
import pytest

from app.strategies.base import (
    BaseStrategy,
    CandidateSignal,
    Direction,
    InsufficientDataError,
)
from app.strategies.liquidity_sweep import LiquiditySweepStrategy


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_candles(
    count: int,
    base_price: float = 2650.0,
    trend: str = "flat",
    start_hour: int = 10,
) -> pd.DataFrame:
    """Generate synthetic OHLCV candles.

    Args:
        count: Number of candles.
        base_price: Starting mid-price.
        trend: "flat", "up", or "down".
        start_hour: UTC hour for the first candle (default 10 = London).

    Returns:
        DataFrame with columns [timestamp, open, high, low, close, volume].
    """
    base_ts = datetime(2026, 1, 1, start_hour, 0, tzinfo=timezone.utc)
    rows = []

    price = base_price
    for i in range(count):
        # Gentle oscillation for flat; drift for trend
        noise = math.sin(i * 0.3) * 2.0 + math.cos(i * 0.7) * 1.5
        if trend == "up":
            drift = i * 0.3
        elif trend == "down":
            drift = -i * 0.3
        else:
            drift = 0.0

        mid = price + drift + noise
        o = mid - 0.5
        c = mid + 0.5
        h = max(o, c) + abs(noise) * 0.3
        l = min(o, c) - abs(noise) * 0.3

        rows.append({
            "timestamp": base_ts + timedelta(hours=i),
            "open": round(o, 2),
            "high": round(h, 2),
            "low": round(l, 2),
            "close": round(c, 2),
            "volume": 1000.0 + i * 10,
        })

    return pd.DataFrame(rows)


def make_sweep_candles(count: int = 150) -> pd.DataFrame:
    """Generate a scenario designed to produce a bullish liquidity sweep signal.

    Structure:
      - Bars 0-79:   gentle uptrend establishing swing structure
      - Bars 80-89:  pullback creating a clear swing low around bar 80
      - Bar 100-104: recovery, prices drift back up
      - Bar 105:     THE SWEEP -- wick below the swing low, close above it
      - Bars 106-108: confirmation candles closing above the sweep candle's high

    All timestamps are in London session (10:00 UTC start, +1h increments).
    """
    base_ts = datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)
    rows: list[dict] = []

    # Phase 1: gentle uptrend (bars 0-79)
    for i in range(80):
        mid = 2650.0 + i * 0.5 + math.sin(i * 0.5) * 2.0
        o = mid - 1.0
        c = mid + 1.0
        h = c + 1.5
        l = o - 1.5
        rows.append({
            "timestamp": base_ts + timedelta(hours=i),
            "open": round(o, 2),
            "high": round(h, 2),
            "low": round(l, 2),
            "close": round(c, 2),
            "volume": 1200.0,
        })

    # Phase 2: pullback creating a swing low (bars 80-89)
    # Descend from last price down to form a clear trough at bar 85
    last_close = rows[-1]["close"]
    for i in range(80, 90):
        offset = i - 80
        # Descend for 5 bars, then partially recover for next 5
        if offset < 5:
            mid = last_close - offset * 3.0
        else:
            mid = last_close - 5 * 3.0 + (offset - 5) * 2.5
        o = mid - 1.0
        c = mid + 1.0
        h = c + 1.0
        l = o - 1.0
        rows.append({
            "timestamp": base_ts + timedelta(hours=i),
            "open": round(o, 2),
            "high": round(h, 2),
            "low": round(l, 2),
            "close": round(c, 2),
            "volume": 1500.0,
        })

    # Record the swing low level (deepest point around bar 84-85)
    swing_low_level = min(r["low"] for r in rows[80:90])

    # Phase 3: recovery back up (bars 90-104)
    last_close = rows[-1]["close"]
    for i in range(90, 105):
        offset = i - 90
        mid = last_close + offset * 1.5
        o = mid - 1.0
        c = mid + 1.0
        h = c + 1.5
        l = o - 1.5
        rows.append({
            "timestamp": base_ts + timedelta(hours=i),
            "open": round(o, 2),
            "high": round(h, 2),
            "low": round(l, 2),
            "close": round(c, 2),
            "volume": 1300.0,
        })

    # Phase 4: THE SWEEP (bar 105)
    # Price wicks below the swing low but closes back above it
    last_close = rows[-1]["close"]
    sweep_open = last_close - 2.0
    sweep_low = swing_low_level - 5.0   # wick well below
    sweep_close = swing_low_level + 4.0  # close above swing low
    sweep_high = sweep_close + 2.0
    rows.append({
        "timestamp": base_ts + timedelta(hours=105),
        "open": round(sweep_open, 2),
        "high": round(sweep_high, 2),
        "low": round(sweep_low, 2),
        "close": round(sweep_close, 2),
        "volume": 3000.0,
    })

    # Phase 5: confirmation candles (bars 106-108)
    # Close above the sweep candle's high to confirm bullish reversal
    for i in range(106, 109):
        offset = i - 106
        mid = sweep_high + 3.0 + offset * 2.0
        o = mid - 1.5
        c = mid + 2.0  # strong close near high
        h = c + 0.5
        l = o - 0.5
        rows.append({
            "timestamp": base_ts + timedelta(hours=i),
            "open": round(o, 2),
            "high": round(h, 2),
            "low": round(l, 2),
            "close": round(c, 2),
            "volume": 2500.0,
        })

    # Pad remaining bars with gentle uptrend
    last_close = rows[-1]["close"]
    for i in range(109, count):
        offset = i - 109
        mid = last_close + offset * 0.5
        o = mid - 1.0
        c = mid + 1.0
        h = c + 1.0
        l = o - 1.0
        rows.append({
            "timestamp": base_ts + timedelta(hours=i),
            "open": round(o, 2),
            "high": round(h, 2),
            "low": round(l, 2),
            "close": round(c, 2),
            "volume": 1100.0,
        })

    return pd.DataFrame(rows)


def make_bearish_sweep_candles(count: int = 150) -> pd.DataFrame:
    """Generate a scenario designed to produce a bearish liquidity sweep signal.

    Structure:
      - Bars 0-79:   gentle downtrend establishing swing highs
      - Bars 80-89:  bounce creating a clear swing high around bar 85
      - Bar 90-104:  drift back down
      - Bar 105:     THE SWEEP -- wick above the swing high, close below it
      - Bars 106-108: confirmation candles closing below the sweep candle's low
    """
    base_ts = datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)
    rows: list[dict] = []

    # Phase 1: gentle downtrend (bars 0-79)
    for i in range(80):
        mid = 2700.0 - i * 0.5 + math.sin(i * 0.5) * 2.0
        o = mid + 1.0
        c = mid - 1.0
        h = o + 1.5
        l = c - 1.5
        rows.append({
            "timestamp": base_ts + timedelta(hours=i),
            "open": round(o, 2),
            "high": round(h, 2),
            "low": round(l, 2),
            "close": round(c, 2),
            "volume": 1200.0,
        })

    # Phase 2: bounce creating a swing high (bars 80-89)
    last_close = rows[-1]["close"]
    for i in range(80, 90):
        offset = i - 80
        if offset < 5:
            mid = last_close + offset * 3.0
        else:
            mid = last_close + 5 * 3.0 - (offset - 5) * 2.5
        o = mid + 1.0
        c = mid - 1.0
        h = o + 1.0
        l = c - 1.0
        rows.append({
            "timestamp": base_ts + timedelta(hours=i),
            "open": round(o, 2),
            "high": round(h, 2),
            "low": round(l, 2),
            "close": round(c, 2),
            "volume": 1500.0,
        })

    swing_high_level = max(r["high"] for r in rows[80:90])

    # Phase 3: drift back down (bars 90-104)
    last_close = rows[-1]["close"]
    for i in range(90, 105):
        offset = i - 90
        mid = last_close - offset * 1.5
        o = mid + 1.0
        c = mid - 1.0
        h = o + 1.5
        l = c - 1.5
        rows.append({
            "timestamp": base_ts + timedelta(hours=i),
            "open": round(o, 2),
            "high": round(h, 2),
            "low": round(l, 2),
            "close": round(c, 2),
            "volume": 1300.0,
        })

    # Phase 4: THE SWEEP (bar 105)
    last_close = rows[-1]["close"]
    sweep_open = last_close + 2.0
    sweep_high = swing_high_level + 5.0
    sweep_close = swing_high_level - 4.0
    sweep_low = sweep_close - 2.0
    rows.append({
        "timestamp": base_ts + timedelta(hours=105),
        "open": round(sweep_open, 2),
        "high": round(sweep_high, 2),
        "low": round(sweep_low, 2),
        "close": round(sweep_close, 2),
        "volume": 3000.0,
    })

    # Phase 5: confirmation candles (bars 106-108)
    for i in range(106, 109):
        offset = i - 106
        mid = sweep_low - 3.0 - offset * 2.0
        o = mid + 1.5
        c = mid - 2.0  # strong close near low
        h = o + 0.5
        l = c - 0.5
        rows.append({
            "timestamp": base_ts + timedelta(hours=i),
            "open": round(o, 2),
            "high": round(h, 2),
            "low": round(l, 2),
            "close": round(c, 2),
            "volume": 2500.0,
        })

    # Pad remaining bars
    last_close = rows[-1]["close"]
    for i in range(109, count):
        offset = i - 109
        mid = last_close - offset * 0.5
        o = mid + 1.0
        c = mid - 1.0
        h = o + 1.0
        l = c - 1.0
        rows.append({
            "timestamp": base_ts + timedelta(hours=i),
            "open": round(o, 2),
            "high": round(h, 2),
            "low": round(l, 2),
            "close": round(c, 2),
            "volume": 1100.0,
        })

    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestStrategyRegistration:
    """Registration and metadata tests."""

    def test_strategy_registered(self):
        """BaseStrategy.get_registry() contains 'liquidity_sweep'."""
        registry = BaseStrategy.get_registry()
        assert "liquidity_sweep" in registry
        assert registry["liquidity_sweep"] is LiquiditySweepStrategy

    def test_strategy_attributes(self):
        """Core class attributes are set correctly."""
        s = LiquiditySweepStrategy()
        assert s.name == "liquidity_sweep"
        assert s.required_timeframes == ["H1"]
        assert s.min_candles == 100


class TestDataValidation:
    """Input validation tests."""

    def test_insufficient_data_raises(self):
        """Passing fewer than min_candles raises InsufficientDataError."""
        s = LiquiditySweepStrategy()
        candles = make_candles(50)
        with pytest.raises(InsufficientDataError):
            s.analyze(candles)

    def test_validate_data_checks_columns(self):
        """Missing required column raises ValueError."""
        s = LiquiditySweepStrategy()
        candles = make_candles(120)
        # Remove a required column
        bad_candles = candles.drop(columns=["high"])
        with pytest.raises(ValueError, match="Missing required columns"):
            s.analyze(bad_candles)

    def test_exact_min_candles_does_not_raise(self):
        """Providing exactly min_candles should not raise."""
        s = LiquiditySweepStrategy()
        candles = make_candles(100)
        # Should not raise
        result = s.analyze(candles)
        assert isinstance(result, list)


class TestAnalyzeReturnType:
    """Basic return type and structure tests."""

    def test_analyze_returns_list_of_candidate_signals(self):
        """analyze() returns a list; every element is CandidateSignal."""
        s = LiquiditySweepStrategy()
        candles = make_sweep_candles(150)
        result = s.analyze(candles)
        assert isinstance(result, list)
        for sig in result:
            assert isinstance(sig, CandidateSignal)

    def test_analyze_on_flat_data_returns_list(self):
        """analyze() on flat data returns a list (likely empty)."""
        s = LiquiditySweepStrategy()
        candles = make_candles(150, trend="flat")
        result = s.analyze(candles)
        assert isinstance(result, list)


class TestSignalFields:
    """Validate signal field correctness when signals are produced."""

    @pytest.fixture
    def bullish_signals(self) -> list[CandidateSignal]:
        """Produce signals from the bullish sweep scenario."""
        s = LiquiditySweepStrategy()
        candles = make_sweep_candles(150)
        return s.analyze(candles)

    @pytest.fixture
    def bearish_signals(self) -> list[CandidateSignal]:
        """Produce signals from the bearish sweep scenario."""
        s = LiquiditySweepStrategy()
        candles = make_bearish_sweep_candles(150)
        return s.analyze(candles)

    def test_signal_fields_populated(self, bullish_signals):
        """When a signal is produced, all required fields have valid values."""
        # If the synthetic data produces signals, validate them
        for sig in bullish_signals:
            assert sig.direction in (Direction.BUY, Direction.SELL)
            assert sig.entry_price > 0
            assert sig.stop_loss > 0
            assert sig.take_profit_1 > 0
            assert sig.take_profit_2 > 0
            assert sig.risk_reward > 0
            assert 0 <= sig.confidence <= 100
            assert len(sig.reasoning) > 0
            assert sig.timestamp is not None

    def test_buy_signal_sl_below_entry(self, bullish_signals):
        """For BUY signals: stop_loss < entry_price < take_profit_1 < take_profit_2."""
        buy_signals = [s for s in bullish_signals if s.direction == Direction.BUY]
        for sig in buy_signals:
            assert sig.stop_loss < sig.entry_price, (
                f"SL {sig.stop_loss} should be < entry {sig.entry_price}"
            )
            assert sig.entry_price < sig.take_profit_1, (
                f"Entry {sig.entry_price} should be < TP1 {sig.take_profit_1}"
            )
            assert sig.take_profit_1 < sig.take_profit_2, (
                f"TP1 {sig.take_profit_1} should be < TP2 {sig.take_profit_2}"
            )

    def test_sell_signal_sl_above_entry(self, bearish_signals):
        """For SELL signals: take_profit_2 < take_profit_1 < entry_price < stop_loss."""
        sell_signals = [s for s in bearish_signals if s.direction == Direction.SELL]
        for sig in sell_signals:
            assert sig.stop_loss > sig.entry_price, (
                f"SL {sig.stop_loss} should be > entry {sig.entry_price}"
            )
            assert sig.entry_price > sig.take_profit_1, (
                f"Entry {sig.entry_price} should be > TP1 {sig.take_profit_1}"
            )
            assert sig.take_profit_1 > sig.take_profit_2, (
                f"TP1 {sig.take_profit_1} should be > TP2 {sig.take_profit_2}"
            )

    def test_confidence_bounds(self, bullish_signals):
        """Confidence is always between 0 and 100."""
        for sig in bullish_signals:
            assert Decimal("0") <= sig.confidence <= Decimal("100")

    def test_risk_reward_is_positive(self, bullish_signals):
        """Risk/reward ratio is positive."""
        for sig in bullish_signals:
            assert sig.risk_reward > 0


class TestSessionFilter:
    """Session filtering tests."""

    def test_session_filter_applied(self):
        """All returned signals have timestamps within London or NY session hours."""
        s = LiquiditySweepStrategy()
        candles = make_sweep_candles(150)
        signals = s.analyze(candles)
        for sig in signals:
            hour = sig.timestamp.hour
            # London: 7-16 UTC, New York: 12-21 UTC
            in_london = 7 <= hour < 16
            in_ny = 12 <= hour < 21
            assert in_london or in_ny, (
                f"Signal at hour {hour} UTC is outside London/NY sessions"
            )

    def test_no_signals_outside_sessions(self):
        """Candles entirely outside trading sessions produce no signals."""
        s = LiquiditySweepStrategy()
        # Create candles starting at 22:00 UTC -- outside London and NY
        candles = make_candles(150, start_hour=22)
        signals = s.analyze(candles)
        # Most (if not all) candles will be in Asian session or no session
        # Signals should be very rare or empty
        for sig in signals:
            hour = sig.timestamp.hour
            in_london = 7 <= hour < 16
            in_ny = 12 <= hour < 21
            assert in_london or in_ny, (
                f"Signal generated outside session at hour {hour}"
            )


class TestStrategyName:
    """Strategy name consistency in signals."""

    def test_strategy_name_in_signals(self):
        """All signals have strategy_name == 'liquidity_sweep'."""
        s = LiquiditySweepStrategy()
        candles = make_sweep_candles(150)
        signals = s.analyze(candles)
        for sig in signals:
            assert sig.strategy_name == "liquidity_sweep"


class TestNoSignalsOnFlatData:
    """False positive guard: flat data should not generate many signals."""

    def test_no_signals_on_flat_data(self):
        """Flat, non-volatile data produces few or no signals."""
        s = LiquiditySweepStrategy()
        candles = make_candles(200, trend="flat")
        signals = s.analyze(candles)
        # Flat data might produce some signals from sine noise but should be minimal
        # We mainly validate it doesn't crash and doesn't generate an absurd number
        assert len(signals) < 30, (
            f"Flat data produced {len(signals)} signals -- too many false positives"
        )


class TestNoLookahead:
    """Verify no lookahead bias in signal generation."""

    def test_signal_uses_data_up_to_signal_bar(self):
        """Signals should be deterministic: adding future data should not
        change signals for earlier bars."""
        s = LiquiditySweepStrategy()
        full = make_sweep_candles(150)
        truncated = full.iloc[:130].copy().reset_index(drop=True)

        signals_full = s.analyze(full)
        signals_trunc = s.analyze(truncated)

        # Filter signals from full that have index <= 129
        # (timestamp-based: truncated ends at bar 129)
        max_ts = truncated["timestamp"].iloc[-1]
        # Ensure timezone-aware comparison
        if hasattr(max_ts, 'to_pydatetime'):
            max_ts = max_ts.to_pydatetime()
        if max_ts.tzinfo is None:
            max_ts = max_ts.replace(tzinfo=timezone.utc)
        early_full = [
            sig for sig in signals_full
            if sig.timestamp.replace(tzinfo=timezone.utc) <= max_ts
            if sig.timestamp is not None
        ]

        # Same number of signals for the overlapping range
        assert len(early_full) == len(signals_trunc), (
            f"Lookahead detected: {len(early_full)} signals with full data "
            f"vs {len(signals_trunc)} with truncated data in same range"
        )

        # Same entry prices (sorted by timestamp for stable comparison)
        early_full_sorted = sorted(early_full, key=lambda s: s.timestamp)
        trunc_sorted = sorted(signals_trunc, key=lambda s: s.timestamp)
        for sf, st in zip(early_full_sorted, trunc_sorted):
            assert sf.entry_price == st.entry_price, (
                f"Entry price mismatch: {sf.entry_price} vs {st.entry_price}"
            )


class TestDecimalPrecision:
    """Price fields use Decimal with proper precision."""

    def test_prices_are_decimal(self):
        """All price fields are Decimal instances."""
        s = LiquiditySweepStrategy()
        candles = make_sweep_candles(150)
        signals = s.analyze(candles)
        for sig in signals:
            assert isinstance(sig.entry_price, Decimal)
            assert isinstance(sig.stop_loss, Decimal)
            assert isinstance(sig.take_profit_1, Decimal)
            assert isinstance(sig.take_profit_2, Decimal)
            assert isinstance(sig.risk_reward, Decimal)
            assert isinstance(sig.confidence, Decimal)
