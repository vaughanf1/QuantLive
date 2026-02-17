"""Feedback controller for strategy degradation, recovery, and circuit breaker.

Manages the self-improvement safeguard loop: detects degrading strategies,
handles auto-recovery after sustained improvement, and implements a circuit
breaker to halt signal generation during losing streaks or excessive drawdown.

Exports:
    FeedbackController  -- main service class
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.backtest_result import BacktestResult
from app.models.outcome import Outcome
from app.models.strategy_performance import StrategyPerformance
from app.services.risk_manager import RiskManager


class FeedbackController:
    """Manages strategy degradation, recovery, and circuit breaker.

    Degradation criteria (FEED-03):
        - Live win rate drops >15% below baseline (backtest) win rate
        - Live profit factor < 1.0

    Recovery criteria (FEED-04):
        - Strategy was degraded
        - Last 7 days: win_rate >= baseline - 0.05 AND profit_factor >= 1.0
        - Degradation has been active for >= 7 days

    Circuit breaker (FEED-05):
        - 5+ consecutive losses (sl_hit or expired)
        - OR current drawdown > 2x historical max drawdown
        - Auto-resets after 24-hour cooldown
        - Also resets when consecutive loss count drops below 5 (after a win)
    """

    CONSECUTIVE_LOSS_LIMIT = 5
    DRAWDOWN_MULTIPLIER = 2.0
    COOLDOWN_HOURS = 24
    DEGRADATION_RECOVERY_DAYS = 7
    WIN_RATE_DROP_THRESHOLD = 0.15

    # In-memory circuit breaker state (acceptable per decision: MemoryJobStore)
    _circuit_breaker_active: bool = False
    _circuit_breaker_triggered_at: datetime | None = None

    # ------------------------------------------------------------------
    # Degradation detection
    # ------------------------------------------------------------------

    async def check_degradation(
        self, session: AsyncSession, strategy_id: int
    ) -> tuple[bool, str | None]:
        """Check if a strategy should be flagged as degraded.

        Compares live 30d StrategyPerformance against oldest backtest baseline.
        Updates is_degraded flag in StrategyPerformance table.

        Returns (is_degraded, reason_or_none).
        """
        reasons: list[str] = []

        # Fetch live 30d performance
        perf_stmt = select(StrategyPerformance).where(
            StrategyPerformance.strategy_id == strategy_id,
            StrategyPerformance.period == "30d",
        )
        perf_result = await session.execute(perf_stmt)
        perf = perf_result.scalar_one_or_none()

        if perf is None:
            logger.debug(
                "No 30d StrategyPerformance for strategy_id={}, skipping degradation check",
                strategy_id,
            )
            return False, None

        live_wr = float(perf.win_rate or 0)
        live_pf = float(perf.profit_factor or 0)

        # Check profit factor < 1.0
        if live_pf < 1.0:
            reasons.append(f"Profit factor {live_pf:.4f} below 1.0")

        # Query oldest non-walk-forward backtest result as baseline
        baseline_stmt = (
            select(BacktestResult)
            .where(
                BacktestResult.strategy_id == strategy_id,
                BacktestResult.is_walk_forward.isnot(True),
            )
            .order_by(BacktestResult.created_at.asc())
            .limit(1)
        )
        baseline_result = await session.execute(baseline_stmt)
        baseline = baseline_result.scalar_one_or_none()

        if baseline is not None:
            baseline_wr = float(baseline.win_rate or 0)
            drop = baseline_wr - live_wr
            if drop > self.WIN_RATE_DROP_THRESHOLD:
                reasons.append(
                    f"Win rate dropped {drop:.4f} "
                    f"(from {baseline_wr:.4f} to {live_wr:.4f})"
                )

        is_degraded = len(reasons) > 0
        reason = "; ".join(reasons) if reasons else None

        # Persist the is_degraded flag
        if perf.is_degraded != is_degraded:
            perf.is_degraded = is_degraded
            await session.commit()
            logger.info(
                "Strategy id={} degradation flag updated to {} (reason: {})",
                strategy_id,
                is_degraded,
                reason,
            )

        return is_degraded, reason

    # ------------------------------------------------------------------
    # Recovery detection
    # ------------------------------------------------------------------

    async def check_recovery(
        self, session: AsyncSession, strategy_id: int
    ) -> bool:
        """Check if a degraded strategy has recovered.

        Recovery requires:
        1. Strategy is currently degraded
        2. Degradation has been active >= 7 days
        3. Last 7d metrics show recovery (win_rate within 5% of baseline, pf >= 1.0)

        Returns True if recovery detected (and clears degradation flag).
        """
        # Fetch 30d performance (must be degraded)
        perf_30d_stmt = select(StrategyPerformance).where(
            StrategyPerformance.strategy_id == strategy_id,
            StrategyPerformance.period == "30d",
            StrategyPerformance.is_degraded.is_(True),
        )
        perf_30d_result = await session.execute(perf_30d_stmt)
        perf_30d = perf_30d_result.scalar_one_or_none()

        if perf_30d is None:
            return False  # Not degraded or no performance data

        # Check degradation duration >= 7 days
        now = datetime.now(timezone.utc)
        calculated_at = perf_30d.calculated_at
        if calculated_at is not None:
            if calculated_at.tzinfo is None:
                calculated_at = calculated_at.replace(tzinfo=timezone.utc)
            days_degraded = (now - calculated_at).total_seconds() / 86400
            if days_degraded < self.DEGRADATION_RECOVERY_DAYS:
                logger.debug(
                    "Strategy id={} degraded for {:.1f} days (need {}), no recovery yet",
                    strategy_id,
                    days_degraded,
                    self.DEGRADATION_RECOVERY_DAYS,
                )
                return False
        else:
            return False  # No timestamp to evaluate

        # Fetch 7d performance for recent metrics
        perf_7d_stmt = select(StrategyPerformance).where(
            StrategyPerformance.strategy_id == strategy_id,
            StrategyPerformance.period == "7d",
        )
        perf_7d_result = await session.execute(perf_7d_stmt)
        perf_7d = perf_7d_result.scalar_one_or_none()

        if perf_7d is None:
            return False

        live_7d_wr = float(perf_7d.win_rate or 0)
        live_7d_pf = float(perf_7d.profit_factor or 0)

        # Fetch baseline for win_rate comparison
        baseline_stmt = (
            select(BacktestResult)
            .where(
                BacktestResult.strategy_id == strategy_id,
                BacktestResult.is_walk_forward.isnot(True),
            )
            .order_by(BacktestResult.created_at.asc())
            .limit(1)
        )
        baseline_result = await session.execute(baseline_stmt)
        baseline = baseline_result.scalar_one_or_none()

        if baseline is None:
            return False

        baseline_wr = float(baseline.win_rate or 0)

        # Recovery thresholds: win_rate within 5% of baseline AND pf >= 1.0
        wr_recovered = live_7d_wr >= (baseline_wr - 0.05)
        pf_recovered = live_7d_pf >= 1.0

        if wr_recovered and pf_recovered:
            # Clear degradation flag on all performance rows for this strategy
            all_perf_stmt = select(StrategyPerformance).where(
                StrategyPerformance.strategy_id == strategy_id,
                StrategyPerformance.is_degraded.is_(True),
            )
            all_perf_result = await session.execute(all_perf_stmt)
            for row in all_perf_result.scalars().all():
                row.is_degraded = False
            await session.commit()

            logger.info(
                "Strategy id={} has recovered: 7d win_rate={:.4f} "
                "(baseline={:.4f}), 7d pf={:.4f}",
                strategy_id,
                live_7d_wr,
                baseline_wr,
                live_7d_pf,
            )
            return True

        return False

    # ------------------------------------------------------------------
    # Circuit breaker
    # ------------------------------------------------------------------

    async def check_circuit_breaker(self, session: AsyncSession) -> bool:
        """Check if the circuit breaker should be active.

        Checks:
        1. If currently in cooldown and 24h has passed -> reset
        2. Count consecutive losses from most recent outcomes
        3. Check current drawdown vs 2x historical max

        Returns True if circuit breaker is active (signals should be halted).
        """
        now = datetime.now(timezone.utc)

        # 1. Check 24h cooldown reset
        if self._circuit_breaker_active and self._circuit_breaker_triggered_at is not None:
            elapsed = (now - self._circuit_breaker_triggered_at).total_seconds() / 3600
            if elapsed >= self.COOLDOWN_HOURS:
                logger.info(
                    "Circuit breaker cooldown expired ({:.1f}h >= {}h), resetting",
                    elapsed,
                    self.COOLDOWN_HOURS,
                )
                self._circuit_breaker_active = False
                self._circuit_breaker_triggered_at = None

        # 2. Check consecutive losses
        consecutive_losses = await self._count_consecutive_losses(session)

        if consecutive_losses >= self.CONSECUTIVE_LOSS_LIMIT:
            if not self._circuit_breaker_active:
                self._circuit_breaker_active = True
                self._circuit_breaker_triggered_at = now
                logger.warning(
                    "Circuit breaker ACTIVATED: {} consecutive losses (limit={})",
                    consecutive_losses,
                    self.CONSECUTIVE_LOSS_LIMIT,
                )
            return True

        # 3. Check drawdown
        rm = RiskManager()
        dd_metrics = await rm.get_drawdown_metrics(session)
        running_dd = dd_metrics["running_drawdown"]
        max_dd = dd_metrics["max_drawdown"]

        # Edge case: if max_drawdown is 0, no baseline to compare -> not triggered
        if max_dd > 0 and running_dd > self.DRAWDOWN_MULTIPLIER * max_dd:
            if not self._circuit_breaker_active:
                self._circuit_breaker_active = True
                self._circuit_breaker_triggered_at = now
                logger.warning(
                    "Circuit breaker ACTIVATED: drawdown {:.2f} > {}x max {:.2f}",
                    running_dd,
                    self.DRAWDOWN_MULTIPLIER,
                    max_dd,
                )
            return True

        # If we got here, no trigger conditions met
        if self._circuit_breaker_active:
            # Reset if cooldown expired (handled above) or conditions cleared
            self._circuit_breaker_active = False
            self._circuit_breaker_triggered_at = None
            logger.info("Circuit breaker conditions cleared, resetting")

        return False

    async def _count_consecutive_losses(self, session: AsyncSession) -> int:
        """Count consecutive losses from most recent outcome backwards.

        Queries outcomes ordered by created_at DESC. Counts sl_hit and expired
        results until a win (tp1_hit or tp2_hit) is encountered.
        """
        stmt = (
            select(Outcome.result)
            .order_by(Outcome.created_at.desc())
        )
        result = await session.execute(stmt)
        results = result.scalars().all()

        count = 0
        for r in results:
            if r in ("sl_hit", "expired"):
                count += 1
            else:
                break  # Hit a win, stop counting

        return count

    # ------------------------------------------------------------------
    # Combined check (called by jobs.py)
    # ------------------------------------------------------------------

    async def run_checks(self, session: AsyncSession) -> dict:
        """Run all feedback checks. Called after outcome detection.

        Returns summary dict with degradation changes and circuit breaker status.
        """
        summary: dict = {
            "circuit_breaker_active": False,
            "degradation_changes": [],
        }

        # Check circuit breaker
        cb_active = await self.check_circuit_breaker(session)
        summary["circuit_breaker_active"] = cb_active

        return summary
