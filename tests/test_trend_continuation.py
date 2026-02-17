"""Unit tests for TrendContinuationStrategy (STRAT-02).

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
from app.strategies.trend_continuation import TrendContinuationStrategy


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_trending_candles(
    count: int = 250,
    direction: str = "up",
    base_price: float = 2600.0,
) -> pd.DataFrame:
    """Generate synthetic candles with a clear EMA trend and a pullback zone.

    For uptrend:
    - Bars 0-199: gradual uptrend so EMA-50 > EMA-200 after warmup
    - Bars 200-215: continuing uptrend (price well above EMA-50)
    - Bars 216-225: pullback to EMA-50 zone
    - Bars 226-230: momentum confirmation candles (close > prev high)
    - Bars 231+: trend resumes

    For downtrend: mirror image.

    All timestamps start at 10:00 UTC (London session).
    """
    base_ts = datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)
    rows: list[dict] = []

    if direction == "up":
        for i in range(count):
            # Steady uptrend with small noise
            trend_drift = i * 0.5
            noise = math.sin(i * 0.3) * 1.5

            if 216 <= i <= 225:
                # Pullback zone: flatten / slight dip toward EMA-50 zone
                # EMA-50 should be roughly at base_price + (i-25) * 0.5
                # Pull price down toward that level
                pullback_depth = (i - 216) * -1.5
                trend_drift = 216 * 0.5 + pullback_depth
                noise *= 0.3  # reduce noise during pullback
            elif 226 <= i <= 230:
                # Momentum resumption: strong green candles
                trend_drift = 216 * 0.5 - 10 * 1.5 + (i - 226) * 3.0
                noise = 0.5

            mid = base_price + trend_drift + noise

            if 226 <= i <= 230:
                # Strong bullish candles for confirmation
                o = mid - 2.0
                c = mid + 3.0
                h = c + 1.0
                l = o - 0.5
            else:
                o = mid - 1.0
                c = mid + 1.0
                h = max(o, c) + abs(noise) * 0.3 + 1.0
                l = min(o, c) - abs(noise) * 0.3 - 1.0

            rows.append({
                "timestamp": base_ts + timedelta(hours=i),
                "open": round(o, 2),
                "high": round(h, 2),
                "low": round(l, 2),
                "close": round(c, 2),
                "volume": 1000.0 + i * 5,
            })
    else:  # "down"
        for i in range(count):
            trend_drift = -i * 0.5
            noise = math.sin(i * 0.3) * 1.5

            if 216 <= i <= 225:
                # Pullback zone: prices rise back toward EMA-50
                pullback_depth = (i - 216) * 1.5
                trend_drift = -216 * 0.5 + pullback_depth
                noise *= 0.3
            elif 226 <= i <= 230:
                # Bearish confirmation: strong red candles
                trend_drift = -216 * 0.5 + 10 * 1.5 - (i - 226) * 3.0
                noise = -0.5

            mid = base_price + trend_drift + noise

            if 226 <= i <= 230:
                # Strong bearish candles for confirmation
                o = mid + 2.0
                c = mid - 3.0
                h = o + 0.5
                l = c - 1.0
            else:
                o = mid + 1.0
                c = mid - 1.0
                h = max(o, c) + abs(noise) * 0.3 + 1.0
                l = min(o, c) - abs(noise) * 0.3 - 1.0

            rows.append({
                "timestamp": base_ts + timedelta(hours=i),
                "open": round(o, 2),
                "high": round(h, 2),
                "low": round(l, 2),
                "close": round(c, 2),
                "volume": 1000.0 + i * 5,
            })

    return pd.DataFrame(rows)


def make_flat_candles(count: int = 250, base_price: float = 2600.0) -> pd.DataFrame:
    """Generate flat, non-trending candles for false-positive testing."""
    base_ts = datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)
    rows: list[dict] = []

    for i in range(count):
        noise = math.sin(i * 0.5) * 2.0
        mid = base_price + noise
        o = mid - 0.5
        c = mid + 0.5
        h = max(o, c) + 1.0
        l = min(o, c) - 1.0
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
        """'trend_continuation' is in the registry."""
        registry = BaseStrategy.get_registry()
        assert "trend_continuation" in registry
        assert registry["trend_continuation"] is TrendContinuationStrategy

    def test_min_candles_200(self):
        """strategy.min_candles == 200."""
        s = TrendContinuationStrategy()
        assert s.min_candles == 200

    def test_strategy_name_correct(self):
        """strategy.name == 'trend_continuation'."""
        s = TrendContinuationStrategy()
        assert s.name == "trend_continuation"


class TestDataValidation:
    """Input validation tests."""

    def test_insufficient_data_raises(self):
        """100 candles (< 200 min) raises InsufficientDataError."""
        s = TrendContinuationStrategy()
        candles = make_flat_candles(100)
        with pytest.raises(InsufficientDataError):
            s.analyze(candles)

    def test_validate_data_missing_columns(self):
        """DataFrame missing 'close' column raises ValueError."""
        s = TrendContinuationStrategy()
        candles = make_flat_candles(250)
        bad_candles = candles.drop(columns=["close"])
        with pytest.raises(ValueError, match="Missing required columns"):
            s.analyze(bad_candles)


class TestAnalyzeReturn:
    """Return type tests."""

    def test_analyze_returns_list(self):
        """analyze() returns a list (possibly empty)."""
        s = TrendContinuationStrategy()
        candles = make_trending_candles(250, direction="up")
        result = s.analyze(candles)
        assert isinstance(result, list)

    def test_analyze_returns_list_downtrend(self):
        """analyze() returns a list for downtrend data."""
        s = TrendContinuationStrategy()
        candles = make_trending_candles(250, direction="down")
        result = s.analyze(candles)
        assert isinstance(result, list)


class TestSignalFields:
    """Validate CandidateSignal field correctness."""

    @pytest.fixture
    def up_signals(self) -> list[CandidateSignal]:
        s = TrendContinuationStrategy()
        candles = make_trending_candles(250, direction="up")
        return s.analyze(candles)

    @pytest.fixture
    def down_signals(self) -> list[CandidateSignal]:
        s = TrendContinuationStrategy()
        candles = make_trending_candles(250, direction="down")
        return s.analyze(candles)

    def test_signal_fields_valid(self, up_signals):
        """If signals produced: direction, prices, confidence are valid."""
        for sig in up_signals:
            assert sig.direction in (Direction.BUY, Direction.SELL)
            assert isinstance(sig.entry_price, Decimal)
            assert isinstance(sig.stop_loss, Decimal)
            assert isinstance(sig.take_profit_1, Decimal)
            assert isinstance(sig.take_profit_2, Decimal)
            assert sig.entry_price > 0
            assert sig.risk_reward > 0
            assert Decimal("0") <= sig.confidence <= Decimal("100")
            assert len(sig.reasoning) > 0

    def test_buy_signal_price_ordering(self, up_signals):
        """BUY: SL < entry < TP1 < TP2."""
        buy_signals = [s for s in up_signals if s.direction == Direction.BUY]
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

    def test_sell_signal_price_ordering(self, down_signals):
        """SELL: TP2 < TP1 < entry < SL."""
        sell_signals = [s for s in down_signals if s.direction == Direction.SELL]
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

    def test_strategy_name_correct(self, up_signals):
        """All signals have strategy_name == 'trend_continuation'."""
        for sig in up_signals:
            assert sig.strategy_name == "trend_continuation"
