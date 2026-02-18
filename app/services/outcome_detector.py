"""Outcome detection service for live XAUUSD signals.

Polls the current XAUUSD price via Twelve Data /price endpoint, evaluates
all active signals against SL/TP levels with spread accounting, records
outcomes to the database, and updates signal status.

SL always takes priority over TP when both could trigger in the same check
(conservative assumption, decision [03-01]).

Spread accounting (TRACK-05):
- BUY:  SL checked against bid (price), TP checked against bid (price)
- SELL: SL checked against ask (bid + spread), TP checked against bid (price)

Exports:
    OutcomeDetector -- main service class
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import httpx
from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

# HTTP 429 means we've hit the rate limit -- retrying just burns more quota
_RETRYABLE_ERRORS = (httpx.ConnectError,)

from app.models.outcome import Outcome
from app.models.signal import Signal
from app.services.performance_tracker import PerformanceTracker
from app.services.spread_model import SessionSpreadModel


class OutcomeDetector:
    """Detects outcomes for active XAUUSD signals by polling current price.

    Fetches the latest XAUUSD bid price from Twelve Data /price endpoint,
    evaluates all active signals for SL hit, TP1 hit, TP2 hit, or expiry,
    records outcomes to the database, and updates signal status.

    Attributes:
        PIP_VALUE: Price movement per pip for XAUUSD ($0.10).
        PRICE_ENDPOINT: Twelve Data REST endpoint for current price.
    """

    PIP_VALUE = 0.10
    PRICE_ENDPOINT = "https://api.twelvedata.com/price"
    CACHE_MAX_AGE_SECONDS = 300  # Fall back to cached price up to 5 min old

    # Class-level price cache (persists across instances since jobs.py
    # creates a new OutcomeDetector each cycle)
    _cached_price: float | None = None
    _cached_at: datetime | None = None

    def __init__(self, api_key: str) -> None:
        self.api_key = api_key
        self.spread_model = SessionSpreadModel()
        self.performance_tracker = PerformanceTracker()

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    async def check_outcomes(self, session: AsyncSession) -> list[Outcome]:
        """Check all active signals against current price for outcomes.

        Steps:
        1. Query active signals from the database.
        2. If none, return immediately (no API call).
        3. Fetch current XAUUSD price from Twelve Data.
        4. If price fetch fails, log warning and return empty.
        5. Get current spread from SessionSpreadModel.
        6. Evaluate each signal for SL/TP/expiry.
        7. Record outcomes, update signal status, commit.
        8. Return list of new Outcome objects.

        Args:
            session: Async SQLAlchemy session for DB operations.

        Returns:
            List of newly created Outcome objects.
        """
        # 1. Query active signals
        active_signals = await self._get_active_signals(session)
        if not active_signals:
            return []

        # 2. Fetch current price with cache fallback
        price = await self._get_price_with_cache()
        if price is None:
            return []

        # 3. Get current spread
        now = datetime.now(timezone.utc)
        spread = self.spread_model.get_spread(now)

        # 4. Evaluate each signal and record outcomes
        outcomes: list[Outcome] = []
        for signal in active_signals:
            result = self._evaluate_signal(signal, price, spread)
            if result is not None:
                exit_price = price
                outcome = self._record_outcome(
                    signal=signal,
                    result=result,
                    exit_price=exit_price,
                    now=now,
                )
                session.add(outcome)
                outcomes.append(outcome)

        if outcomes:
            await session.commit()

            # Recalculate rolling performance metrics for each affected strategy
            affected_strategy_ids = {
                signal.strategy_id
                for signal in active_signals
                if signal.status != "active"  # status updated by _record_outcome
            }
            for sid in affected_strategy_ids:
                try:
                    await self.performance_tracker.recalculate_for_strategy(session, sid)
                except Exception:
                    logger.exception(
                        "outcome_detector: failed to recalculate performance for strategy_id={}",
                        sid,
                    )

        return outcomes

    # ------------------------------------------------------------------
    # Database queries
    # ------------------------------------------------------------------

    async def _get_active_signals(self, session: AsyncSession) -> list[Signal]:
        """Query all signals with status='active'."""
        stmt = select(Signal).where(Signal.status == "active")
        result = await session.execute(stmt)
        return list(result.scalars().all())

    # ------------------------------------------------------------------
    # Price fetching with cache fallback
    # ------------------------------------------------------------------

    async def _get_price_with_cache(self) -> float | None:
        """Get current price, falling back to cached price on API failure.

        On success, updates the class-level cache. On failure, returns the
        cached price if it's less than CACHE_MAX_AGE_SECONDS old. This means
        the job almost never fails — brief API outages are invisible.
        """
        try:
            price = await self._fetch_current_price()
        except Exception:
            logger.warning("outcome_detector: price fetch failed after retries")
            price = None

        if price is not None:
            OutcomeDetector._cached_price = price
            OutcomeDetector._cached_at = datetime.now(timezone.utc)
            return price

        # Fall back to cached price if fresh enough
        if (
            OutcomeDetector._cached_price is not None
            and OutcomeDetector._cached_at is not None
        ):
            age = (datetime.now(timezone.utc) - OutcomeDetector._cached_at).total_seconds()
            if age <= self.CACHE_MAX_AGE_SECONDS:
                logger.info(
                    "outcome_detector: using cached price {} (age={:.0f}s)",
                    OutcomeDetector._cached_price,
                    age,
                )
                return OutcomeDetector._cached_price

        logger.warning("outcome_detector: no price available (API down, no cache)")
        return None

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type(_RETRYABLE_ERRORS),
    )
    async def _fetch_current_price(self) -> float | None:
        """Fetch latest XAUUSD bid price from Twelve Data /price endpoint.

        Uses httpx async client with tenacity retry (3 attempts, exponential
        backoff for connection errors only). HTTP errors like 429 rate limits
        are not retried to avoid compounding quota usage.

        Endpoint: GET /price?symbol=XAU/USD&apikey=...
        Response: {"price": "2650.45"}
        """
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(
                    self.PRICE_ENDPOINT,
                    params={"symbol": "XAU/USD", "apikey": self.api_key},
                )
                response.raise_for_status()
                data = response.json()

                if "price" not in data:
                    logger.warning(
                        "outcome_detector: unexpected response from /price: {}",
                        data,
                    )
                    return None

                return float(data["price"])
        except httpx.ConnectError:
            # Let tenacity retry handle connection failures
            raise
        except httpx.HTTPStatusError as exc:
            logger.warning(
                "outcome_detector: price API returned HTTP {status}",
                status=exc.response.status_code,
            )
            return None
        except Exception:
            logger.exception("outcome_detector: price fetch error")
            return None

    # ------------------------------------------------------------------
    # Pure evaluation logic
    # ------------------------------------------------------------------

    def _evaluate_signal(
        self, signal: Signal, price: float, spread: Decimal
    ) -> str | None:
        """Evaluate if current price triggers any outcome for this signal.

        Priority order (conservative, decision [03-01]):
        1. Check expiry (time-based) -- always first
        2. Check SL -- ALWAYS takes priority over TP
        3. Check TP2 -- if price jumped past TP1 to TP2, record tp2_hit
        4. Check TP1

        Spread accounting (TRACK-05):
        - BUY: SL checked against bid (price), TP checked against bid (price)
        - SELL: SL checked against ask (price + spread), TP checked against bid (price)

        Args:
            signal: Signal ORM object to evaluate.
            price: Current bid price as float.
            spread: Current spread in price units as Decimal.

        Returns:
            Result string ('sl_hit', 'tp1_hit', 'tp2_hit', 'expired') or None.
        """
        is_buy = signal.direction == "BUY"
        sl = float(signal.stop_loss)
        tp1 = float(signal.take_profit_1)
        tp2 = float(signal.take_profit_2)
        spread_f = float(spread)

        # 1. Check expiry
        if signal.expires_at is not None:
            now = datetime.now(timezone.utc)
            expires = signal.expires_at
            # Handle naive datetimes by assuming UTC
            if expires.tzinfo is None:
                expires = expires.replace(tzinfo=timezone.utc)
            if now >= expires:
                return "expired"

        # 2. Check SL (priority over TP -- decision [03-01])
        if is_buy:
            # BUY: SL hit when bid drops to or below SL
            sl_hit = price <= sl
        else:
            # SELL: SL hit when ask (bid + spread) rises to or above SL
            ask = price + spread_f
            sl_hit = ask >= sl

        if sl_hit:
            return "sl_hit"

        # 3. Check TP2 (priority over TP1 -- catches price jumps)
        if is_buy:
            tp2_hit = price >= tp2
        else:
            tp2_hit = price <= tp2

        if tp2_hit:
            return "tp2_hit"

        # 4. Check TP1
        if is_buy:
            tp1_hit = price >= tp1
        else:
            tp1_hit = price <= tp1

        if tp1_hit:
            return "tp1_hit"

        return None

    # ------------------------------------------------------------------
    # PnL and duration calculations
    # ------------------------------------------------------------------

    def _calculate_pnl(self, signal: Signal, exit_price: float) -> Decimal:
        """Calculate PnL in pips for a signal.

        BUY:  (exit_price - entry_price) / PIP_VALUE
        SELL: (entry_price - exit_price) / PIP_VALUE

        Args:
            signal: Signal ORM object.
            exit_price: Price at which the outcome occurred.

        Returns:
            PnL in pips as Decimal, rounded to 2 decimal places.
        """
        entry = float(signal.entry_price)
        if signal.direction == "BUY":
            pnl = (exit_price - entry) / self.PIP_VALUE
        else:
            pnl = (entry - exit_price) / self.PIP_VALUE
        return Decimal(str(round(pnl, 2)))

    def _calculate_duration(self, signal: Signal, now: datetime) -> int:
        """Calculate trade duration in minutes.

        Args:
            signal: Signal ORM object with created_at.
            now: Current UTC datetime.

        Returns:
            Duration in whole minutes (truncated).
        """
        created = signal.created_at
        # Handle naive datetimes
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        return int((now - created).total_seconds() // 60)

    # ------------------------------------------------------------------
    # Outcome recording
    # ------------------------------------------------------------------

    def _record_outcome(
        self,
        signal: Signal,
        result: str,
        exit_price: float,
        now: datetime,
    ) -> Outcome:
        """Create an Outcome record and update the signal status.

        Args:
            signal: Signal ORM object to update.
            result: Outcome result string (sl_hit, tp1_hit, tp2_hit, expired).
            exit_price: Price at which outcome occurred.
            now: Current UTC datetime.

        Returns:
            New Outcome ORM object (not yet added to session).
        """
        pnl_pips = self._calculate_pnl(signal, exit_price)
        duration = self._calculate_duration(signal, now)

        # Update signal status
        signal.status = result

        outcome = Outcome(
            signal_id=signal.id,
            result=result,
            exit_price=Decimal(str(round(exit_price, 2))),
            pnl_pips=pnl_pips,
            duration_minutes=duration,
        )

        logger.info(
            "outcome_detector: signal_id={} result={} exit={} pnl={}pips duration={}min",
            signal.id,
            result,
            exit_price,
            pnl_pips,
            duration,
        )

        return outcome
