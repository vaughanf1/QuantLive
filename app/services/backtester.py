"""BacktestRunner: orchestrates strategy backtesting on rolling windows.

Runs any strategy's analyze() method on sliding windows of H1 candle data,
collects SimulatedTrades, and computes BacktestMetrics. Uses the EXACT same
strategy.analyze() code path as live signal generation -- no separate
backtest-only implementations.
"""

import pandas as pd
from loguru import logger

from app.services.metrics_calculator import BacktestMetrics, MetricsCalculator
from app.services.spread_model import SessionSpreadModel
from app.services.trade_simulator import SimulatedTrade, TradeSimulator
from app.strategies.base import BaseStrategy, InsufficientDataError


class BacktestRunner:
    """Runs strategies on rolling windows and collects simulated trades.

    The runner slides a window of `window_days` across H1 candle data,
    calling strategy.analyze() on each window (same code path as live),
    then simulates resulting signals on bars AFTER the analysis window.

    Attributes:
        simulator: TradeSimulator for simulating individual trades.
        spread_model: SessionSpreadModel for realistic spread costs.
        metrics_calculator: MetricsCalculator for computing performance metrics.
    """

    def __init__(
        self,
        simulator: TradeSimulator | None = None,
        spread_model: SessionSpreadModel | None = None,
        metrics_calculator: MetricsCalculator | None = None,
    ) -> None:
        self.simulator = simulator or TradeSimulator()
        self.spread_model = spread_model or SessionSpreadModel()
        self.metrics_calculator = metrics_calculator or MetricsCalculator()

    def run_rolling_backtest(
        self,
        strategy: BaseStrategy,
        candles: pd.DataFrame,
        window_days: int,
        step_days: int = 1,
    ) -> list[SimulatedTrade]:
        """Run a strategy on rolling windows and collect simulated trades.

        Slides a window of `window_days` across the candle data, calling
        strategy.analyze() on each window. Resulting signals are simulated
        against bars AFTER the analysis window to prevent look-ahead bias.

        Args:
            strategy: Strategy instance to evaluate.
            candles: DataFrame with columns [timestamp, open, high, low, close].
                     Must be H1 candles sorted by timestamp ascending.
            window_days: Number of days per analysis window.
            step_days: Number of days to advance between windows (default 1).

        Returns:
            List of SimulatedTrade results from all windows.
        """
        window_candles = window_days * 24  # H1 = 24 candles/day
        step_candles = step_days * 24

        min_required = window_candles + TradeSimulator.MAX_BARS_FORWARD
        if len(candles) < min_required:
            logger.warning(
                "Insufficient candles for rolling backtest: "
                f"have {len(candles)}, need {min_required} "
                f"(window={window_days}d + {TradeSimulator.MAX_BARS_FORWARD} bars forward)"
            )
            return []

        trades: list[SimulatedTrade] = []

        for start_idx in range(
            0,
            len(candles) - window_candles - TradeSimulator.MAX_BARS_FORWARD,
            step_candles,
        ):
            end_idx = start_idx + window_candles
            window = candles.iloc[start_idx:end_idx].reset_index(drop=True)

            try:
                signals = strategy.analyze(window)
            except InsufficientDataError:
                logger.debug(
                    f"Skipping window at idx {start_idx}: insufficient data "
                    f"for strategy '{strategy.name}'"
                )
                continue
            except Exception:
                logger.exception(
                    f"Error in strategy '{strategy.name}' at window idx {start_idx}"
                )
                continue

            for signal in signals:
                try:
                    spread = self.spread_model.get_spread(signal.timestamp)
                    trade = self.simulator.simulate_trade(
                        signal, candles, end_idx - 1, spread
                    )
                    trades.append(trade)
                except Exception:
                    logger.exception(
                        f"Error simulating trade for signal at {signal.timestamp}"
                    )

        return trades

    def run_full_backtest(
        self,
        strategy: BaseStrategy,
        candles: pd.DataFrame,
        window_days: int,
        step_days: int = 1,
    ) -> tuple[BacktestMetrics, list[SimulatedTrade]]:
        """Run a rolling backtest and compute aggregate metrics.

        Calls run_rolling_backtest() to collect trades, then computes
        performance metrics via MetricsCalculator.

        Args:
            strategy: Strategy instance to evaluate.
            candles: DataFrame with H1 OHLC data.
            window_days: Number of days per analysis window.
            step_days: Number of days to advance between windows (default 1).

        Returns:
            Tuple of (BacktestMetrics, list[SimulatedTrade]).
        """
        trades = self.run_rolling_backtest(strategy, candles, window_days, step_days)
        metrics = self.metrics_calculator.compute(trades)

        logger.info(
            f"Backtest complete: strategy={strategy.name}, "
            f"window={window_days}d, "
            f"total_trades={metrics.total_trades}, "
            f"win_rate={metrics.win_rate}, "
            f"profit_factor={metrics.profit_factor}"
        )

        return metrics, trades

    def run_all_strategies(
        self,
        candles: pd.DataFrame,
        window_days_list: list[int] | None = None,
    ) -> dict[str, dict[int, tuple[BacktestMetrics, list[SimulatedTrade]]]]:
        """Run all registered strategies on multiple window sizes.

        Iterates over all strategies in BaseStrategy.get_registry(),
        running a full backtest for each strategy and window size.

        Args:
            candles: DataFrame with H1 OHLC data.
            window_days_list: List of window sizes in days (default [30, 60]).

        Returns:
            Nested dict: {strategy_name: {window_days: (metrics, trades)}}.
        """
        if window_days_list is None:
            window_days_list = [30, 60]

        registry = BaseStrategy.get_registry()
        results: dict[str, dict[int, tuple[BacktestMetrics, list[SimulatedTrade]]]] = {}

        for name, strategy_cls in registry.items():
            strategy = strategy_cls()
            results[name] = {}

            for window_days in window_days_list:
                try:
                    metrics, trades = self.run_full_backtest(
                        strategy, candles, window_days
                    )
                    results[name][window_days] = (metrics, trades)
                except Exception:
                    logger.exception(
                        f"Error running backtest for strategy '{name}' "
                        f"with window={window_days}d"
                    )

        return results
