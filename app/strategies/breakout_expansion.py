"""Breakout Expansion strategy (STRAT-03).

Detects consolidation-range breakouts on XAUUSD H1: identifies periods of
low volatility (ATR compression) followed by decisive price expansion beyond
the consolidation range, producing CandidateSignal outputs.
"""

from datetime import datetime
from decimal import Decimal
from math import isnan

import numpy as np
import pandas as pd

from typing import ClassVar

from app.strategies.base import BaseStrategy, CandidateSignal, Direction
from app.strategies.helpers import (
    compute_atr,
    get_active_sessions,
    is_in_session,
)


class BreakoutExpansionStrategy(BaseStrategy):
    """Detects consolidation-range breakouts on XAUUSD H1.

    A breakout expansion setup occurs when:
    1. Volatility contracts (ATR < 0.5 * ATR_MA_50) for at least 10 bars
    2. A consolidation range is established (range_high / range_low)
    3. Price breaks decisively above range_high (bullish) or below
       range_low (bearish)

    Optionally confirms with volume expansion on the breakout bar.
    """

    name = "breakout_expansion"
    required_timeframes = ["H1"]
    min_candles = 70

    # ------------------------------------------------------------------
    # Default parameters (overridable via constructor)
    # ------------------------------------------------------------------
    DEFAULT_PARAMS: ClassVar[dict[str, float]] = {
        "ATR_LENGTH": 14,
        "ATR_MA_LENGTH": 50,
        "ATR_COMPRESSION": 0.5,
        "MIN_CONSOL_BARS": 10,
        "VOLUME_MULT": 1.5,
        "WIDE_RANGE_ATR_MULT": 3.0,
        "BREAKOUT_BODY_ATR": 1.5,
        "BASE_CONFIDENCE": 50,
        "LONDON_OPEN_START": 7,
        "LONDON_OPEN_END": 9,
    }

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def analyze(self, candles: pd.DataFrame) -> list[CandidateSignal]:
        """Scan *candles* for consolidation breakout setups.

        Args:
            candles: DataFrame with columns [timestamp, open, high, low,
                     close] and optionally [volume].  Values are floats.

        Returns:
            List of CandidateSignal instances (may be empty).

        Raises:
            InsufficientDataError: If len(candles) < min_candles.
            ValueError: If required columns are missing.
        """
        self.validate_data(candles)
        candles = candles.copy()

        # --- indicators ---
        atr = compute_atr(
            candles["high"], candles["low"], candles["close"],
            length=int(self.params["ATR_LENGTH"]),
        )
        atr_ma = atr.rolling(window=int(self.params["ATR_MA_LENGTH"])).mean()

        opens = candles["open"].values
        highs = candles["high"].values
        lows = candles["low"].values
        closes = candles["close"].values
        timestamps = candles["timestamp"].values

        # Volume (may be absent or all zero)
        has_volume = (
            "volume" in candles.columns
            and not candles["volume"].fillna(0).eq(0).all()
        )
        volumes = candles["volume"].values if has_volume else None

        n = len(candles)
        signals: list[CandidateSignal] = []

        # --- detect consolidation ranges and breakouts ---
        # Track consecutive compression bars
        consol_start: int | None = None
        in_consolidation = False

        for i in range(self.min_candles, n):
            atr_val = float(atr.iloc[i])
            atr_ma_val = float(atr_ma.iloc[i])

            if isnan(atr_val) or isnan(atr_ma_val) or atr_ma_val <= 0:
                consol_start = None
                in_consolidation = False
                continue

            is_compressed = atr_val < self.params["ATR_COMPRESSION"] * atr_ma_val

            if is_compressed:
                if consol_start is None:
                    consol_start = i
                in_consolidation = True
            else:
                # Bar is NOT compressed -- check if we just exited a consolidation
                if in_consolidation and consol_start is not None:
                    consol_length = i - consol_start
                    if consol_length >= int(self.params["MIN_CONSOL_BARS"]):
                        # We have a valid consolidation range
                        signal = self._check_breakout(
                            i, consol_start, consol_length,
                            atr_val, atr_ma_val,
                            opens, highs, lows, closes, timestamps,
                            volumes, has_volume,
                        )
                        if signal is not None:
                            signals.append(signal)

                consol_start = None
                in_consolidation = False

        return signals

    # ------------------------------------------------------------------
    # Breakout detection
    # ------------------------------------------------------------------
    def _check_breakout(
        self,
        i: int,
        consol_start: int,
        consol_length: int,
        atr_val: float,
        atr_ma_val: float,
        opens: np.ndarray,
        highs: np.ndarray,
        lows: np.ndarray,
        closes: np.ndarray,
        timestamps: np.ndarray,
        volumes: np.ndarray | None,
        has_volume: bool,
    ) -> CandidateSignal | None:
        """Check if bar *i* is a breakout from the consolidation range."""
        # Consolidation range
        consol_highs = highs[consol_start:i]
        consol_lows = lows[consol_start:i]

        range_high = float(np.max(consol_highs))
        range_low = float(np.min(consol_lows))
        range_height = range_high - range_low

        if range_height <= 0:
            return None

        close_val = float(closes[i])
        open_val = float(opens[i])

        # Detect breakout direction
        bullish_breakout = close_val > range_high
        bearish_breakout = close_val < range_low

        if not bullish_breakout and not bearish_breakout:
            return None

        # --- optional volume confirmation ---
        volume_confirms = False
        if has_volume and volumes is not None:
            consol_volumes = volumes[consol_start:i]
            avg_vol = float(np.mean(consol_volumes))
            if avg_vol > 0:
                breakout_vol = float(volumes[i])
                if breakout_vol > self.params["VOLUME_MULT"] * avg_vol:
                    volume_confirms = True

        # --- timestamp and session info ---
        ts = pd.Timestamp(timestamps[i]).to_pydatetime()
        active_sessions = get_active_sessions(ts)
        session = active_sessions[0] if active_sessions else None

        # London open bonus check (07:00-09:00 UTC)
        london_open = int(self.params["LONDON_OPEN_START"]) <= ts.hour < int(self.params["LONDON_OPEN_END"])

        # Breakout candle body size
        candle_body = abs(close_val - open_val)

        # --- build signal ---
        if bullish_breakout:
            return self._build_bullish_signal(
                i, close_val, range_high, range_low, range_height,
                atr_val, consol_length, candle_body,
                volume_confirms, london_open,
                timestamps, session, ts,
            )
        else:
            return self._build_bearish_signal(
                i, close_val, range_high, range_low, range_height,
                atr_val, consol_length, candle_body,
                volume_confirms, london_open,
                timestamps, session, ts,
            )

    # ------------------------------------------------------------------
    # Signal builders
    # ------------------------------------------------------------------
    def _build_bullish_signal(
        self,
        i: int,
        entry: float,
        range_high: float,
        range_low: float,
        range_height: float,
        atr_val: float,
        consol_length: int,
        candle_body: float,
        volume_confirms: bool,
        london_open: bool,
        timestamps: np.ndarray,
        session: str | None,
        ts: datetime,
    ) -> CandidateSignal:
        """Build a bullish breakout signal."""
        # Stop loss: range_low (or midpoint if range is very wide)
        if range_height > self.params["WIDE_RANGE_ATR_MULT"] * atr_val:
            sl = (range_high + range_low) / 2.0  # midpoint
        else:
            sl = range_low

        risk_dist = abs(entry - sl)
        if risk_dist == 0:
            risk_dist = atr_val  # fallback

        # TP1: 1.0 * range_height from entry
        tp1 = entry + 1.0 * range_height
        # TP2: 2.0 * range_height from entry
        tp2 = entry + 2.0 * range_height

        rr = round((tp1 - entry) / risk_dist, 2) if risk_dist > 0 else 0.0

        confidence = self._compute_confidence(
            consol_length=consol_length,
            candle_body=candle_body,
            atr_val=atr_val,
            volume_confirms=volume_confirms,
            london_open=london_open,
        )

        reasoning = (
            f"Bullish breakout from {consol_length}-bar consolidation range "
            f"({range_low:.2f}-{range_high:.2f}). "
            f"ATR expansion confirms volatility breakout. "
            f"Entry at {entry:.2f}, SL at {sl:.2f}."
        )

        return CandidateSignal(
            strategy_name=self.name,
            symbol="XAUUSD",
            timeframe=self.required_timeframes[0],
            direction=Direction.BUY,
            entry_price=Decimal(str(round(entry, 2))),
            stop_loss=Decimal(str(round(sl, 2))),
            take_profit_1=Decimal(str(round(tp1, 2))),
            take_profit_2=Decimal(str(round(tp2, 2))),
            risk_reward=Decimal(str(round(rr, 2))),
            confidence=Decimal(str(round(confidence, 2))),
            reasoning=reasoning,
            timestamp=ts,
            session=session,
        )

    def _build_bearish_signal(
        self,
        i: int,
        entry: float,
        range_high: float,
        range_low: float,
        range_height: float,
        atr_val: float,
        consol_length: int,
        candle_body: float,
        volume_confirms: bool,
        london_open: bool,
        timestamps: np.ndarray,
        session: str | None,
        ts: datetime,
    ) -> CandidateSignal:
        """Build a bearish breakout signal."""
        # Stop loss: range_high (or midpoint if range is very wide)
        if range_height > self.params["WIDE_RANGE_ATR_MULT"] * atr_val:
            sl = (range_high + range_low) / 2.0  # midpoint
        else:
            sl = range_high

        risk_dist = abs(sl - entry)
        if risk_dist == 0:
            risk_dist = atr_val  # fallback

        # TP1: 1.0 * range_height below entry
        tp1 = entry - 1.0 * range_height
        # TP2: 2.0 * range_height below entry
        tp2 = entry - 2.0 * range_height

        rr = round((entry - tp1) / risk_dist, 2) if risk_dist > 0 else 0.0

        confidence = self._compute_confidence(
            consol_length=consol_length,
            candle_body=candle_body,
            atr_val=atr_val,
            volume_confirms=volume_confirms,
            london_open=london_open,
        )

        reasoning = (
            f"Bearish breakout from {consol_length}-bar consolidation range "
            f"({range_low:.2f}-{range_high:.2f}). "
            f"ATR expansion confirms volatility breakout. "
            f"Entry at {entry:.2f}, SL at {sl:.2f}."
        )

        return CandidateSignal(
            strategy_name=self.name,
            symbol="XAUUSD",
            timeframe=self.required_timeframes[0],
            direction=Direction.SELL,
            entry_price=Decimal(str(round(entry, 2))),
            stop_loss=Decimal(str(round(sl, 2))),
            take_profit_1=Decimal(str(round(tp1, 2))),
            take_profit_2=Decimal(str(round(tp2, 2))),
            risk_reward=Decimal(str(round(rr, 2))),
            confidence=Decimal(str(round(confidence, 2))),
            reasoning=reasoning,
            timestamp=ts,
            session=session,
        )

    # ------------------------------------------------------------------
    # Confidence scoring
    # ------------------------------------------------------------------
    def _compute_confidence(
        self,
        consol_length: int,
        candle_body: float,
        atr_val: float,
        volume_confirms: bool,
        london_open: bool,
    ) -> float:
        """Additive confidence score (0-100).

        Base 50, with bonuses for:
          +10  consolidation lasted > 20 bars (stronger range)
          +10  breakout candle body > 1.5 * ATR (strong momentum)
          +10  volume confirms breakout
          +10  London open session (07:00-09:00 UTC)
        """
        score = float(self.params["BASE_CONFIDENCE"])

        if consol_length > 20:
            score += 10

        if atr_val > 0 and candle_body > self.params["BREAKOUT_BODY_ATR"] * atr_val:
            score += 10

        if volume_confirms:
            score += 10

        if london_open:
            score += 10

        return min(score, 100.0)
