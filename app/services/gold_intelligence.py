"""Gold-specific market intelligence service.

Identifies active trading sessions, applies London/NY overlap confidence
boost, attaches session metadata to signals, and monitors gold-DXY
correlation as informational enrichment.

Float math internally; Decimal(str(round(x, 2))) at CandidateSignal boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

import pandas as pd
from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.candle import Candle
from app.strategies.base import CandidateSignal
from app.strategies.helpers.session_filter import get_active_sessions

# ---------------------------------------------------------------------------
# Configuration constants
# ---------------------------------------------------------------------------

OVERLAP_CONFIDENCE_BOOST: int = 5
"""Confidence points added to signals generated during London/NY overlap."""

DXY_SYMBOL: str = "DXY"
"""Twelve Data symbol for the US Dollar Index."""

DXY_CORRELATION_WINDOW: int = 30
"""Rolling window (in periods) for Pearson correlation calculation."""

DXY_DIVERGENCE_THRESHOLD: float = -0.3
"""Correlation weaker (less negative) than this triggers a divergence flag."""

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SessionInfo:
    """Snapshot of active trading sessions at a point in time."""

    active_sessions: list[str]
    is_overlap: bool
    timestamp: datetime


@dataclass(frozen=True)
class DXYCorrelation:
    """Result of DXY rolling-correlation computation."""

    correlation: float | None
    is_divergent: bool
    available: bool
    message: str


# ---------------------------------------------------------------------------
# Volatility profiles (informational – no signal suppression)
# ---------------------------------------------------------------------------

_VOLATILITY_PROFILES: dict[str, str] = {
    "asian": "Low volatility (typically 40-60% of London)",
    "london": "High volatility (London open drives significant price movement)",
    "new_york": "High volatility (NY open adds liquidity and direction)",
    "overlap": "Very high volatility (peak liquidity, largest moves)",
}


# ---------------------------------------------------------------------------
# GoldIntelligence service
# ---------------------------------------------------------------------------


class GoldIntelligence:
    """Gold-specific market intelligence for the signal pipeline.

    Responsibilities:
        1. Identify the current trading session via ``get_active_sessions``.
        2. Enrich ``CandidateSignal`` list with session metadata and optional
           London/NY overlap confidence boost (+5).
        3. Compute rolling Pearson correlation between XAUUSD and DXY (async,
           database-backed) with full graceful degradation.
        4. Provide qualitative session volatility profiles for context.
    """

    # ------------------------------------------------------------------
    # Session identification
    # ------------------------------------------------------------------

    def get_session_info(self, timestamp: datetime | None = None) -> SessionInfo:
        """Return active trading sessions for *timestamp*.

        Args:
            timestamp: UTC datetime to evaluate.  Defaults to ``now(UTC)``.

        Returns:
            ``SessionInfo`` with the list of active sessions and whether
            the London/NY overlap window is active.
        """
        if timestamp is None:
            timestamp = datetime.now(timezone.utc)

        active = get_active_sessions(timestamp)
        is_overlap = "overlap" in active

        logger.debug(
            "Session info | ts={} active={} overlap={}",
            timestamp.isoformat(),
            active,
            is_overlap,
        )
        return SessionInfo(
            active_sessions=active,
            is_overlap=is_overlap,
            timestamp=timestamp,
        )

    # ------------------------------------------------------------------
    # Signal enrichment
    # ------------------------------------------------------------------

    def enrich(
        self,
        candidates: list[CandidateSignal],
        dxy_info: DXYCorrelation | None = None,
    ) -> list[CandidateSignal]:
        """Enrich candidate signals with session metadata and optional boosts.

        For each candidate:
        - Attach primary session name (``overlap`` if overlap is active,
          otherwise the first active session).
        - If overlap is active, add ``OVERLAP_CONFIDENCE_BOOST`` to confidence.
        - If *dxy_info* indicates divergence, append a note to reasoning
          (informational only -- confidence is **not** modified).

        Args:
            candidates: List of ``CandidateSignal`` produced by a strategy.
            dxy_info: Optional DXY correlation result. ``None`` means DXY
                      data was not fetched (skipped entirely).

        Returns:
            New list of ``CandidateSignal`` with enrichments applied.
        """
        enriched: list[CandidateSignal] = []

        now = datetime.now(timezone.utc)
        for candidate in candidates:
            session_info = self.get_session_info(now)

            # Determine primary session label
            if session_info.is_overlap:
                primary_session = "overlap"
            elif session_info.active_sessions:
                primary_session = session_info.active_sessions[0]
            else:
                primary_session = "off_hours"

            updates: dict = {"session": primary_session}
            reasoning = candidate.reasoning

            # London/NY overlap confidence boost
            if session_info.is_overlap:
                boosted = float(candidate.confidence) + OVERLAP_CONFIDENCE_BOOST
                new_confidence = Decimal(str(round(min(boosted, 100.0), 2)))
                updates["confidence"] = new_confidence
                reasoning += " | London/NY overlap: +5 confidence"
                logger.info(
                    "Overlap boost applied | strategy={} old={} new={}",
                    candidate.strategy_name,
                    candidate.confidence,
                    new_confidence,
                )

            # DXY divergence note (informational only)
            if dxy_info is not None and dxy_info.is_divergent:
                corr_str = (
                    f"{dxy_info.correlation:.2f}"
                    if dxy_info.correlation is not None
                    else "N/A"
                )
                reasoning += (
                    f" | DXY divergence detected (corr={corr_str})"
                )
                logger.info(
                    "DXY divergence noted | strategy={} corr={}",
                    candidate.strategy_name,
                    corr_str,
                )

            updates["reasoning"] = reasoning
            enriched.append(candidate.model_copy(update=updates))

        logger.debug(
            "Enriched {} candidates | dxy_available={}",
            len(enriched),
            dxy_info.available if dxy_info is not None else "not_fetched",
        )
        return enriched

    # ------------------------------------------------------------------
    # DXY correlation (async, database-backed)
    # ------------------------------------------------------------------

    async def get_dxy_correlation(
        self, session: AsyncSession
    ) -> DXYCorrelation:
        """Compute rolling Pearson correlation between XAUUSD and DXY.

        Queries the latest 60 D1 candles for both symbols, aligns them by
        date, and computes a ``DXY_CORRELATION_WINDOW``-period rolling
        correlation.

        Returns a ``DXYCorrelation`` with ``available=False`` when DXY data
        is missing or insufficient.  **This method never raises** -- all
        errors are caught, logged, and returned as an unavailable result.
        """
        unavailable = DXYCorrelation(
            correlation=None,
            is_divergent=False,
            available=False,
            message="DXY data unavailable",
        )

        try:
            # Fetch DXY D1 candles
            dxy_stmt = (
                select(Candle)
                .where(Candle.symbol == DXY_SYMBOL, Candle.timeframe == "D1")
                .order_by(Candle.timestamp.desc())
                .limit(60)
            )
            dxy_result = await session.execute(dxy_stmt)
            dxy_candles = dxy_result.scalars().all()

            if len(dxy_candles) < DXY_CORRELATION_WINDOW + 5:
                logger.warning(
                    "Insufficient DXY candles ({}) for correlation window ({}+5)",
                    len(dxy_candles),
                    DXY_CORRELATION_WINDOW,
                )
                return unavailable

            # Fetch XAUUSD D1 candles
            gold_stmt = (
                select(Candle)
                .where(Candle.symbol == "XAUUSD", Candle.timeframe == "D1")
                .order_by(Candle.timestamp.desc())
                .limit(60)
            )
            gold_result = await session.execute(gold_stmt)
            gold_candles = gold_result.scalars().all()

            if len(gold_candles) < DXY_CORRELATION_WINDOW + 5:
                logger.warning(
                    "Insufficient XAUUSD D1 candles ({}) for correlation",
                    len(gold_candles),
                )
                return unavailable

            # Build DataFrames and align by date
            dxy_df = pd.DataFrame(
                [
                    {
                        "date": c.timestamp.date(),
                        "dxy_close": float(c.close),
                    }
                    for c in dxy_candles
                ]
            ).sort_values("date")

            gold_df = pd.DataFrame(
                [
                    {
                        "date": c.timestamp.date(),
                        "gold_close": float(c.close),
                    }
                    for c in gold_candles
                ]
            ).sort_values("date")

            merged = pd.merge(dxy_df, gold_df, on="date", how="inner")

            if len(merged) < DXY_CORRELATION_WINDOW + 5:
                logger.warning(
                    "Insufficient aligned data points ({}) after merge",
                    len(merged),
                )
                return unavailable

            # Rolling Pearson correlation
            rolling_corr = merged["gold_close"].rolling(
                DXY_CORRELATION_WINDOW
            ).corr(merged["dxy_close"])

            latest_corr = rolling_corr.dropna().iloc[-1] if not rolling_corr.dropna().empty else None

            if latest_corr is None:
                logger.warning("Rolling correlation produced no valid values")
                return unavailable

            latest_corr = float(latest_corr)
            is_divergent = latest_corr > DXY_DIVERGENCE_THRESHOLD

            msg = (
                f"Gold-DXY 30-period correlation: {latest_corr:.3f}"
                f" ({'DIVERGENT' if is_divergent else 'normal inverse'})"
            )
            logger.info(msg)

            return DXYCorrelation(
                correlation=latest_corr,
                is_divergent=is_divergent,
                available=True,
                message=msg,
            )

        except Exception:
            logger.opt(exception=True).warning(
                "DXY correlation computation failed -- degrading gracefully"
            )
            return unavailable

    # ------------------------------------------------------------------
    # Session volatility profiles (informational)
    # ------------------------------------------------------------------

    def get_session_volatility_profile(self, session_name: str) -> str:
        """Return a qualitative volatility description for *session_name*.

        This is purely informational metadata and is **not** used to
        suppress or modify signals (per CONTEXT.md: no session suppression).

        Args:
            session_name: One of ``asian``, ``london``, ``new_york``,
                          ``overlap``.

        Returns:
            Human-readable volatility description string.
        """
        return _VOLATILITY_PROFILES.get(
            session_name, f"Unknown session '{session_name}'"
        )
