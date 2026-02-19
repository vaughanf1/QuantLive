"""Signal pipeline orchestrator: the heartbeat of the trading system.

Wires StrategySelector, SignalGenerator, RiskManager, and GoldIntelligence
into a sequential flow that runs every hour:

    expire stale -> select strategy -> generate candidates ->
    validate (R:R, confidence, dedup, bias) -> risk check ->
    H4 confluence boost -> gold enrichment -> persist.

Exports:
    SignalPipeline  -- main orchestrator class
"""

from __future__ import annotations

from decimal import Decimal

from loguru import logger
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.candle import Candle
from app.models.signal import Signal
from app.models.strategy import Strategy as StrategyModel
from app.services.gold_intelligence import GoldIntelligence
from app.services.risk_manager import RiskManager
from app.services.signal_generator import SignalGenerator
from app.services.strategy_selector import StrategySelector
from app.strategies.helpers.indicators import compute_atr


class SignalPipeline:
    """Orchestrates the full signal generation pipeline.

    Flow: expire stale -> select strategy -> generate candidates ->
          validate (R:R, confidence, dedup, bias) -> risk check ->
          H4 confluence boost -> gold enrichment -> persist.
    """

    def __init__(
        self,
        selector: StrategySelector,
        generator: SignalGenerator,
        risk_manager: RiskManager,
        gold_intel: GoldIntelligence,
    ) -> None:
        self.selector = selector
        self.generator = generator
        self.risk_manager = risk_manager
        self.gold_intel = gold_intel

    async def run(self, session: AsyncSession) -> list[Signal]:
        """Execute the full signal pipeline.

        Steps:
            1. Expire stale signals
            2. Select best strategy
            3. Generate candidate signals
            4. Validate candidates (R:R, confidence, dedup, bias)
            5. Risk check (daily loss, concurrent limit, position sizing)
            6. H4 confluence boost (+5 confidence if H4 EMA agrees)
            7. DXY correlation (non-blocking, informational)
            8. Gold intelligence enrichment (session metadata, overlap boost)
            9. Persist approved signals to DB

        Args:
            session: Async database session.

        Returns:
            List of persisted Signal ORM objects.
        """
        # 1. Expire stale signals
        expired_count = await self.generator.expire_stale_signals(session)
        logger.info("Expired {} stale signal(s) before scan", expired_count)

        # 2. Select best strategy
        best = await self.selector.select_best(session)
        if best is None:
            logger.info(
                "No qualifying strategy found, skipping signal generation"
            )
            return []

        strategy_name = best.strategy_name
        regime = best.regime

        # 3. Generate candidates
        candidates = await self.generator.generate(session, strategy_name)
        if not candidates:
            logger.info(
                "No candidates from '{}', skipping", strategy_name
            )
            return []

        # 4. Validate candidates
        validated = await self.generator.validate(session, candidates)
        if not validated:
            logger.info("All candidates filtered out during validation")
            return []

        # 4b. Pick the single best candidate (highest confidence).
        #     Prevents conflicting BUY/SELL signals in the same run and
        #     avoids flooding the user with multiple simultaneous trades.
        validated.sort(
            key=lambda c: float(c.confidence), reverse=True
        )
        best_candidate = validated[0]
        if len(validated) > 1:
            logger.info(
                "Pipeline narrowed {} candidates to best: {} {} (conf={:.1f}%)",
                len(validated),
                best_candidate.direction.value,
                best_candidate.entry_price,
                float(best_candidate.confidence),
            )
        validated = [best_candidate]

        # 4c. Block opposite-direction signal if one is already active.
        active_stmt = (
            select(Signal.direction)
            .where(Signal.status == "active")
            .limit(1)
        )
        active_result = await session.execute(active_stmt)
        active_dir = active_result.scalar_one_or_none()
        if active_dir is not None:
            new_dir = validated[0].direction.value
            if new_dir != active_dir:
                logger.info(
                    "Blocking {} signal: active {} signal already open",
                    new_dir,
                    active_dir,
                )
                return []

        # 5. Risk check (with real ATR for volatility-adjusted sizing)
        current_atr, baseline_atr = await self._compute_atr(session)
        risk_results = await self.risk_manager.check(
            session, validated,
            current_atr=current_atr,
            baseline_atr=baseline_atr,
        )

        # Filter to only approved candidates
        approved_candidates = []
        approved_sizes: dict[int, Decimal] = {}  # index -> position_size
        for i, (candidate, risk_result) in enumerate(risk_results):
            if risk_result.approved:
                approved_candidates.append(candidate)
                approved_sizes[len(approved_candidates) - 1] = (
                    risk_result.position_size
                )
            else:
                logger.info(
                    "Candidate rejected by risk check: {}",
                    risk_result.rejection_reason,
                )

        if not approved_candidates:
            logger.info("All candidates rejected by risk manager")
            return []

        # 6. H4 confluence boost
        for i, candidate in enumerate(approved_candidates):
            has_confluence = await self.selector.check_h4_confluence(
                session, candidate.direction.value
            )
            if has_confluence:
                boosted = min(float(candidate.confidence) + 5, 100.0)
                new_confidence = Decimal(str(round(boosted, 2)))
                new_reasoning = candidate.reasoning + " | H4 confluence confirmed"
                approved_candidates[i] = candidate.model_copy(
                    update={
                        "confidence": new_confidence,
                        "reasoning": new_reasoning,
                    }
                )
                logger.info(
                    "H4 confluence boost: {} confidence {} -> {}",
                    candidate.direction.value,
                    candidate.confidence,
                    new_confidence,
                )

        # 7. DXY correlation (non-blocking, informational)
        dxy_info = await self.gold_intel.get_dxy_correlation(session)

        # 8. Gold intelligence enrichment
        enriched = self.gold_intel.enrich(approved_candidates, dxy_info)

        # 9. Persist signals
        # Look up strategy_id from Strategy table
        strat_stmt = select(StrategyModel).where(
            StrategyModel.name == strategy_name
        )
        strat_result = await session.execute(strat_stmt)
        strategy_row = strat_result.scalar_one_or_none()

        if strategy_row is None:
            logger.error(
                "Strategy '{}' not found in strategies table, cannot persist signals",
                strategy_name,
            )
            return []

        strategy_id = strategy_row.id

        persisted: list[Signal] = []
        for i, candidate in enumerate(enriched):
            expires_at = self.generator.compute_expiry(candidate)

            # Append position size to reasoning
            position_size = approved_sizes.get(i)
            reasoning = candidate.reasoning
            if position_size is not None:
                reasoning += f" | Position size: {position_size}"

            signal = Signal(
                strategy_id=strategy_id,
                symbol=candidate.symbol,
                timeframe=candidate.timeframe,
                direction=candidate.direction.value,
                entry_price=candidate.entry_price,
                stop_loss=candidate.stop_loss,
                take_profit_1=candidate.take_profit_1,
                take_profit_2=candidate.take_profit_2,
                risk_reward=candidate.risk_reward,
                confidence=candidate.confidence,
                reasoning=reasoning,
                status="active",
                expires_at=expires_at,
            )
            session.add(signal)
            persisted.append(signal)

        await session.commit()

        logger.info(
            "Pipeline complete: {} signal(s) generated from '{}' (regime={})",
            len(persisted),
            strategy_name,
            regime.value,
        )
        return persisted

    async def _compute_atr(
        self, session: AsyncSession
    ) -> tuple[float, float]:
        """Compute current and baseline ATR(14) from H1 candle data.

        Current ATR is the latest ATR(14) value. Baseline ATR is the mean
        of the last 50 ATR values, providing a normalization reference for
        volatility-adjusted position sizing.

        Returns:
            (current_atr, baseline_atr) -- defaults to (1.0, 1.0) if
            insufficient data is available.
        """
        import pandas as pd

        # Need at least 14 + 50 bars for a meaningful baseline
        stmt = (
            select(Candle.high, Candle.low, Candle.close)
            .where(
                and_(
                    Candle.symbol == "XAUUSD",
                    Candle.timeframe == "H1",
                )
            )
            .order_by(Candle.timestamp.desc())
            .limit(100)
        )
        result = await session.execute(stmt)
        rows = result.all()

        if len(rows) < 20:  # Need ATR(14) + a few bars minimum
            logger.debug(
                "Insufficient H1 candles ({}) for ATR, using defaults",
                len(rows),
            )
            return (1.0, 1.0)

        # Rows are desc; reverse to chronological order
        rows = list(reversed(rows))
        highs = pd.Series([float(r[0]) for r in rows])
        lows = pd.Series([float(r[1]) for r in rows])
        closes = pd.Series([float(r[2]) for r in rows])

        atr_series = compute_atr(highs, lows, closes, length=14)
        atr_valid = atr_series.dropna()

        if atr_valid.empty:
            return (1.0, 1.0)

        current_atr = float(atr_valid.iloc[-1])
        baseline_atr = float(atr_valid.mean())

        if current_atr <= 0 or baseline_atr <= 0:
            return (1.0, 1.0)

        logger.debug(
            "ATR computed: current={:.4f}, baseline={:.4f}",
            current_atr,
            baseline_atr,
        )
        return (current_atr, baseline_atr)
