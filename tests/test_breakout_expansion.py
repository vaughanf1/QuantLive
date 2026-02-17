"""Unit tests for BreakoutExpansionStrategy (STRAT-03).

All tests use synthetic candle data -- no database fixtures required.
"""

import math
from datetime import datetime, timedelta, timezone
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
from app.strategies.breakout_expansion import BreakoutExpansionStrategy


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_consolidation_breakout_candles(
    count: int = 120,
    base_price: float = 2650.0,
    breakout_direction: str = "up",
) -> pd.DataFrame:
    """Generate synthetic candles with consolidation followed by breakout.

    Structure:
    - Bars 0-19: establish price range with normal ATR (wide candles)
    - Bars 20-79: tight consolidation (small candles, narrow range ~$5)
    - Bars 80-84: transition
    - Bars 85-90: breakout candle(s) that close decisively beyond range
    - Bars 91+: continuation after breakout

    All timestamps start at 07:00 UTC (London open for confidence bonus).
    """
    base_ts = datetime(2026, 1, 1, 7, 0, tzinfo=timezone.utc)
    rows: list[dict] = []

    # Phase 1: Normal volatility (bars 0-19)
    for i in range(20):
        noise = math.sin(i * 0.5) * 3.0
        mid = base_price + noise
        o = mid - 3.0
        c = mid + 3.0
        h = max(o, c) + 4.0
        l = min(o, c) - 4.0
        rows.append({
            "timestamp": base_ts + timedelta(hours=i),
            "open": round(o, 2),
            "high": round(h, 2),
            "low": round(l, 2),
            "close": round(c, 2),
            "volume": 2000.0,
        })

    # Phase 2: Tight consolidation (bars 20-79)
    # ATR should be very small here, and range is ~$5
    consol_mid = base_price
    consol_range = 2.5  # half of $5 range
    for i in range(20, 80):
        tiny_noise = math.sin(i * 0.7) * 0.3
        mid = consol_mid + tiny_noise
        # Very small candles
        o = mid - 0.2
        c = mid + 0.2
        h = max(o, c) + 0.3
        l = min(o, c) - 0.3
        # Keep within consolidation range
        h = min(h, consol_mid + consol_range)
        l = max(l, consol_mid - consol_range)
        rows.append({
            "timestamp": base_ts + timedelta(hours=i),
            "open": round(o, 2),
            "high": round(h, 2),
            "low": round(l, 2),
            "close": round(c, 2),
            "volume": 800.0,
        })

    # Phase 3: Transition bars (80-84) -- still compressed but slightly
    # expanding, to set up breakout detection at bar 85
    for i in range(80, 85):
        tiny_noise = math.sin(i * 0.7) * 0.3
        mid = consol_mid + tiny_noise
        o = mid - 0.3
        c = mid + 0.3
        h = max(o, c) + 0.4
        l = min(o, c) - 0.4
        h = min(h, consol_mid + consol_range)
        l = max(l, consol_mid - consol_range)
        rows.append({
            "timestamp": base_ts + timedelta(hours=i),
            "open": round(o, 2),
            "high": round(h, 2),
            "low": round(l, 2),
            "close": round(c, 2),
            "volume": 900.0,
        })

    # Determine consolidation range from actual data
    consol_data_highs = [r["high"] for r in rows[20:85]]
    consol_data_lows = [r["low"] for r in rows[20:85]]
    range_high = max(consol_data_highs)
    range_low = min(consol_data_lows)

    # Phase 4: Breakout (bars 85-90)
    if breakout_direction == "up":
        for i in range(85, 91):
            offset = i - 85
            # Large bullish candles breaking above range_high
            mid = range_high + 5.0 + offset * 4.0
            o = mid - 3.0
            c = mid + 4.0
            h = c + 2.0
            l = o - 1.0
            rows.append({
                "timestamp": base_ts + timedelta(hours=i),
                "open": round(o, 2),
                "high": round(h, 2),
                "low": round(l, 2),
                "close": round(c, 2),
                "volume": 5000.0 + offset * 500,
            })
    else:  # "down"
        for i in range(85, 91):
            offset = i - 85
            mid = range_low - 5.0 - offset * 4.0
            o = mid + 3.0
            c = mid - 4.0
            h = o + 1.0
            l = c - 2.0
            rows.append({
                "timestamp": base_ts + timedelta(hours=i),
                "open": round(o, 2),
                "high": round(h, 2),
                "low": round(l, 2),
                "close": round(c, 2),
                "volume": 5000.0 + offset * 500,
            })

    # Phase 5: Continuation (bars 91+)
    last_close = rows[-1]["close"]
    for i in range(91, count):
        offset = i - 91
        if breakout_direction == "up":
            mid = last_close + offset * 1.0
        else:
            mid = last_close - offset * 1.0
        o = mid - 2.0
        c = mid + 2.0
        h = max(o, c) + 2.0
        l = min(o, c) - 2.0
        rows.append({
            "timestamp": base_ts + timedelta(hours=i),
            "open": round(o, 2),
            "high": round(h, 2),
            "low": round(l, 2),
            "close": round(c, 2),
            "volume": 2000.0,
        })

    return pd.DataFrame(rows)


def make_flat_candles(count: int = 120, base_price: float = 2650.0) -> pd.DataFrame:
    """Generate flat candles for basic testing."""
    base_ts = datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)
    rows: list[dict] = []

    for i in range(count):
        noise = math.sin(i * 0.5) * 2.0
        mid = base_price + noise
        o = mid - 1.0
        c = mid + 1.0
        h = max(o, c) + 1.5
        l = min(o, c) - 1.5
        rows.append({
            "timestamp": base_ts + timedelta(hours=i),
            "open": round(o, 2),
            "high": round(h, 2),
            "low": round(l, 2),
            "close": round(c, 2),
            "volume": 1000.0,
        })

    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestStrategyRegistration:
    """Registration and metadata tests."""

    def test_strategy_registered(self):
        """'breakout_expansion' is in the registry."""
        registry = BaseStrategy.get_registry()
        assert "breakout_expansion" in registry
        assert registry["breakout_expansion"] is BreakoutExpansionStrategy

    def test_min_candles_70(self):
        """strategy.min_candles == 70."""
        s = BreakoutExpansionStrategy()
        assert s.min_candles == 70

    def test_strategy_name_correct(self):
        """strategy.name == 'breakout_expansion'."""
        s = BreakoutExpansionStrategy()
        assert s.name == "breakout_expansion"


class TestDataValidation:
    """Input validation tests."""

    def test_insufficient_data_raises(self):
        """30 candles (< 70 min) raises InsufficientDataError."""
        s = BreakoutExpansionStrategy()
        candles = make_flat_candles(30)
        with pytest.raises(InsufficientDataError):
            s.analyze(candles)

    def test_validate_data_missing_columns(self):
        """DataFrame missing a column raises ValueError."""
        s = BreakoutExpansionStrategy()
        candles = make_flat_candles(120)
        bad_candles = candles.drop(columns=["close"])
        with pytest.raises(ValueError, match="Missing required columns"):
            s.analyze(bad_candles)


class TestAnalyzeReturn:
    """Return type tests."""

    def test_analyze_returns_list(self):
        """analyze() returns a list."""
        s = BreakoutExpansionStrategy()
        candles = make_consolidation_breakout_candles(120)
        result = s.analyze(candles)
        assert isinstance(result, list)

    def test_analyze_returns_list_bearish(self):
        """analyze() returns a list for bearish breakout data."""
        s = BreakoutExpansionStrategy()
        candles = make_consolidation_breakout_candles(
            120, breakout_direction="down",
        )
        result = s.analyze(candles)
        assert isinstance(result, list)


class TestSignalFields:
    """Validate CandidateSignal field correctness."""

    @pytest.fixture
    def bullish_signals(self) -> list[CandidateSignal]:
        s = BreakoutExpansionStrategy()
        candles = make_consolidation_breakout_candles(
            120, breakout_direction="up",
        )
        return s.analyze(candles)

    @pytest.fixture
    def bearish_signals(self) -> list[CandidateSignal]:
        s = BreakoutExpansionStrategy()
        candles = make_consolidation_breakout_candles(
            120, breakout_direction="down",
        )
        return s.analyze(candles)

    def test_signal_fields_valid(self, bullish_signals):
        """Validates CandidateSignal field types and ranges."""
        for sig in bullish_signals:
            assert sig.direction in (Direction.BUY, Direction.SELL)
            assert isinstance(sig.entry_price, Decimal)
            assert isinstance(sig.stop_loss, Decimal)
            assert isinstance(sig.take_profit_1, Decimal)
            assert isinstance(sig.take_profit_2, Decimal)
            assert sig.entry_price > 0
            assert sig.risk_reward > 0
            assert Decimal("0") <= sig.confidence <= Decimal("100")
            assert len(sig.reasoning) > 0

    def test_buy_signal_price_ordering(self, bullish_signals):
        """BUY: SL < entry < TP1 < TP2."""
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

    def test_sell_signal_price_ordering(self, bearish_signals):
        """SELL: TP2 < TP1 < entry < SL."""
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

    def test_strategy_name_correct(self, bullish_signals):
        """All signals have strategy_name == 'breakout_expansion'."""
        for sig in bullish_signals:
            assert sig.strategy_name == "breakout_expansion"
