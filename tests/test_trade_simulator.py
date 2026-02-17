"""Unit tests for TradeSimulator.

Tests cover BUY/SELL trade simulation, SL priority over TP,
expired trades, spread adjustment, and no-lookahead bias.
All tests are pure unit tests with no database dependencies.
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pandas as pd
import pytest

from app.services.trade_simulator import SimulatedTrade, TradeOutcome, TradeSimulator
from app.strategies.base import CandidateSignal, Direction


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_signal(
    direction: Direction = Direction.BUY,
    entry: str = "2000.00",
    sl: str = "1995.00",
    tp1: str = "2010.00",
    tp2: str = "2020.00",
    timestamp: datetime | None = None,
) -> CandidateSignal:
    """Create a CandidateSignal with sensible defaults."""
    if timestamp is None:
        timestamp = datetime(2026, 2, 16, 10, 0, tzinfo=timezone.utc)
    return CandidateSignal(
        strategy_name="test_strategy",
        symbol="XAUUSD",
        timeframe="H1",
        direction=direction,
        entry_price=Decimal(entry),
        stop_loss=Decimal(sl),
        take_profit_1=Decimal(tp1),
        take_profit_2=Decimal(tp2),
        risk_reward=Decimal("2.00"),
        confidence=Decimal("70.00"),
        reasoning="test signal",
        timestamp=timestamp,
    )


def _make_candles(ohlc_list: list[tuple[float, float, float, float]]) -> pd.DataFrame:
    """Create a candle DataFrame from a list of (open, high, low, close) tuples."""
    rows = []
    base_time = datetime(2026, 2, 16, 10, 0, tzinfo=timezone.utc)
    for i, (o, h, l, c) in enumerate(ohlc_list):
        rows.append({
            "timestamp": base_time + timedelta(hours=i),
            "open": o,
            "high": h,
            "low": l,
            "close": c,
        })
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestTradeSimulator:
    """Tests for TradeSimulator.simulate_trade()."""

    def setup_method(self):
        self.sim = TradeSimulator()

    def test_buy_tp1_hit(self):
        """BUY signal where TP1 is reached -- positive PnL."""
        signal = _make_signal(
            direction=Direction.BUY,
            entry="2000.00",
            sl="1995.00",
            tp1="2005.00",
            tp2="2015.00",
        )
        # Bar 0 = signal bar. Bar 1 reaches TP1 (high >= 2005).
        candles = _make_candles([
            (2000, 2001, 1999, 2000),  # bar 0 (signal bar)
            (2001, 2006, 2000, 2005),  # bar 1: high=2006 >= tp1=2005
        ])
        spread = Decimal("0.30")
        trade = self.sim.simulate_trade(signal, candles, signal_bar_idx=0, spread=spread)

        assert trade.outcome == TradeOutcome.TP1_HIT
        assert trade.exit_price == Decimal("2005.00")
        assert trade.pnl_pips > 0
        assert trade.bars_held == 1

    def test_buy_tp2_hit(self):
        """BUY signal where TP2 is reached (before TP1 check)."""
        signal = _make_signal(
            direction=Direction.BUY,
            entry="2000.00",
            sl="1995.00",
            tp1="2005.00",
            tp2="2010.00",
        )
        # Bar 1 high reaches TP2 (high >= 2010), so TP2 is checked first
        candles = _make_candles([
            (2000, 2001, 1999, 2000),  # bar 0 (signal bar)
            (2001, 2012, 2000, 2010),  # bar 1: high=2012 >= tp2=2010
        ])
        spread = Decimal("0.30")
        trade = self.sim.simulate_trade(signal, candles, signal_bar_idx=0, spread=spread)

        assert trade.outcome == TradeOutcome.TP2_HIT
        assert trade.exit_price == Decimal("2010.00")
        assert trade.pnl_pips > 0

    def test_buy_sl_hit(self):
        """BUY signal where SL is hit -- negative PnL."""
        signal = _make_signal(
            direction=Direction.BUY,
            entry="2000.00",
            sl="1995.00",
            tp1="2005.00",
            tp2="2010.00",
        )
        # Bar 1: low <= SL (1994 <= 1995)
        candles = _make_candles([
            (2000, 2001, 1999, 2000),  # bar 0
            (2000, 2001, 1994, 1996),  # bar 1: low=1994 <= sl=1995
        ])
        spread = Decimal("0.30")
        trade = self.sim.simulate_trade(signal, candles, signal_bar_idx=0, spread=spread)

        assert trade.outcome == TradeOutcome.SL_HIT
        assert trade.exit_price == Decimal("1995.00")
        assert trade.pnl_pips < 0

    def test_sell_tp1_hit(self):
        """SELL signal where TP1 is reached -- positive PnL."""
        signal = _make_signal(
            direction=Direction.SELL,
            entry="2000.00",
            sl="2005.00",
            tp1="1995.00",
            tp2="1990.00",
        )
        # Bar 1: low <= tp1 (1994 <= 1995)
        candles = _make_candles([
            (2000, 2001, 1999, 2000),  # bar 0
            (2000, 2001, 1994, 1996),  # bar 1: low=1994 <= tp1=1995
        ])
        spread = Decimal("0.30")
        trade = self.sim.simulate_trade(signal, candles, signal_bar_idx=0, spread=spread)

        assert trade.outcome == TradeOutcome.TP1_HIT
        assert trade.exit_price == Decimal("1995.00")
        assert trade.pnl_pips > 0

    def test_sell_sl_hit(self):
        """SELL signal where SL is hit -- negative PnL."""
        signal = _make_signal(
            direction=Direction.SELL,
            entry="2000.00",
            sl="2005.00",
            tp1="1995.00",
            tp2="1990.00",
        )
        # Bar 1: high + spread >= SL (2004 + 0.30 = 2004.30 < 2005, try bar 2)
        # Bar 2: high + spread >= SL (2005 + 0.30 = 2005.30 >= 2005)
        candles = _make_candles([
            (2000, 2001, 1999, 2000),  # bar 0
            (2001, 2002, 2000, 2001),  # bar 1: no hit
            (2002, 2005, 2001, 2004),  # bar 2: high=2005, 2005+0.30=2005.30 >= sl=2005
        ])
        spread = Decimal("0.30")
        trade = self.sim.simulate_trade(signal, candles, signal_bar_idx=0, spread=spread)

        assert trade.outcome == TradeOutcome.SL_HIT
        assert trade.exit_price == Decimal("2005.00")
        assert trade.pnl_pips < 0

    def test_sl_priority_over_tp(self):
        """When both SL and TP could be hit in the same bar, SL wins."""
        signal = _make_signal(
            direction=Direction.BUY,
            entry="2000.00",
            sl="1995.00",
            tp1="2005.00",
            tp2="2010.00",
        )
        # Bar 1: both SL and TP1 could trigger (low <= SL AND high >= TP1)
        candles = _make_candles([
            (2000, 2001, 1999, 2000),  # bar 0
            (2000, 2006, 1994, 2000),  # bar 1: low=1994<=1995 AND high=2006>=2005
        ])
        spread = Decimal("0.30")
        trade = self.sim.simulate_trade(signal, candles, signal_bar_idx=0, spread=spread)

        # SL takes priority (conservative assumption per BACK-01 decision)
        assert trade.outcome == TradeOutcome.SL_HIT

    def test_expired_no_hit(self):
        """No SL or TP hit within MAX_BARS_FORWARD -- trade expires."""
        signal = _make_signal(
            direction=Direction.BUY,
            entry="2000.00",
            sl="1990.00",   # SL far away
            tp1="2020.00",  # TP far away
            tp2="2030.00",
        )
        # Create enough bars that all are within safe range (no SL/TP hit)
        bar_count = TradeSimulator.MAX_BARS_FORWARD + 1  # signal bar + forward bars
        ohlc = [(2000, 2001, 1999, 2000)] * bar_count
        candles = _make_candles(ohlc)
        spread = Decimal("0.30")

        trade = self.sim.simulate_trade(signal, candles, signal_bar_idx=0, spread=spread)

        assert trade.outcome == TradeOutcome.EXPIRED
        assert trade.bars_held == TradeSimulator.MAX_BARS_FORWARD

    def test_spread_adjusts_buy_entry(self):
        """Spread increases effective entry for BUY, reducing net PnL."""
        signal = _make_signal(
            direction=Direction.BUY,
            entry="2000.00",
            sl="1995.00",
            tp1="2005.00",
            tp2="2010.00",
        )
        candles = _make_candles([
            (2000, 2001, 1999, 2000),  # bar 0
            (2001, 2006, 2000, 2005),  # bar 1: TP1 hit
        ])

        # With zero spread
        trade_zero = self.sim.simulate_trade(signal, candles, signal_bar_idx=0, spread=Decimal("0"))
        # With 0.50 spread
        trade_spread = self.sim.simulate_trade(signal, candles, signal_bar_idx=0, spread=Decimal("0.50"))

        # Both should hit TP1 but spread version has lower PnL
        assert trade_zero.outcome == TradeOutcome.TP1_HIT
        assert trade_spread.outcome == TradeOutcome.TP1_HIT
        assert trade_spread.pnl_pips < trade_zero.pnl_pips

    def test_no_lookahead(self):
        """Bar 0 (signal bar) is NOT checked for SL/TP -- only bar 1+."""
        signal = _make_signal(
            direction=Direction.BUY,
            entry="2000.00",
            sl="1995.00",
            tp1="2005.00",
            tp2="2010.00",
        )
        # Bar 0 has SL-triggering low AND TP-triggering high, but should NOT be checked
        # Bar 1 is neutral, bar 2 hits TP1
        candles = _make_candles([
            (2000, 2020, 1990, 2000),  # bar 0: would trigger SL and TP, but is signal bar
            (2001, 2002, 2000, 2001),  # bar 1: neutral
            (2001, 2006, 2000, 2005),  # bar 2: TP1 hit
        ])
        spread = Decimal("0.30")
        trade = self.sim.simulate_trade(signal, candles, signal_bar_idx=0, spread=spread)

        # Should skip bar 0 and find TP1 on bar 2
        assert trade.outcome == TradeOutcome.TP1_HIT
        assert trade.bars_held == 2
