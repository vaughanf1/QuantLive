"""Strategy selector service with composite scoring and volatility regime detection.

Ranks all registered strategies by weighted composite score derived from backtest
metrics, classifies current volatility regime via ATR percentile, applies regime-based
score modifiers, and detects strategy degradation.

The ``check_h4_confluence`` method is intended to be called by ``SignalGenerator``
(Plan 04-02) for SEL-04 multi-timeframe confluence scoring -- it is not called by the
selector itself.

Exports:
    StrategySelector  -- main service class
    VolatilityRegime  -- LOW / MEDIUM / HIGH enum
    StrategyScore     -- per-strategy scoring result
    METRIC_WEIGHTS    -- weight dictionary for composite scoring
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

import numpy as np
from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.backtest_result import BacktestResult
from app.models.candle import Candle
from app.models.strategy import Strategy as StrategyModel
from app.strategies.base import candles_to_dataframe
from app.strategies.helpers.indicators import compute_atr, compute_ema


# ---------------------------------------------------------------------------
# Enums & data structures
# ---------------------------------------------------------------------------


class VolatilityRegime(str, Enum):
    """Market volatility classification based on ATR percentile."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


@dataclass
class StrategyScore:
    """Result of composite scoring for a single strategy."""

    strategy_name: str
    strategy_id: int
    composite_score: float
    win_rate: float
    profit_factor: float
    sharpe_ratio: float
    expectancy: float
    max_drawdown: float
    total_trades: int
    regime: VolatilityRegime
    is_degraded: bool
    degradation_reason: str | None


# ---------------------------------------------------------------------------
# Metric weights (must sum to 1.0)
# ---------------------------------------------------------------------------

METRIC_WEIGHTS: dict[str, float] = {
    "win_rate": 0.30,  # Primary -- consistency
    "profit_factor": 0.25,  # Close second -- profitability
    "sharpe_ratio": 0.15,  # Risk-adjusted returns
    "expectancy": 0.15,  # Average trade expectation
    "max_drawdown": 0.15,  # Inverted -- lower is better
}


# ---------------------------------------------------------------------------
# Minimum trade threshold (SEL-07)
# ---------------------------------------------------------------------------

MIN_TRADES = 50


# ---------------------------------------------------------------------------
# StrategySelector
# ---------------------------------------------------------------------------


class StrategySelector:
    """Ranks strategies by composite score and selects the best one.

    Workflow (``select_best``):
        1. Query most recent non-walk-forward BacktestResult per strategy.
        2. Exclude strategies with fewer than ``MIN_TRADES`` trades.
        3. Compute normalised composite scores.
        4. Detect current volatility regime (ATR percentile on H1).
        5. Apply regime-based score modifiers (+/-10%).
        6. Flag degraded strategies.
        7. Return the highest-scoring ``StrategyScore``, or ``None``.
    """

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def select_best(self, session: AsyncSession) -> StrategyScore | None:
        """Select the highest-scoring strategy for signal generation.

        Returns ``None`` when no strategy qualifies (all excluded or
        insufficient backtest data).
        """
        results = await self._fetch_latest_results(session)
        if not results:
            logger.warning("No qualifying BacktestResult rows found -- skipping selection")
            return None

        # Filter by minimum trade count
        qualified: list[BacktestResult] = []
        for r in results:
            if r.total_trades < MIN_TRADES:
                logger.warning(
                    "Strategy id={} excluded: only {} trades (min {})",
                    r.strategy_id,
                    r.total_trades,
                    MIN_TRADES,
                )
            else:
                qualified.append(r)

        if not qualified:
            logger.warning("All strategies excluded due to insufficient trades")
            return None

        # Compute raw composite scores
        scores = self._compute_scores(qualified)

        # Detect current volatility regime
        regime = await self._detect_volatility_regime(session)
        logger.info("Current volatility regime: {}", regime.value)

        # Attach regime to each score
        for s in scores:
            s.regime = regime

        # Apply regime modifier
        scores = self._apply_regime_modifier(scores, regime)

        # Check degradation for each strategy
        result_map: dict[str, BacktestResult] = {
            self._strategy_name(r, results): r for r in qualified
        }
        for s in scores:
            is_deg, reason = await self._check_degradation(
                s.strategy_name, result_map[s.strategy_name], session
            )
            s.is_degraded = is_deg
            s.degradation_reason = reason
            if is_deg:
                logger.warning(
                    "Strategy '{}' is degraded: {}", s.strategy_name, reason
                )

        # Re-sort: non-degraded first, then by composite_score descending
        scores.sort(key=lambda s: (not s.is_degraded, s.composite_score), reverse=True)

        best = scores[0]
        logger.info(
            "Selected strategy '{}' (score={:.4f}, degraded={})",
            best.strategy_name,
            best.composite_score,
            best.is_degraded,
        )
        return best

    async def check_h4_confluence(
        self, session: AsyncSession, direction: str
    ) -> bool:
        """Check if the H4 EMA trend agrees with the proposed signal direction.

        Used by ``SignalGenerator`` (Plan 04-02) for SEL-04 multi-timeframe
        confluence scoring.  Returns ``True`` when EMA-50 > EMA-200 for BUY
        or EMA-50 < EMA-200 for SELL.  Returns ``False`` on insufficient data.

        Args:
            session: Async database session.
            direction: ``"BUY"`` or ``"SELL"``.

        Returns:
            ``True`` if the higher timeframe agrees with the signal direction.
        """
        stmt = (
            select(Candle)
            .where(Candle.symbol == "XAUUSD", Candle.timeframe == "H4")
            .order_by(Candle.timestamp.desc())
            .limit(200)
        )
        result = await session.execute(stmt)
        candles = list(result.scalars().all())

        if len(candles) < 200:
            logger.warning(
                "H4 confluence check: insufficient candles ({}/200) -- returning False",
                len(candles),
            )
            return False

        df = candles_to_dataframe(candles)

        ema_50 = compute_ema(df["close"], length=50)
        ema_200 = compute_ema(df["close"], length=200)

        latest_ema50 = ema_50.iloc[-1]
        latest_ema200 = ema_200.iloc[-1]

        if np.isnan(latest_ema50) or np.isnan(latest_ema200):
            logger.warning("H4 confluence check: EMA values contain NaN -- returning False")
            return False

        if direction.upper() == "BUY":
            confluence = latest_ema50 > latest_ema200
        elif direction.upper() == "SELL":
            confluence = latest_ema50 < latest_ema200
        else:
            logger.error("H4 confluence check: invalid direction '{}'", direction)
            return False

        logger.info(
            "H4 confluence for {}: EMA50={:.2f}, EMA200={:.2f} -> {}",
            direction,
            latest_ema50,
            latest_ema200,
            confluence,
        )
        return confluence

    # ------------------------------------------------------------------
    # Internal: fetching
    # ------------------------------------------------------------------

    async def _fetch_latest_results(
        self, session: AsyncSession
    ) -> list[BacktestResult]:
        """Fetch the most recent non-walk-forward BacktestResult per strategy.

        Prefers ``window_days=60``; falls back to ``window_days=30`` if 60 is
        not available for a given strategy.
        """
        # Get all active strategies
        strat_stmt = select(StrategyModel).where(StrategyModel.is_active.is_(True))
        strat_result = await session.execute(strat_stmt)
        strategies = list(strat_result.scalars().all())

        if not strategies:
            logger.warning("No active strategies found in DB")
            return []

        results: list[BacktestResult] = []
        for strat in strategies:
            # Try window_days=60 first
            bt = await self._latest_result_for(session, strat.id, window_days=60)
            if bt is None:
                # Fallback to window_days=30
                bt = await self._latest_result_for(session, strat.id, window_days=30)
            if bt is None:
                # Try any non-walk-forward result
                bt = await self._latest_result_for(session, strat.id, window_days=None)
            if bt is not None:
                results.append(bt)
            else:
                logger.warning(
                    "No backtest results for strategy '{}' (id={})",
                    strat.name,
                    strat.id,
                )

        # Cache strategy name lookup
        self._strategy_names: dict[int, str] = {s.id: s.name for s in strategies}

        return results

    async def _latest_result_for(
        self,
        session: AsyncSession,
        strategy_id: int,
        window_days: int | None,
    ) -> BacktestResult | None:
        """Fetch the most recent non-walk-forward BacktestResult."""
        stmt = (
            select(BacktestResult)
            .where(
                BacktestResult.strategy_id == strategy_id,
                BacktestResult.is_walk_forward.isnot(True),
            )
        )
        if window_days is not None:
            stmt = stmt.where(BacktestResult.window_days == window_days)

        stmt = stmt.order_by(BacktestResult.created_at.desc()).limit(1)

        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    # ------------------------------------------------------------------
    # Internal: scoring
    # ------------------------------------------------------------------

    def _strategy_name(
        self, result: BacktestResult, _all_results: list[BacktestResult]
    ) -> str:
        """Resolve human-readable strategy name for a BacktestResult."""
        return self._strategy_names.get(result.strategy_id, f"strategy_{result.strategy_id}")

    def _compute_scores(self, results: list[BacktestResult]) -> list[StrategyScore]:
        """Normalise metrics to [0, 1] and compute weighted composite scores.

        For a single strategy, all normalised values default to 0.5 to avoid
        division by zero.
        """
        # Extract raw metric values (convert Decimal -> float)
        raw: dict[str, list[float]] = {
            "win_rate": [float(r.win_rate or 0) for r in results],
            "profit_factor": [float(r.profit_factor or 0) for r in results],
            "sharpe_ratio": [float(r.sharpe_ratio or 0) for r in results],
            "expectancy": [float(r.expectancy or 0) for r in results],
            "max_drawdown": [float(r.max_drawdown or 0) for r in results],
        }

        n = len(results)

        # Normalise to [0, 1]
        normalised: dict[str, list[float]] = {}
        for metric, values in raw.items():
            if n == 1:
                # Single strategy: normalise to 0.5
                normalised[metric] = [0.5]
            else:
                mn = min(values)
                mx = max(values)
                rng = mx - mn
                if rng == 0:
                    normalised[metric] = [0.5] * n
                else:
                    normalised[metric] = [(v - mn) / rng for v in values]

        scores: list[StrategyScore] = []
        for i, r in enumerate(results):
            wr = normalised["win_rate"][i]
            pf = normalised["profit_factor"][i]
            sr = normalised["sharpe_ratio"][i]
            ex = normalised["expectancy"][i]
            dd = normalised["max_drawdown"][i]

            composite = (
                METRIC_WEIGHTS["win_rate"] * wr
                + METRIC_WEIGHTS["profit_factor"] * pf
                + METRIC_WEIGHTS["sharpe_ratio"] * sr
                + METRIC_WEIGHTS["expectancy"] * ex
                + METRIC_WEIGHTS["max_drawdown"] * (1.0 - dd)  # Inverted
            )

            scores.append(
                StrategyScore(
                    strategy_name=self._strategy_names.get(
                        r.strategy_id, f"strategy_{r.strategy_id}"
                    ),
                    strategy_id=r.strategy_id,
                    composite_score=composite,
                    win_rate=float(r.win_rate or 0),
                    profit_factor=float(r.profit_factor or 0),
                    sharpe_ratio=float(r.sharpe_ratio or 0),
                    expectancy=float(r.expectancy or 0),
                    max_drawdown=float(r.max_drawdown or 0),
                    total_trades=r.total_trades,
                    regime=VolatilityRegime.MEDIUM,  # Placeholder; set later
                    is_degraded=False,
                    degradation_reason=None,
                )
            )

        # Sort descending by composite score
        scores.sort(key=lambda s: s.composite_score, reverse=True)

        logger.info(
            "Computed scores for {} strategies: {}",
            len(scores),
            [(s.strategy_name, round(s.composite_score, 4)) for s in scores],
        )
        return scores

    # ------------------------------------------------------------------
    # Internal: volatility regime
    # ------------------------------------------------------------------

    async def _detect_volatility_regime(
        self, session: AsyncSession
    ) -> VolatilityRegime:
        """Classify current volatility as LOW / MEDIUM / HIGH.

        Queries 720 H1 candles (~30 days), computes ATR(14), and ranks the
        current ATR value by percentile against the 30-day ATR series.

        Thresholds:
            <=25th percentile  ->  LOW
            >=75th percentile  ->  HIGH
            else               ->  MEDIUM
        """
        stmt = (
            select(Candle)
            .where(Candle.symbol == "XAUUSD", Candle.timeframe == "H1")
            .order_by(Candle.timestamp.desc())
            .limit(720)
        )
        result = await session.execute(stmt)
        candles = list(result.scalars().all())

        if len(candles) < 30:
            logger.warning(
                "Insufficient H1 candles for regime detection ({}/720) -- defaulting to MEDIUM",
                len(candles),
            )
            return VolatilityRegime.MEDIUM

        df = candles_to_dataframe(candles)

        atr_series = compute_atr(df["high"], df["low"], df["close"], length=14)
        atr_values = atr_series.dropna()

        if len(atr_values) < 2:
            logger.warning("ATR series too short -- defaulting to MEDIUM")
            return VolatilityRegime.MEDIUM

        current_atr = float(atr_values.iloc[-1])
        percentile = float((atr_values < current_atr).sum() / len(atr_values) * 100)

        logger.debug(
            "ATR regime: current={:.4f}, percentile={:.1f}%, series_len={}",
            current_atr,
            percentile,
            len(atr_values),
        )

        if percentile <= 25.0:
            return VolatilityRegime.LOW
        elif percentile >= 75.0:
            return VolatilityRegime.HIGH
        return VolatilityRegime.MEDIUM

    # ------------------------------------------------------------------
    # Internal: regime modifier
    # ------------------------------------------------------------------

    def _apply_regime_modifier(
        self, scores: list[StrategyScore], regime: VolatilityRegime
    ) -> list[StrategyScore]:
        """Adjust composite scores based on strategy-regime suitability.

        Modifiers are multiplicative:
            HIGH volatility:  breakout_expansion  ->  score * 0.90 (-10%)
            LOW  volatility:  trend_continuation  ->  score * 0.90 (-10%)
            MEDIUM:           no modification

        Other strategies are not modified in any regime.
        """
        for s in scores:
            if regime == VolatilityRegime.HIGH and s.strategy_name == "breakout_expansion":
                original = s.composite_score
                s.composite_score *= 0.90
                logger.info(
                    "Regime modifier: '{}' score {:.4f} -> {:.4f} (HIGH vol -10%)",
                    s.strategy_name,
                    original,
                    s.composite_score,
                )
            elif regime == VolatilityRegime.LOW and s.strategy_name == "trend_continuation":
                original = s.composite_score
                s.composite_score *= 0.90
                logger.info(
                    "Regime modifier: '{}' score {:.4f} -> {:.4f} (LOW vol -10%)",
                    s.strategy_name,
                    original,
                    s.composite_score,
                )

        # Re-sort after modification
        scores.sort(key=lambda s: s.composite_score, reverse=True)
        return scores

    # ------------------------------------------------------------------
    # Internal: degradation detection
    # ------------------------------------------------------------------

    async def _check_degradation(
        self,
        strategy_name: str,
        current_result: BacktestResult,
        session: AsyncSession,
    ) -> tuple[bool, str | None]:
        """Detect whether a strategy has degraded from its baseline performance.

        Degradation criteria (SEL-05, SEL-06):
            - Win rate dropped >0.15 (absolute) compared to the oldest baseline.
            - Current profit factor < 1.0.

        Returns:
            Tuple of ``(is_degraded, reason_or_none)``.
        """
        reasons: list[str] = []

        current_pf = float(current_result.profit_factor or 0)
        current_wr = float(current_result.win_rate or 0)

        # Check profit factor < 1.0
        if current_pf < 1.0:
            reasons.append(f"Profit factor {current_pf:.4f} below 1.0")

        # Query oldest non-walk-forward result for baseline comparison
        baseline_stmt = (
            select(BacktestResult)
            .where(
                BacktestResult.strategy_id == current_result.strategy_id,
                BacktestResult.is_walk_forward.isnot(True),
            )
            .order_by(BacktestResult.created_at.asc())
            .limit(1)
        )
        baseline_result = await session.execute(baseline_stmt)
        baseline = baseline_result.scalar_one_or_none()

        if baseline is not None and baseline.id != current_result.id:
            baseline_wr = float(baseline.win_rate or 0)
            drop = baseline_wr - current_wr
            if drop > 0.15:
                reasons.append(
                    f"Win rate dropped {drop:.4f} "
                    f"(from {baseline_wr:.4f} to {current_wr:.4f})"
                )

        if reasons:
            return True, "; ".join(reasons)
        return False, None
