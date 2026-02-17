"""Unit tests for MetricsCalculator.

Tests cover empty trades, all winners, all losers, mixed trades,
single trade edge case, and max drawdown calculation.
All tests are pure unit tests with no database dependencies.
"""

from decimal import Decimal

import pytest

from app.services.metrics_calculator import BacktestMetrics, MetricsCalculator
from app.services.trade_simulator import SimulatedTrade, TradeOutcome
from app.strategies.base import CandidateSignal, Direction

from datetime import datetime, timezone


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_trade(
    outcome: TradeOutcome,
    pnl_pips: float,
    bars_held: int = 5,
) -> SimulatedTrade:
    """Create a SimulatedTrade with the given outcome and PnL."""
    signal = CandidateSignal(
        strategy_name="test",
        symbol="XAUUSD",
        timeframe="H1",
        direction=Direction.BUY,
        entry_price=Decimal("2000.00"),
        stop_loss=Decimal("1995.00"),
        take_profit_1=Decimal("2005.00"),
        take_profit_2=Decimal("2010.00"),
        risk_reward=Decimal("2.00"),
        confidence=Decimal("70.00"),
        reasoning="test",
        timestamp=datetime(2026, 2, 16, 10, 0, tzinfo=timezone.utc),
    )
    return SimulatedTrade(
        signal=signal,
        outcome=outcome,
        exit_price=Decimal("2000.00"),
        pnl_pips=Decimal(str(pnl_pips)),
        bars_held=bars_held,
        spread_cost=Decimal("0.30"),
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestMetricsCalculator:
    """Tests for MetricsCalculator.compute()."""

    def setup_method(self):
        self.calc = MetricsCalculator()

    def test_empty_trades(self):
        """Empty trade list produces zeroed metrics."""
        metrics = self.calc.compute([])

        assert metrics.total_trades == 0
        assert metrics.win_rate == Decimal("0")
        assert metrics.profit_factor == Decimal("0")
        assert metrics.sharpe_ratio == Decimal("0")
        assert metrics.max_drawdown == Decimal("0")
        assert metrics.expectancy == Decimal("0")

    def test_all_winners(self):
        """All winning trades: win_rate=1.0, capped profit_factor."""
        trades = [
            _make_trade(TradeOutcome.TP1_HIT, 50.0),
            _make_trade(TradeOutcome.TP2_HIT, 100.0),
            _make_trade(TradeOutcome.TP1_HIT, 30.0),
        ]
        metrics = self.calc.compute(trades)

        assert metrics.win_rate == Decimal("1.0")
        assert metrics.profit_factor == Decimal("9999.9999")
        assert metrics.expectancy > Decimal("0")
        assert metrics.total_trades == 3

    def test_all_losers(self):
        """All losing trades: win_rate=0.0, negative expectancy."""
        trades = [
            _make_trade(TradeOutcome.SL_HIT, -50.0),
            _make_trade(TradeOutcome.SL_HIT, -30.0),
            _make_trade(TradeOutcome.SL_HIT, -40.0),
        ]
        metrics = self.calc.compute(trades)

        assert metrics.win_rate == Decimal("0.0")
        assert metrics.profit_factor == Decimal("0")
        assert metrics.expectancy < Decimal("0")
        assert metrics.total_trades == 3

    def test_mixed_trades(self):
        """Mixed wins and losses produce correct ratios."""
        trades = [
            _make_trade(TradeOutcome.TP1_HIT, 50.0),   # win
            _make_trade(TradeOutcome.SL_HIT, -25.0),    # loss
            _make_trade(TradeOutcome.TP2_HIT, 100.0),   # win
            _make_trade(TradeOutcome.EXPIRED, -10.0),    # loss (EXPIRED is not a win)
        ]
        metrics = self.calc.compute(trades)

        # Win rate: 2 wins (TP1_HIT, TP2_HIT) out of 4 trades
        assert metrics.win_rate == Decimal("0.5")

        # Profit factor: gross_profit / gross_loss = 150 / 35
        expected_pf = round(150.0 / 35.0, 4)
        assert float(metrics.profit_factor) == pytest.approx(expected_pf, rel=1e-3)

        # Expectancy: (50 - 25 + 100 - 10) / 4 = 28.75
        assert float(metrics.expectancy) == pytest.approx(28.75, rel=1e-3)

        assert metrics.total_trades == 4

    def test_single_trade(self):
        """Single trade: sharpe_ratio=0 (cannot compute std with n=1)."""
        trades = [_make_trade(TradeOutcome.TP1_HIT, 50.0)]
        metrics = self.calc.compute(trades)

        assert metrics.sharpe_ratio == Decimal("0")
        assert metrics.win_rate == Decimal("1.0")
        assert metrics.total_trades == 1

    def test_max_drawdown(self):
        """Known PnL sequence produces correct max drawdown."""
        # Sequence: +50, -30, -40, +20 => cumulative: 50, 20, -20, 0
        # Peak at 50, trough at -20 => drawdown = 70
        trades = [
            _make_trade(TradeOutcome.TP1_HIT, 50.0),
            _make_trade(TradeOutcome.SL_HIT, -30.0),
            _make_trade(TradeOutcome.SL_HIT, -40.0),
            _make_trade(TradeOutcome.TP1_HIT, 20.0),
        ]
        metrics = self.calc.compute(trades)

        assert float(metrics.max_drawdown) == pytest.approx(70.0, rel=1e-3)
