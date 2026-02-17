"""Integration tests for BacktestRunner and WalkForwardValidator.

Tests cover runner instantiation, insufficient data handling,
strategy.analyze() integration, and walk-forward overfitting detection.
All tests are pure unit tests with no database dependencies.
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from app.services.backtester import BacktestRunner
from app.services.metrics_calculator import BacktestMetrics
from app.services.trade_simulator import SimulatedTrade, TradeOutcome, TradeSimulator
from app.services.walk_forward import WalkForwardResult, WalkForwardValidator
from app.strategies.base import BaseStrategy, CandidateSignal, Direction, InsufficientDataError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_candle_df(n_bars: int, base_price: float = 2000.0) -> pd.DataFrame:
    """Create a simple candle DataFrame with n_bars."""
    rows = []
    base_time = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
    for i in range(n_bars):
        price = base_price + (i * 0.1)
        rows.append({
            "timestamp": base_time + timedelta(hours=i),
            "open": price,
            "high": price + 1.0,
            "low": price - 1.0,
            "close": price + 0.5,
        })
    return pd.DataFrame(rows)


def _make_trade(
    outcome: TradeOutcome = TradeOutcome.TP1_HIT,
    pnl_pips: float = 50.0,
) -> SimulatedTrade:
    """Create a SimulatedTrade for testing."""
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
        exit_price=Decimal("2005.00"),
        pnl_pips=Decimal(str(pnl_pips)),
        bars_held=5,
        spread_cost=Decimal("0.30"),
    )


# ---------------------------------------------------------------------------
# BacktestRunner Tests
# ---------------------------------------------------------------------------

class TestBacktestRunner:
    """Tests for BacktestRunner."""

    def test_runner_instantiation(self):
        """BacktestRunner creates default simulator, spread model, and metrics calculator."""
        runner = BacktestRunner()

        assert runner.simulator is not None
        assert runner.spread_model is not None
        assert runner.metrics_calculator is not None
        assert isinstance(runner.simulator, TradeSimulator)

    def test_rolling_backtest_insufficient_data(self):
        """Returns empty trades when candle count is below minimum."""
        runner = BacktestRunner()
        strategy = MagicMock(spec=BaseStrategy)
        strategy.name = "mock_strategy"

        # Need window_days * 24 + MAX_BARS_FORWARD candles
        # For window=30: 30*24 + 72 = 792. Provide only 100.
        candles = _make_candle_df(100)
        trades = runner.run_rolling_backtest(strategy, candles, window_days=30)

        assert trades == []
        strategy.analyze.assert_not_called()

    def test_rolling_backtest_uses_analyze(self):
        """Verifies strategy.analyze() is called on each window (BACK-02 linkage)."""
        runner = BacktestRunner()
        strategy = MagicMock(spec=BaseStrategy)
        strategy.name = "mock_strategy"

        # Create a signal that will be returned by analyze
        signal = CandidateSignal(
            strategy_name="mock_strategy",
            symbol="XAUUSD",
            timeframe="H1",
            direction=Direction.BUY,
            entry_price=Decimal("2000.00"),
            stop_loss=Decimal("1990.00"),
            take_profit_1=Decimal("2020.00"),
            take_profit_2=Decimal("2030.00"),
            risk_reward=Decimal("2.00"),
            confidence=Decimal("70.00"),
            reasoning="mock signal",
            timestamp=datetime(2026, 2, 16, 10, 0, tzinfo=timezone.utc),
        )
        strategy.analyze.return_value = [signal]

        # Provide exactly enough data for one window + forward bars
        # window=30d -> 720 bars + 72 forward = 792 minimum
        # Need at least 793 for one step (range end is exclusive)
        # Plus one extra step: need 792 + step_candles(24)
        n_bars = 30 * 24 + TradeSimulator.MAX_BARS_FORWARD + 24
        candles = _make_candle_df(n_bars)

        trades = runner.run_rolling_backtest(strategy, candles, window_days=30)

        # analyze() should have been called at least once
        assert strategy.analyze.call_count >= 1


# ---------------------------------------------------------------------------
# WalkForwardValidator Tests
# ---------------------------------------------------------------------------

class TestWalkForwardValidator:
    """Tests for WalkForwardValidator."""

    def test_walk_forward_insufficient_oos_trades(self):
        """When OOS produces fewer than MIN_OOS_TRADES, overfitting is not flagged."""
        runner = MagicMock(spec=BacktestRunner)

        # IS returns many trades, OOS returns fewer than MIN_OOS_TRADES
        is_metrics = BacktestMetrics(
            win_rate=Decimal("0.6"),
            profit_factor=Decimal("2.0"),
            sharpe_ratio=Decimal("1.5"),
            max_drawdown=Decimal("10.0"),
            expectancy=Decimal("5.0"),
            total_trades=50,
        )
        oos_metrics = BacktestMetrics(
            win_rate=Decimal("0.3"),
            profit_factor=Decimal("0.5"),
            sharpe_ratio=Decimal("0.5"),
            max_drawdown=Decimal("20.0"),
            expectancy=Decimal("-2.0"),
            total_trades=3,  # Below MIN_OOS_TRADES (5)
        )
        runner.run_full_backtest.side_effect = [
            (is_metrics, [_make_trade()] * 50),
            (oos_metrics, [_make_trade()] * 3),
        ]

        validator = WalkForwardValidator(runner=runner)
        strategy = MagicMock(spec=BaseStrategy)
        strategy.name = "mock_strategy"

        candles = _make_candle_df(2000)
        result = validator.validate(strategy, candles, window_days=30)

        assert result.insufficient_oos_trades is True
        assert result.is_overfitted is False  # Skipped, not flagged

    def test_walk_forward_detects_overfitting(self):
        """Strategy flagged as overfitted when OOS metrics degrade below 50% of IS."""
        runner = MagicMock(spec=BacktestRunner)

        # IS has strong metrics
        is_metrics = BacktestMetrics(
            win_rate=Decimal("0.8"),
            profit_factor=Decimal("3.0"),
            sharpe_ratio=Decimal("2.0"),
            max_drawdown=Decimal("5.0"),
            expectancy=Decimal("10.0"),
            total_trades=100,
        )
        # OOS shows major degradation: win_rate drops from 0.8 to 0.2 (25% = below 50%)
        oos_metrics = BacktestMetrics(
            win_rate=Decimal("0.2"),
            profit_factor=Decimal("0.5"),
            sharpe_ratio=Decimal("0.3"),
            max_drawdown=Decimal("30.0"),
            expectancy=Decimal("-5.0"),
            total_trades=20,
        )
        runner.run_full_backtest.side_effect = [
            (is_metrics, [_make_trade()] * 100),
            (oos_metrics, [_make_trade(TradeOutcome.SL_HIT, -5.0)] * 20),
        ]

        validator = WalkForwardValidator(runner=runner)
        strategy = MagicMock(spec=BaseStrategy)
        strategy.name = "mock_overfit"

        candles = _make_candle_df(2000)
        result = validator.validate(strategy, candles, window_days=30)

        assert result.is_overfitted is True
        assert result.insufficient_oos_trades is False
        # WFE win rate: 0.2/0.8 = 0.25 < 0.5
        assert result.wfe_win_rate is not None
        assert result.wfe_win_rate < WalkForwardValidator.DEGRADATION_THRESHOLD
