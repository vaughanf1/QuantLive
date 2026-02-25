"""EMA Momentum strategy (STRAT-04).

Detects strong trending moves on XAUUSD H1: fires when price is above both
EMA-21 and EMA-50, both EMAs are rising, and a strong bullish/bearish candle
confirms momentum. Designed for trending markets where pullback-based
strategies produce no signals.
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
    compute_ema,
    detect_swing_highs,
    detect_swing_lows,
    get_active_sessions,
    is_in_session,
)


class EMAMomentumStrategy(BaseStrategy):
    """Detects strong EMA-aligned momentum moves on XAUUSD H1.

    A momentum setup occurs when:
    1. EMA-21 > EMA-50 > EMA-200 (bullish) or reverse (bearish)
    2. Both EMA-21 and EMA-50 are rising (slope check over last 5 bars)
    3. A strong candle closes in the trend direction (body > 0.6 * ATR)
    4. Price is above EMA-21 (bullish) or below EMA-21 (bearish)

    No pullback required -- this fires in trending markets.
    SL below recent swing low (bullish) or above recent swing high (bearish),
    capped at 150 pips.
    """

    name = "ema_momentum"
    required_timeframes = ["H1"]
    min_candles = 200

    # ------------------------------------------------------------------
    # Default parameters (overridable via constructor)
    # ------------------------------------------------------------------
    DEFAULT_PARAMS: ClassVar[dict[str, float]] = {
        "EMA_FAST": 21,
        "EMA_MID": 50,
        "EMA_SLOW": 200,
        "ATR_LENGTH": 14,
        "BODY_ATR_MULT": 0.6,          # Min candle body as fraction of ATR
        "SL_ATR_MULT": 1.0,            # SL padding below swing low
        "TP1_RR": 1.5,                 # TP1 risk:reward
        "TP2_RR": 3.0,                 # TP2 risk:reward
        "EMA_SLOPE_BARS": 5,           # Bars to check EMA slope
        "SWING_ORDER": 5,              # Swing detection lookback
        "SWING_LOOKBACK": 20,          # Bars to search for swing low/high
        "BASE_CONFIDENCE": 50,
        "MAX_SL_PIPS": 150.0,          # Hard cap on SL distance in pips
        "PIP_VALUE": 0.10,             # XAUUSD pip value
    }

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def analyze(self, candles: pd.DataFrame) -> list[CandidateSignal]:
        """Scan *candles* for EMA momentum setups.

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
        ema_fast = compute_ema(candles["close"], int(self.params["EMA_FAST"]))
        ema_mid = compute_ema(candles["close"], int(self.params["EMA_MID"]))
        ema_slow = compute_ema(candles["close"], int(self.params["EMA_SLOW"]))
        atr = compute_atr(
            candles["high"], candles["low"], candles["close"],
            length=int(self.params["ATR_LENGTH"]),
        )

        # Swing detection for SL placement
        swing_high_indices = detect_swing_highs(
            candles["high"], order=int(self.params["SWING_ORDER"]),
        )
        swing_low_indices = detect_swing_lows(
            candles["low"], order=int(self.params["SWING_ORDER"]),
        )

        opens = candles["open"].values
        highs = candles["high"].values
        lows = candles["low"].values
        closes = candles["close"].values
        timestamps = candles["timestamp"].values

        n = len(candles)
        signals: list[CandidateSignal] = []
        slope_bars = int(self.params["EMA_SLOPE_BARS"])

        for i in range(self.min_candles, n):
            atr_val = float(atr.iloc[i])
            if isnan(atr_val) or atr_val <= 0:
                continue

            ema_f = float(ema_fast.iloc[i])
            ema_m = float(ema_mid.iloc[i])
            ema_s = float(ema_slow.iloc[i])

            if isnan(ema_f) or isnan(ema_m) or isnan(ema_s):
                continue

            # --- session filter: London or New York ---
            ts = pd.Timestamp(timestamps[i]).to_pydatetime()
            if not (is_in_session(ts, "london") or is_in_session(ts, "new_york")):
                continue

            close_val = float(closes[i])
            open_val = float(opens[i])

            # Candle body strength
            candle_body = abs(close_val - open_val)
            if candle_body < self.params["BODY_ATR_MULT"] * atr_val:
                continue

            # --- Check EMA alignment and slope ---
            # Need historical EMA values for slope check
            if i < slope_bars:
                continue

            ema_f_prev = float(ema_fast.iloc[i - slope_bars])
            ema_m_prev = float(ema_mid.iloc[i - slope_bars])

            if isnan(ema_f_prev) or isnan(ema_m_prev):
                continue

            # Bullish: EMA-21 > EMA-50 > EMA-200, both rising, bullish candle
            bullish = (
                ema_f > ema_m > ema_s
                and ema_f > ema_f_prev  # EMA-21 rising
                and ema_m > ema_m_prev  # EMA-50 rising
                and close_val > open_val  # green candle
                and close_val > ema_f  # price above EMA-21
            )

            # Bearish: EMA-21 < EMA-50 < EMA-200, both falling, bearish candle
            bearish = (
                ema_f < ema_m < ema_s
                and ema_f < ema_f_prev  # EMA-21 falling
                and ema_m < ema_m_prev  # EMA-50 falling
                and close_val < open_val  # red candle
                and close_val < ema_f  # price below EMA-21
            )

            if bullish:
                signal = self._build_bullish_signal(
                    i, close_val, atr_val,
                    ema_f, ema_m, ema_s,
                    highs, lows, timestamps,
                    swing_low_indices, swing_high_indices,
                    ts,
                )
                if signal is not None:
                    signals.append(signal)

            elif bearish:
                signal = self._build_bearish_signal(
                    i, close_val, atr_val,
                    ema_f, ema_m, ema_s,
                    highs, lows, timestamps,
                    swing_high_indices, swing_low_indices,
                    ts,
                )
                if signal is not None:
                    signals.append(signal)

        return signals

    # ------------------------------------------------------------------
    # Signal builders
    # ------------------------------------------------------------------
    def _build_bullish_signal(
        self,
        i: int,
        entry: float,
        atr_val: float,
        ema_f: float,
        ema_m: float,
        ema_s: float,
        highs: np.ndarray,
        lows: np.ndarray,
        timestamps: np.ndarray,
        swing_low_indices: np.ndarray,
        swing_high_indices: np.ndarray,
        ts: datetime,
    ) -> CandidateSignal | None:
        """Build a bullish EMA momentum signal."""
        lookback = int(self.params["SWING_LOOKBACK"])
        max_sl_pips = self.params["MAX_SL_PIPS"]
        pip_value = self.params["PIP_VALUE"]

        # SL: recent swing low - padding
        sl = self._find_recent_swing_low(
            i, lows, swing_low_indices, lookback
        )
        if sl is None:
            # Fallback: lowest low in lookback
            start = max(0, i - lookback)
            sl = float(np.min(lows[start:i + 1]))

        sl -= self.params["SL_ATR_MULT"] * atr_val

        # Cap SL distance
        risk_dist = abs(entry - sl)
        sl_pips = risk_dist / pip_value
        if sl_pips > max_sl_pips:
            sl = entry - max_sl_pips * pip_value
            risk_dist = abs(entry - sl)

        if risk_dist <= 0:
            return None

        tp1 = entry + self.params["TP1_RR"] * risk_dist
        tp2 = entry + self.params["TP2_RR"] * risk_dist

        rr = round((tp1 - entry) / risk_dist, 2)

        confidence = self._compute_confidence(
            direction=Direction.BUY,
            entry=entry,
            ema_f=ema_f,
            ema_m=ema_m,
            ema_s=ema_s,
            atr_val=atr_val,
            ts=ts,
        )

        active_sessions = get_active_sessions(ts)
        session = active_sessions[0] if active_sessions else None

        reasoning = (
            f"Bullish EMA momentum: EMA-21 ({ema_f:.2f}) > EMA-50 ({ema_m:.2f}) "
            f"> EMA-200 ({ema_s:.2f}), all rising. Strong bullish candle at "
            f"{entry:.2f}. SL at {sl:.2f}, TP1 at {tp1:.2f}."
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
        atr_val: float,
        ema_f: float,
        ema_m: float,
        ema_s: float,
        highs: np.ndarray,
        lows: np.ndarray,
        timestamps: np.ndarray,
        swing_high_indices: np.ndarray,
        swing_low_indices: np.ndarray,
        ts: datetime,
    ) -> CandidateSignal | None:
        """Build a bearish EMA momentum signal."""
        lookback = int(self.params["SWING_LOOKBACK"])
        max_sl_pips = self.params["MAX_SL_PIPS"]
        pip_value = self.params["PIP_VALUE"]

        # SL: recent swing high + padding
        sl = self._find_recent_swing_high(
            i, highs, swing_high_indices, lookback
        )
        if sl is None:
            # Fallback: highest high in lookback
            start = max(0, i - lookback)
            sl = float(np.max(highs[start:i + 1]))

        sl += self.params["SL_ATR_MULT"] * atr_val

        # Cap SL distance
        risk_dist = abs(sl - entry)
        sl_pips = risk_dist / pip_value
        if sl_pips > max_sl_pips:
            sl = entry + max_sl_pips * pip_value
            risk_dist = abs(sl - entry)

        if risk_dist <= 0:
            return None

        tp1 = entry - self.params["TP1_RR"] * risk_dist
        tp2 = entry - self.params["TP2_RR"] * risk_dist

        rr = round((entry - tp1) / risk_dist, 2)

        confidence = self._compute_confidence(
            direction=Direction.SELL,
            entry=entry,
            ema_f=ema_f,
            ema_m=ema_m,
            ema_s=ema_s,
            atr_val=atr_val,
            ts=ts,
        )

        active_sessions = get_active_sessions(ts)
        session = active_sessions[0] if active_sessions else None

        reasoning = (
            f"Bearish EMA momentum: EMA-21 ({ema_f:.2f}) < EMA-50 ({ema_m:.2f}) "
            f"< EMA-200 ({ema_s:.2f}), all falling. Strong bearish candle at "
            f"{entry:.2f}. SL at {sl:.2f}, TP1 at {tp1:.2f}."
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
    # Swing helpers
    # ------------------------------------------------------------------
    def _find_recent_swing_low(
        self,
        i: int,
        lows: np.ndarray,
        swing_low_indices: np.ndarray,
        lookback: int,
    ) -> float | None:
        """Find the most recent swing low within lookback bars before bar i."""
        start = max(0, i - lookback)
        candidates = [
            float(lows[idx])
            for idx in swing_low_indices
            if start <= idx < i
        ]
        return min(candidates) if candidates else None

    def _find_recent_swing_high(
        self,
        i: int,
        highs: np.ndarray,
        swing_high_indices: np.ndarray,
        lookback: int,
    ) -> float | None:
        """Find the most recent swing high within lookback bars before bar i."""
        start = max(0, i - lookback)
        candidates = [
            float(highs[idx])
            for idx in swing_high_indices
            if start <= idx < i
        ]
        return max(candidates) if candidates else None

    # ------------------------------------------------------------------
    # Confidence scoring
    # ------------------------------------------------------------------
    def _compute_confidence(
        self,
        direction: Direction,
        entry: float,
        ema_f: float,
        ema_m: float,
        ema_s: float,
        atr_val: float,
        ts: datetime,
    ) -> float:
        """Additive confidence score (0-100).

        Base 50, with bonuses for:
          +10  strong EMA separation (EMA-21/50 spread > 1.0 * ATR)
          +10  price well above/below EMA-21 (distance > 0.3 * ATR)
          +10  London/NY overlap session
          +10  all three EMAs strongly aligned (EMA-50/200 spread > 2.0 * ATR)
        """
        score = float(self.params["BASE_CONFIDENCE"])

        # Strong EMA-21/50 separation
        ema_fast_mid_spread = abs(ema_f - ema_m)
        if ema_fast_mid_spread > 1.0 * atr_val:
            score += 10

        # Price distance from EMA-21
        price_ema_dist = abs(entry - ema_f)
        if price_ema_dist > 0.3 * atr_val:
            score += 10

        # Overlap session bonus
        if is_in_session(ts, "overlap"):
            score += 10

        # All three EMAs strongly aligned
        ema_mid_slow_spread = abs(ema_m - ema_s)
        if ema_mid_slow_spread > 2.0 * atr_val:
            score += 10

        return min(score, 100.0)
