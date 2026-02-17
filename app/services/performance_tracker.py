"""Rolling strategy performance tracker service.

Recalculates 7-day and 30-day rolling performance metrics for a given strategy
based on live signal outcomes. Metrics are upserted into the strategy_performance
table so that the StrategySelector can blend live performance data into its
composite scoring.

Metrics computed:
- win_rate: fraction of outcomes that are tp1_hit or tp2_hit
- profit_factor: gross_profit / gross_loss (capped at 9999.9999 for Numeric(10,4))
- avg_rr: average risk_reward from associated signals

Exports:
    PerformanceTracker -- main service class
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.outcome import Outcome
from app.models.signal import Signal
from app.models.strategy_performance import StrategyPerformance


class PerformanceTracker:
    """Recalculates rolling strategy performance metrics after each trade outcome.

    Call ``recalculate_for_strategy`` after recording a new outcome.  It computes
    win_rate, profit_factor, and avg_rr for both 7-day and 30-day windows, then
    upserts a StrategyPerformance row for each period (no duplicate rows).

    Class constants:
        PERIODS: Mapping of period label to number of days.
        WIN_RESULTS: Outcome result strings that count as wins.
        LOSS_RESULTS: Outcome result strings that count as losses.
        MAX_PROFIT_FACTOR: Cap for profit_factor to fit Numeric(10,4).
    """

    PERIODS: dict[str, int] = {"7d": 7, "30d": 30}
    WIN_RESULTS: set[str] = {"tp1_hit", "tp2_hit"}
    LOSS_RESULTS: set[str] = {"sl_hit", "expired"}
    MAX_PROFIT_FACTOR: Decimal = Decimal("9999.9999")

    async def recalculate_for_strategy(
        self, session: AsyncSession, strategy_id: int
    ) -> list[StrategyPerformance]:
        """Recalculate 7d and 30d rolling metrics for a single strategy.

        Args:
            session: Async SQLAlchemy session.
            strategy_id: ID of the strategy to recalculate.

        Returns:
            List of upserted StrategyPerformance rows (one per period).
        """
        results: list[StrategyPerformance] = []

        for period_label, days in self.PERIODS.items():
            metrics = await self._compute_metrics(session, strategy_id, period_label, days)
            perf = await self._upsert_performance(session, strategy_id, period_label, metrics)
            results.append(perf)

        await session.commit()

        logger.info(
            "performance_tracker: recalculated strategy_id={} -> {}",
            strategy_id,
            {r.period: f"wr={r.win_rate} pf={r.profit_factor} rr={r.avg_rr} n={r.total_signals}" for r in results},
        )

        return results

    async def _compute_metrics(
        self,
        session: AsyncSession,
        strategy_id: int,
        period_label: str,
        days: int,
    ) -> dict:
        """Compute win_rate, profit_factor, avg_rr for a rolling window.

        Queries outcomes whose created_at falls within the last ``days`` days,
        filtered to only outcomes from signals belonging to ``strategy_id``.

        Args:
            session: Async SQLAlchemy session.
            strategy_id: Strategy to filter by.
            period_label: Label string (e.g. "7d").
            days: Number of days in the rolling window.

        Returns:
            Dict with keys: win_rate, profit_factor, avg_rr, total_signals.
        """
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)

        # Query outcomes for this strategy within the window
        stmt = (
            select(Outcome, Signal.risk_reward)
            .join(Signal, Outcome.signal_id == Signal.id)
            .where(
                Signal.strategy_id == strategy_id,
                Outcome.created_at >= cutoff,
            )
        )
        result = await session.execute(stmt)
        rows = result.all()

        total = len(rows)
        if total == 0:
            return {
                "win_rate": Decimal("0.0000"),
                "profit_factor": Decimal("0.0000"),
                "avg_rr": Decimal("0.0000"),
                "total_signals": 0,
            }

        # Count wins and losses
        wins = sum(1 for row in rows if row[0].result in self.WIN_RESULTS)

        # Compute profit factor: gross_profit / gross_loss
        gross_profit = sum(
            float(row[0].pnl_pips) for row in rows if float(row[0].pnl_pips) > 0
        )
        gross_loss = abs(
            sum(float(row[0].pnl_pips) for row in rows if float(row[0].pnl_pips) < 0)
        )

        if gross_loss == 0:
            profit_factor = self.MAX_PROFIT_FACTOR if gross_profit > 0 else Decimal("0.0000")
        else:
            pf = gross_profit / gross_loss
            pf = min(pf, float(self.MAX_PROFIT_FACTOR))
            profit_factor = Decimal(str(round(pf, 4)))

        # Win rate
        win_rate = Decimal(str(round(wins / total, 4)))

        # Average risk:reward from associated signals
        rr_values = [float(row[1]) for row in rows]
        avg_rr = Decimal(str(round(sum(rr_values) / len(rr_values), 4)))

        return {
            "win_rate": win_rate,
            "profit_factor": profit_factor,
            "avg_rr": avg_rr,
            "total_signals": total,
        }

    async def _upsert_performance(
        self,
        session: AsyncSession,
        strategy_id: int,
        period: str,
        metrics: dict,
    ) -> StrategyPerformance:
        """Upsert a StrategyPerformance row for the given strategy+period.

        If a row already exists for this (strategy_id, period), update it.
        Otherwise, create a new one.

        Args:
            session: Async SQLAlchemy session.
            strategy_id: Strategy ID.
            period: Period label (e.g. "7d", "30d").
            metrics: Dict from _compute_metrics().

        Returns:
            The upserted StrategyPerformance row.
        """
        stmt = select(StrategyPerformance).where(
            StrategyPerformance.strategy_id == strategy_id,
            StrategyPerformance.period == period,
        )
        result = await session.execute(stmt)
        existing = result.scalar_one_or_none()

        now = datetime.now(timezone.utc)

        if existing is not None:
            existing.win_rate = metrics["win_rate"]
            existing.profit_factor = metrics["profit_factor"]
            existing.avg_rr = metrics["avg_rr"]
            existing.total_signals = metrics["total_signals"]
            existing.calculated_at = now
            return existing
        else:
            perf = StrategyPerformance(
                strategy_id=strategy_id,
                period=period,
                win_rate=metrics["win_rate"],
                profit_factor=metrics["profit_factor"],
                avg_rr=metrics["avg_rr"],
                total_signals=metrics["total_signals"],
                is_degraded=False,
            )
            session.add(perf)
            return perf
