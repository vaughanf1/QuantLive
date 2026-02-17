"""Signal generator service: generation, validation, dedup, expiry, and bias detection.

Transforms strategy analysis into validated, de-duplicated trade signals.
Float math internally; Decimal(str(round(x, 2))) at persistence boundary only.
"""

from datetime import datetime, timedelta, timezone

from loguru import logger
from sqlalchemy import and_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.candle import Candle
from app.models.signal import Signal

# ---------------------------------------------------------------------------
# Configuration constants
# ---------------------------------------------------------------------------

MIN_RR: float = 2.0  # Minimum risk:reward ratio (1:2)
MIN_CONFIDENCE: float = 65.0  # Minimum confidence threshold (%)
DEDUP_WINDOW_HOURS: int = 4  # Same-direction dedup window (hours)
EXPIRY_HOURS: dict[str, int] = {
    "M15": 4,   # Scalp: 4 hours
    "H1": 8,    # Intraday: 8 hours
    "H4": 24,   # Intraday/swing: 24 hours
    "D1": 48,   # Swing: 48 hours
}
BIAS_WINDOW_SIGNALS: int = 20  # Number of recent signals to check for bias
BIAS_SKEW_THRESHOLD: float = 0.75  # >75% same direction flags bias


class SignalGenerator:
    """Generates, validates, deduplicates, and expires trade signals.

    Usage (within pipeline orchestrator)::

        sg = SignalGenerator()
        candidates = await sg.generate(session, "liquidity_sweep")
        validated = await sg.validate(session, candidates)
        # Persist validated signals in pipeline (Plan 05)
    """

    async def generate(
        self,
        session: AsyncSession,
        strategy_name: str,
    ) -> list:
        """Run a strategy's analyze() on latest candle data.

        Imports strategy modules inside the method body to trigger
        auto-registration and avoid circular imports (Phase 3 pattern).

        Args:
            session: Async database session.
            strategy_name: Registered strategy name (e.g. "liquidity_sweep").

        Returns:
            List of CandidateSignal instances (may be empty).
        """
        # --- Lazy imports (circular-import avoidance, Phase 3 pattern) ---
        from app.strategies.base import (
            BaseStrategy,
            CandidateSignal,
            InsufficientDataError,
            candles_to_dataframe,
        )
        # Import concrete strategies to trigger registration
        import app.strategies.liquidity_sweep  # noqa: F401
        import app.strategies.trend_continuation  # noqa: F401
        import app.strategies.breakout_expansion  # noqa: F401

        # 1. Get strategy instance
        try:
            strategy = BaseStrategy.get_strategy(strategy_name)
        except KeyError:
            logger.error(
                "Strategy '{}' not found in registry. Available: {}",
                strategy_name,
                list(BaseStrategy.get_registry().keys()),
            )
            return []

        # 2. Query latest candles for the strategy's primary timeframe
        primary_tf = strategy.required_timeframes[0]
        limit = strategy.min_candles + 50  # Extra buffer

        stmt = (
            select(Candle)
            .where(
                and_(
                    Candle.symbol == "XAUUSD",
                    Candle.timeframe == primary_tf,
                )
            )
            .order_by(Candle.timestamp.desc())
            .limit(limit)
        )
        result = await session.execute(stmt)
        candles = result.scalars().all()

        if not candles:
            logger.warning(
                "No candles found for XAUUSD/{} -- cannot generate signals",
                primary_tf,
            )
            return []

        # 3. Convert to DataFrame (sorts ascending internally)
        df = candles_to_dataframe(list(candles))

        # 4. Run strategy analysis
        try:
            candidates: list[CandidateSignal] = strategy.analyze(df)
        except InsufficientDataError as exc:
            logger.warning(
                "Insufficient data for strategy '{}': {}",
                strategy_name,
                exc,
            )
            return []

        logger.info(
            "Strategy '{}' produced {} candidate signal(s)",
            strategy_name,
            len(candidates),
        )
        return candidates

    async def validate(
        self,
        session: AsyncSession,
        candidates: list,
    ) -> list:
        """Apply validation filters to candidate signals.

        Filters applied in order:
          1. R:R >= MIN_RR  (reject below)
          2. Confidence >= MIN_CONFIDENCE  (reject below)
          3. Dedup check within DEDUP_WINDOW_HOURS  (suppress duplicates)
          4. Directional bias check  (warn only, does not reject)

        Args:
            session: Async database session.
            candidates: List of CandidateSignal instances.

        Returns:
            Filtered list of CandidateSignal instances that passed all filters.
        """
        validated: list = []

        for candidate in candidates:
            # --- Filter 1: R:R threshold (SIG-03) ---
            rr = float(candidate.risk_reward)
            if rr < MIN_RR:
                logger.info(
                    "Signal rejected: R:R {:.2f} below minimum {:.2f}",
                    rr,
                    MIN_RR,
                )
                continue

            # --- Filter 2: Confidence threshold (SIG-04) ---
            conf = float(candidate.confidence)
            if conf < MIN_CONFIDENCE:
                logger.info(
                    "Signal rejected: confidence {:.1f}% below minimum {:.1f}%",
                    conf,
                    MIN_CONFIDENCE,
                )
                continue

            # --- Filter 3: Dedup (SIG-05) ---
            if await self._is_duplicate(session, candidate):
                logger.info(
                    "Signal suppressed: duplicate {} signal within {}h window",
                    candidate.direction.value,
                    DEDUP_WINDOW_HOURS,
                )
                continue

            # --- Filter 4: Directional bias (SIG-07) ---
            if await self._check_directional_bias(session, candidate):
                logger.warning(
                    "Directional bias detected: >{}% of recent signals are {}",
                    int(BIAS_SKEW_THRESHOLD * 100),
                    candidate.direction.value,
                )
                # Informational only -- do NOT reject; append note to reasoning
                candidate = candidate.model_copy(
                    update={
                        "reasoning": (
                            candidate.reasoning
                            + " [NOTE: directional bias detected"
                            f" -- >{int(BIAS_SKEW_THRESHOLD * 100)}% of"
                            f" last {BIAS_WINDOW_SIGNALS} signals are"
                            f" {candidate.direction.value}]"
                        ),
                    }
                )

            validated.append(candidate)

        logger.info(
            "Validation complete: {}/{} candidates passed all filters",
            len(validated),
            len(candidates),
        )
        return validated

    async def _is_duplicate(
        self,
        session: AsyncSession,
        candidate: object,
    ) -> bool:
        """Check if an active signal with the same direction exists within the dedup window.

        Args:
            session: Async database session.
            candidate: CandidateSignal with symbol and direction attributes.

        Returns:
            True if a duplicate active signal exists (suppress this candidate).
        """
        cutoff = datetime.now(timezone.utc) - timedelta(hours=DEDUP_WINDOW_HOURS)

        stmt = (
            select(Signal.id)
            .where(
                and_(
                    Signal.symbol == candidate.symbol,
                    Signal.direction == candidate.direction.value,
                    Signal.status == "active",
                    Signal.created_at >= cutoff,
                )
            )
            .limit(1)
        )
        result = await session.execute(stmt)
        return result.scalar_one_or_none() is not None

    async def _check_directional_bias(
        self,
        session: AsyncSession,
        candidate: object,
    ) -> bool:
        """Detect if recent signal distribution is systematically skewed.

        Checks the last BIAS_WINDOW_SIGNALS signals. If the candidate's
        direction accounts for more than BIAS_SKEW_THRESHOLD of those
        signals, returns True (biased).

        Args:
            session: Async database session.
            candidate: CandidateSignal with direction attribute.

        Returns:
            True if directional bias is detected.
        """
        stmt = (
            select(Signal.direction)
            .order_by(Signal.created_at.desc())
            .limit(BIAS_WINDOW_SIGNALS)
        )
        result = await session.execute(stmt)
        directions = result.scalars().all()

        # Not enough data to judge bias
        if len(directions) < BIAS_WINDOW_SIGNALS:
            return False

        same_direction_count = sum(
            1 for d in directions if d == candidate.direction.value
        )
        ratio = same_direction_count / len(directions)

        return ratio > BIAS_SKEW_THRESHOLD

    def compute_expiry(self, candidate: object) -> datetime:
        """Compute the expiry timestamp for a candidate signal.

        Uses the timeframe-specific EXPIRY_HOURS mapping, defaulting
        to 8 hours if the timeframe is not explicitly configured.

        Called during signal persistence (Plan 05), not during validation.

        Args:
            candidate: CandidateSignal with timeframe and timestamp attributes.

        Returns:
            Expiry datetime (UTC).
        """
        expiry_hours = EXPIRY_HOURS.get(candidate.timeframe, 8)
        return candidate.timestamp + timedelta(hours=expiry_hours)

    async def expire_stale_signals(self, session: AsyncSession) -> int:
        """Mark active signals past their expiry as expired.

        Runs before each scanner cycle to clean up stale signals.

        Args:
            session: Async database session.

        Returns:
            Number of signals expired.
        """
        now = datetime.now(timezone.utc)

        stmt = (
            update(Signal)
            .where(
                and_(
                    Signal.status == "active",
                    Signal.expires_at.isnot(None),
                    Signal.expires_at < now,
                )
            )
            .values(status="expired")
        )
        result = await session.execute(stmt)
        count = result.rowcount

        if count > 0:
            logger.info("Expired {} stale signal(s)", count)
        else:
            logger.debug("No stale signals to expire")

        return count
