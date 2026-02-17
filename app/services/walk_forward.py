"""Walk-forward validation for overfitting detection.

Splits candle data 80/20 into in-sample (IS) and out-of-sample (OOS)
periods, runs independent backtests on each, and flags strategies where
OOS metrics degrade below 50% of IS metrics (indicating overfitting).

Skips overfitting detection when fewer than 5 trades in OOS to avoid
noisy results from small sample sizes.
"""

from dataclasses import dataclass
from decimal import Decimal

import pandas as pd
from loguru import logger

from app.services.backtester import BacktestRunner
from app.services.metrics_calculator import BacktestMetrics
from app.strategies.base import BaseStrategy


@dataclass
class WalkForwardResult:
    """Result of walk-forward validation for a strategy.

    Attributes:
        is_metrics: Backtest metrics on the in-sample (80%) period.
        oos_metrics: Backtest metrics on the out-of-sample (20%) period.
        is_overfitted: True if OOS metrics degrade below threshold vs IS.
        wfe_win_rate: Walk-forward efficiency for win rate (OOS/IS ratio),
            or None if computation not applicable.
        wfe_profit_factor: Walk-forward efficiency for profit factor (OOS/IS ratio),
            or None if computation not applicable.
        insufficient_oos_trades: True if OOS had fewer than MIN_OOS_TRADES,
            meaning overfitting detection was skipped.
    """

    is_metrics: BacktestMetrics
    oos_metrics: BacktestMetrics
    is_overfitted: bool
    wfe_win_rate: float | None
    wfe_profit_factor: float | None
    insufficient_oos_trades: bool


class WalkForwardValidator:
    """Detects overfitting via 80/20 in-sample/out-of-sample comparison.

    Splits candle data into IS (first 80%) and OOS (last 20%) periods,
    runs independent backtests on each using BacktestRunner, then compares
    key metrics. A strategy is flagged as overfitted if either the win rate
    or profit factor WFE ratio drops below DEGRADATION_THRESHOLD (0.5).

    When OOS produces fewer than MIN_OOS_TRADES (5), overfitting detection
    is skipped entirely to avoid drawing conclusions from noisy results.

    Attributes:
        DEGRADATION_THRESHOLD: Minimum acceptable OOS/IS ratio (0.5 = 50%).
        MIN_OOS_TRADES: Minimum trades in OOS for reliable comparison.
        runner: BacktestRunner instance for running backtests.
    """

    DEGRADATION_THRESHOLD: float = 0.5
    MIN_OOS_TRADES: int = 5

    def __init__(self, runner: BacktestRunner | None = None) -> None:
        self.runner = runner or BacktestRunner()

    def validate(
        self,
        strategy: BaseStrategy,
        candles: pd.DataFrame,
        window_days: int = 30,
    ) -> WalkForwardResult:
        """Run walk-forward validation on a strategy.

        Splits candles 80/20, runs rolling backtests on each half
        independently, and checks for metric degradation.

        Args:
            strategy: Strategy instance to validate.
            candles: Full DataFrame of H1 OHLC data.
            window_days: Window size for the rolling backtest (default 30).

        Returns:
            WalkForwardResult with IS/OOS metrics and overfitting flag.
        """
        # Split 80/20
        split_idx = int(len(candles) * 0.8)
        is_candles = candles.iloc[:split_idx].reset_index(drop=True)
        oos_candles = candles.iloc[split_idx:].reset_index(drop=True)

        logger.info(
            f"Walk-forward split: IS={len(is_candles)} bars, "
            f"OOS={len(oos_candles)} bars, "
            f"strategy={strategy.name}, window={window_days}d"
        )

        # Run backtests independently on each period
        is_metrics, is_trades = self.runner.run_full_backtest(
            strategy, is_candles, window_days
        )
        oos_metrics, oos_trades = self.runner.run_full_backtest(
            strategy, oos_candles, window_days
        )

        logger.info(
            f"Walk-forward results: IS trades={is_metrics.total_trades}, "
            f"OOS trades={oos_metrics.total_trades}"
        )

        # Check for insufficient OOS trades
        if oos_metrics.total_trades < self.MIN_OOS_TRADES:
            logger.warning(
                f"Insufficient OOS trades ({oos_metrics.total_trades} < "
                f"{self.MIN_OOS_TRADES}) for strategy '{strategy.name}' -- "
                f"skipping overfitting detection"
            )
            return WalkForwardResult(
                is_metrics=is_metrics,
                oos_metrics=oos_metrics,
                is_overfitted=False,
                wfe_win_rate=None,
                wfe_profit_factor=None,
                insufficient_oos_trades=True,
            )

        # Compute WFE ratios (OOS / IS)
        is_win_rate = float(is_metrics.win_rate)
        oos_win_rate = float(oos_metrics.win_rate)
        is_pf = float(is_metrics.profit_factor)
        oos_pf = float(oos_metrics.profit_factor)

        wfe_win_rate: float | None = None
        wfe_profit_factor: float | None = None

        if is_win_rate > 0:
            wfe_win_rate = oos_win_rate / is_win_rate
        if is_pf > 0:
            wfe_profit_factor = oos_pf / is_pf

        # Flag overfitting if either ratio drops below threshold
        is_overfitted = False
        if wfe_win_rate is not None and wfe_win_rate < self.DEGRADATION_THRESHOLD:
            is_overfitted = True
            logger.warning(
                f"Strategy '{strategy.name}' shows overfitting: "
                f"WFE win_rate={wfe_win_rate:.3f} < {self.DEGRADATION_THRESHOLD}"
            )
        if wfe_profit_factor is not None and wfe_profit_factor < self.DEGRADATION_THRESHOLD:
            is_overfitted = True
            logger.warning(
                f"Strategy '{strategy.name}' shows overfitting: "
                f"WFE profit_factor={wfe_profit_factor:.3f} < {self.DEGRADATION_THRESHOLD}"
            )

        if not is_overfitted:
            logger.info(
                f"Strategy '{strategy.name}' passed walk-forward validation: "
                f"WFE win_rate={wfe_win_rate}, WFE pf={wfe_profit_factor}"
            )

        return WalkForwardResult(
            is_metrics=is_metrics,
            oos_metrics=oos_metrics,
            is_overfitted=is_overfitted,
            wfe_win_rate=wfe_win_rate,
            wfe_profit_factor=wfe_profit_factor,
            insufficient_oos_trades=False,
        )
