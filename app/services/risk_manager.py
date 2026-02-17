"""Risk management service for capital protection and position sizing.

Enforces per-trade risk limits, concurrent signal caps, daily loss limits,
and volatility-adjusted position sizing. All monetary calculations use the
account balance from Settings.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

from loguru import logger
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.outcome import Outcome
from app.models.signal import Signal
from app.strategies.base import CandidateSignal

# ---------------------------------------------------------------------------
# Configuration constants
# ---------------------------------------------------------------------------

RISK_PER_TRADE: float = 0.01  # 1% of account per trade
MAX_CONCURRENT_SIGNALS: int = 2
DAILY_LOSS_LIMIT_PCT: float = 0.02  # 2% daily drawdown limit
PIP_VALUE: float = 0.10  # XAUUSD: $0.10 price movement per pip
ATR_FACTOR_FLOOR: float = 0.5  # minimum position size multiplier
ATR_FACTOR_CAP: float = 1.5  # maximum position size multiplier


# ---------------------------------------------------------------------------
# Result model
# ---------------------------------------------------------------------------


@dataclass
class RiskCheckResult:
    """Result of a risk check for a single candidate signal."""

    approved: bool
    rejection_reason: str | None = None
    position_size: Decimal | None = None  # only set if approved
    risk_amount: float | None = None
    daily_pnl: float | None = None


# ---------------------------------------------------------------------------
# RiskManager
# ---------------------------------------------------------------------------


class RiskManager:
    """Enforces risk rules and calculates position sizes.

    Risk checks (executed in order):
        1. Daily loss limit  -- if breached, reject ALL candidates
        2. Concurrent signal limit -- max active signals
        3. Position sizing  -- ATR-adjusted with floor/cap
    """

    async def check(
        self,
        session: AsyncSession,
        candidates: list[CandidateSignal],
        current_atr: float = 1.0,
        baseline_atr: float = 1.0,
    ) -> list[tuple[CandidateSignal, RiskCheckResult]]:
        """Run all risk checks against a list of candidate signals.

        Args:
            session: Active database session for querying signals/outcomes.
            candidates: Pre-validated candidate signals from strategy engine.
            current_atr: Current ATR(14) value for volatility adjustment.
            baseline_atr: Baseline ATR(14) for normalization (e.g. 50-period mean).

        Returns:
            List of (candidate, RiskCheckResult) tuples in input order.
        """
        results: list[tuple[CandidateSignal, RiskCheckResult]] = []

        if not candidates:
            return results

        # 0. Check circuit breaker (FEED-05) -- lazy import to avoid circular
        from app.services.feedback_controller import FeedbackController

        feedback = FeedbackController()
        circuit_active = await feedback.check_circuit_breaker(session)
        if circuit_active:
            logger.warning(
                "Circuit breaker active, suppressing all signal generation"
            )
            for candidate in candidates:
                results.append((
                    candidate,
                    RiskCheckResult(
                        approved=False,
                        rejection_reason="Circuit breaker active: signal generation halted",
                    ),
                ))
            return results

        # 1. Check daily loss limit (applies globally to all candidates)
        daily_breached, daily_pnl = await self._check_daily_loss(session)

        if daily_breached:
            logger.warning(
                "Daily loss limit breached ({pnl}), suppressing all signal generation",
                pnl=round(daily_pnl, 2),
            )
            for candidate in candidates:
                results.append((
                    candidate,
                    RiskCheckResult(
                        approved=False,
                        rejection_reason=(
                            f"Daily loss limit breached: {round(daily_pnl, 2)} pips"
                        ),
                        daily_pnl=daily_pnl,
                    ),
                ))
            return results

        # Process each candidate individually for remaining checks
        for candidate in candidates:
            # 2. Check concurrent signal limit
            at_limit, active_count = await self._check_concurrent_limit(session)
            if at_limit:
                logger.info(
                    "Concurrent signal limit reached ({count}/{max}), "
                    "rejecting candidate from {strategy}",
                    count=active_count,
                    max=MAX_CONCURRENT_SIGNALS,
                    strategy=candidate.strategy_name,
                )
                results.append((
                    candidate,
                    RiskCheckResult(
                        approved=False,
                        rejection_reason=(
                            f"Concurrent signal limit: {active_count}/"
                            f"{MAX_CONCURRENT_SIGNALS} active"
                        ),
                        daily_pnl=daily_pnl,
                    ),
                ))
                continue

            # 3. Calculate position size
            sl_distance = abs(
                float(candidate.entry_price) - float(candidate.stop_loss)
            )
            position_size = self.calculate_position_size(
                sl_distance_price=sl_distance,
                current_atr=current_atr,
                baseline_atr=baseline_atr,
            )

            account_balance = get_settings().account_balance
            risk_amount = account_balance * RISK_PER_TRADE

            logger.info(
                "Risk check APPROVED for {strategy} {direction} @ {entry}: "
                "position_size={size}, risk=${risk}",
                strategy=candidate.strategy_name,
                direction=candidate.direction.value,
                entry=candidate.entry_price,
                size=position_size,
                risk=round(risk_amount, 2),
            )

            results.append((
                candidate,
                RiskCheckResult(
                    approved=True,
                    position_size=position_size,
                    risk_amount=risk_amount,
                    daily_pnl=daily_pnl,
                ),
            ))

        return results

    async def _check_daily_loss(
        self, session: AsyncSession
    ) -> tuple[bool, float]:
        """Check if daily loss limit has been breached.

        Queries today's outcomes and sums pnl_pips. Computes loss as
        a percentage of account balance.

        Returns:
            (is_breached, daily_pnl_pips) where daily_pnl_pips is the
            sum of today's P&L in pips (negative means loss).
        """
        today_midnight = datetime.now(timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0
        )

        stmt = (
            select(func.coalesce(func.sum(Outcome.pnl_pips), 0))
            .join(Signal, Signal.id == Outcome.signal_id)
            .where(Signal.created_at >= today_midnight)
        )

        result = await session.execute(stmt)
        daily_pnl_pips = float(result.scalar_one())

        if daily_pnl_pips == 0.0:
            return (False, 0.0)

        # Convert pips to dollar amount using pip value
        daily_pnl_amount = daily_pnl_pips * PIP_VALUE
        account_balance = get_settings().account_balance
        daily_loss_pct = daily_pnl_amount / account_balance

        # Breached when loss percentage exceeds limit (loss is negative)
        is_breached = daily_loss_pct <= -DAILY_LOSS_LIMIT_PCT

        logger.debug(
            "Daily P&L: {pips} pips (${amount}), {pct:.4%} of account "
            "(limit: -{limit:.2%}), breached={breached}",
            pips=round(daily_pnl_pips, 2),
            amount=round(daily_pnl_amount, 2),
            pct=daily_loss_pct,
            limit=DAILY_LOSS_LIMIT_PCT,
            breached=is_breached,
        )

        return (is_breached, daily_pnl_pips)

    async def _check_concurrent_limit(
        self, session: AsyncSession
    ) -> tuple[bool, int]:
        """Check if the maximum concurrent active signals limit is reached.

        Returns:
            (is_at_limit, active_count) where is_at_limit is True if
            active_count >= MAX_CONCURRENT_SIGNALS.
        """
        stmt = select(func.count()).select_from(Signal).where(
            Signal.status == "active"
        )
        result = await session.execute(stmt)
        active_count = result.scalar_one()

        logger.debug(
            "Active signals: {count}/{max}",
            count=active_count,
            max=MAX_CONCURRENT_SIGNALS,
        )

        return (active_count >= MAX_CONCURRENT_SIGNALS, active_count)

    def calculate_position_size(
        self,
        sl_distance_price: float,
        current_atr: float,
        baseline_atr: float,
    ) -> Decimal:
        """Calculate volatility-adjusted position size.

        Position size = (risk_amount / sl_distance) * atr_factor

        where atr_factor = baseline_atr / current_atr, clamped to
        [ATR_FACTOR_FLOOR, ATR_FACTOR_CAP].

        Args:
            sl_distance_price: Absolute price distance from entry to SL.
            current_atr: Current ATR value for the instrument.
            baseline_atr: Baseline (average) ATR for normalization.

        Returns:
            Position size as Decimal rounded to 2 decimal places.
            Returns Decimal("0.01") for invalid inputs.
        """
        # Edge case: invalid inputs
        if sl_distance_price <= 0 or current_atr <= 0 or baseline_atr <= 0:
            logger.warning(
                "Invalid inputs for position sizing: "
                "sl_distance={sl}, current_atr={atr}, baseline_atr={base}",
                sl=sl_distance_price,
                atr=current_atr,
                base=baseline_atr,
            )
            return Decimal("0.01")

        account_balance = get_settings().account_balance
        risk_amount = account_balance * RISK_PER_TRADE

        # ATR factor: higher current ATR -> smaller position (inverse)
        atr_factor = baseline_atr / current_atr
        atr_factor = max(ATR_FACTOR_FLOOR, min(ATR_FACTOR_CAP, atr_factor))

        raw_size = (risk_amount / sl_distance_price) * atr_factor

        position_size = Decimal(str(round(raw_size, 2)))

        logger.debug(
            "Position sizing: risk=${risk}, sl_dist={sl}, "
            "atr_factor={factor:.3f} (base={base}/curr={curr}), "
            "raw={raw:.4f}, final={final}",
            risk=round(risk_amount, 2),
            sl=sl_distance_price,
            factor=atr_factor,
            base=baseline_atr,
            curr=current_atr,
            raw=raw_size,
            final=position_size,
        )

        return position_size

    async def get_drawdown_metrics(self, session: AsyncSession) -> dict:
        """Compute running and maximum drawdown from historical outcomes.

        Processes all outcomes chronologically, tracking:
        - Running P&L (cumulative sum of pnl_pips)
        - Peak P&L (highest running P&L achieved)
        - Running drawdown (peak - current)
        - Maximum drawdown (worst peak-to-trough)

        Returns:
            Dict with keys: running_drawdown, max_drawdown, running_pnl,
            peak_pnl. All values are floats (pip-based).
        """
        stmt = (
            select(Outcome.pnl_pips)
            .order_by(Outcome.created_at.asc())
        )
        result = await session.execute(stmt)
        pnl_values = [float(row[0]) for row in result.all()]

        if not pnl_values:
            logger.debug("No outcomes found for drawdown calculation")
            return {
                "running_drawdown": 0.0,
                "max_drawdown": 0.0,
                "running_pnl": 0.0,
                "peak_pnl": 0.0,
            }

        running_pnl = 0.0
        peak_pnl = 0.0
        max_drawdown = 0.0

        for pnl in pnl_values:
            running_pnl += pnl
            if running_pnl > peak_pnl:
                peak_pnl = running_pnl
            current_drawdown = peak_pnl - running_pnl
            if current_drawdown > max_drawdown:
                max_drawdown = current_drawdown

        running_drawdown = peak_pnl - running_pnl

        logger.debug(
            "Drawdown metrics: running_dd={rdd}, max_dd={mdd}, "
            "running_pnl={rpnl}, peak_pnl={ppnl}",
            rdd=round(running_drawdown, 2),
            mdd=round(max_drawdown, 2),
            rpnl=round(running_pnl, 2),
            ppnl=round(peak_pnl, 2),
        )

        return {
            "running_drawdown": round(running_drawdown, 2),
            "max_drawdown": round(max_drawdown, 2),
            "running_pnl": round(running_pnl, 2),
            "peak_pnl": round(peak_pnl, 2),
        }
