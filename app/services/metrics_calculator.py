"""Backtest metrics calculator for simulated trade results.

Computes key performance metrics from a list of SimulatedTrades:
win rate, profit factor, Sharpe ratio, max drawdown, and expectancy.
"""

import math
from dataclasses import dataclass
from decimal import Decimal

from app.services.trade_simulator import SimulatedTrade, TradeOutcome


@dataclass
class BacktestMetrics:
    """Aggregated performance metrics from a backtest run.

    All values are Decimal with 4 decimal places for DB Numeric(10,4)
    compatibility, except total_trades which is an integer.
    """

    win_rate: Decimal
    profit_factor: Decimal
    sharpe_ratio: Decimal
    max_drawdown: Decimal
    expectancy: Decimal
    total_trades: int


class MetricsCalculator:
    """Computes backtest performance metrics from simulated trades.

    Uses float internally for math operations, converting to
    Decimal(str(round(x, 4))) at the output boundary.

    Attributes:
        TRADING_DAYS_PER_YEAR: Used for annualizing Sharpe ratio.
    """

    TRADING_DAYS_PER_YEAR = 252

    def compute(self, trades: list[SimulatedTrade]) -> BacktestMetrics:
        """Compute all backtest metrics from a list of simulated trades.

        Handles edge cases: empty list, all wins, all losses, single trade.

        Args:
            trades: List of SimulatedTrade results from the trade simulator.

        Returns:
            BacktestMetrics with all 5 metrics computed.
        """
        if not trades:
            return BacktestMetrics(
                win_rate=Decimal("0"),
                profit_factor=Decimal("0"),
                sharpe_ratio=Decimal("0"),
                max_drawdown=Decimal("0"),
                expectancy=Decimal("0"),
                total_trades=0,
            )

        pnl_values = [float(t.pnl_pips) for t in trades]
        total = len(trades)

        # Win rate: TP1_HIT and TP2_HIT are wins
        wins = sum(
            1
            for t in trades
            if t.outcome in (TradeOutcome.TP1_HIT, TradeOutcome.TP2_HIT)
        )
        win_rate = wins / total

        # Separate gross profit and gross loss
        gross_profit = sum(p for p in pnl_values if p > 0)
        gross_loss = abs(sum(p for p in pnl_values if p < 0))

        # Profit factor: gross_profit / gross_loss
        if gross_loss == 0:
            # All wins or zero-loss: cap at DB max
            profit_factor = 9999.9999 if gross_profit > 0 else 0.0
        else:
            profit_factor = gross_profit / gross_loss
            # Cap for DB Numeric(10,4) compatibility
            profit_factor = min(profit_factor, 9999.9999)

        # Expectancy: average PnL per trade
        expectancy = sum(pnl_values) / total

        # Sharpe ratio: annualized (mean / std) * sqrt(trading_days)
        if total < 2:
            # Cannot compute std with fewer than 2 trades
            sharpe_ratio = 0.0
        else:
            mean_pnl = sum(pnl_values) / total
            variance = sum((p - mean_pnl) ** 2 for p in pnl_values) / (total - 1)
            std_pnl = math.sqrt(variance)
            if std_pnl == 0:
                sharpe_ratio = 0.0
            else:
                sharpe_ratio = (mean_pnl / std_pnl) * math.sqrt(
                    self.TRADING_DAYS_PER_YEAR
                )

        # Max drawdown: largest peak-to-trough decline in cumulative PnL (in pips)
        max_drawdown = self._compute_max_drawdown(pnl_values)

        return BacktestMetrics(
            win_rate=Decimal(str(round(win_rate, 4))),
            profit_factor=Decimal(str(round(profit_factor, 4))),
            sharpe_ratio=Decimal(str(round(sharpe_ratio, 4))),
            max_drawdown=Decimal(str(round(max_drawdown, 4))),
            expectancy=Decimal(str(round(expectancy, 4))),
            total_trades=total,
        )

    @staticmethod
    def _compute_max_drawdown(pnl_values: list[float]) -> float:
        """Compute maximum drawdown from a sequence of PnL values.

        Tracks the cumulative equity curve and finds the largest
        peak-to-trough decline in absolute pips.

        Args:
            pnl_values: List of per-trade PnL in pips.

        Returns:
            Maximum drawdown as a positive float (absolute pips).
        """
        if not pnl_values:
            return 0.0

        cumulative = 0.0
        peak = 0.0
        max_dd = 0.0

        for pnl in pnl_values:
            cumulative += pnl
            if cumulative > peak:
                peak = cumulative
            drawdown = peak - cumulative
            if drawdown > max_dd:
                max_dd = drawdown

        return max_dd
