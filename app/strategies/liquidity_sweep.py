"""Liquidity Sweep Reversal strategy (STRAT-01).

Detects stop hunts below/above key swing levels, waits for market structure
shift confirmation, and produces CandidateSignal outputs.  This is the first
concrete strategy implementation validating the BaseStrategy interface.
"""

from datetime import datetime
from decimal import Decimal
from math import isnan

import numpy as np
import pandas as pd

from app.strategies.base import BaseStrategy, CandidateSignal, Direction
from app.strategies.helpers import (
    compute_atr,
    detect_swing_highs,
    detect_swing_lows,
    get_active_sessions,
    is_in_session,
)


class LiquiditySweepStrategy(BaseStrategy):
    """Detects liquidity sweep reversals on XAUUSD H1.

    A liquidity sweep occurs when price wicks beyond a key swing level
    (sweeping stop-loss orders) then reverses, signalling institutional
    accumulation/distribution.

    Confirmation: the bar(s) immediately following the sweep must show
    momentum back inside the range (close above sweep high for bullish,
    close below sweep low for bearish).
    """

    name = "liquidity_sweep"
    required_timeframes = ["H1"]
    min_candles = 100

    # ------------------------------------------------------------------
    # Tuning constants
    # ------------------------------------------------------------------
    _SWING_ORDER = 5          # argrelextrema order for swing detection
    _ATR_LENGTH = 14          # ATR lookback period
    _LOOKBACK = 50            # how far back to search for swing levels
    _CONFIRM_BARS = 3         # bars after sweep to look for confirmation
    _SL_ATR_MULT = 0.5        # SL = wick extreme +/- SL_ATR_MULT * ATR
    _TP1_RR = 1.5             # TP1 risk-reward multiple
    _TP2_RR = 3.0             # TP2 risk-reward multiple
    _BASE_CONFIDENCE = 50     # starting confidence score

    # -----------------------------------------------------------------
    # Public API
    # -----------------------------------------------------------------
    def analyze(self, candles: pd.DataFrame) -> list[CandidateSignal]:
        """Scan *candles* for liquidity sweep setups.

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
        atr = compute_atr(candles["high"], candles["low"], candles["close"],
                          length=self._ATR_LENGTH)

        # --- swing structure ---
        swing_high_indices = detect_swing_highs(candles["high"],
                                                order=self._SWING_ORDER)
        swing_low_indices = detect_swing_lows(candles["low"],
                                              order=self._SWING_ORDER)

        highs = candles["high"].values
        lows = candles["low"].values
        closes = candles["close"].values
        timestamps = candles["timestamp"].values

        n = len(candles)
        signals: list[CandidateSignal] = []

        # Start scanning after warmup period
        scan_start = max(self.min_candles, 20)

        for i in range(scan_start, n):
            # Skip if ATR is not ready
            atr_val = atr.iloc[i]
            if isnan(atr_val) or atr_val <= 0:
                continue

            # --- session filter on the sweep candle ---
            ts = pd.Timestamp(timestamps[i]).to_pydatetime()
            if not (is_in_session(ts, "london") or
                    is_in_session(ts, "new_york")):
                continue

            # Collect recent swing lows & highs within lookback window
            recent_sl = swing_low_indices[
                (swing_low_indices >= i - self._LOOKBACK) &
                (swing_low_indices < i)
            ]
            recent_sh = swing_high_indices[
                (swing_high_indices >= i - self._LOOKBACK) &
                (swing_high_indices < i)
            ]

            # --- Bullish sweep detection ---
            signal = self._check_bullish_sweep(
                i, n, recent_sl, lows, highs, closes, timestamps, atr_val, ts
            )
            if signal is not None:
                signals.append(signal)
                continue  # one signal per bar

            # --- Bearish sweep detection ---
            signal = self._check_bearish_sweep(
                i, n, recent_sh, lows, highs, closes, timestamps, atr_val, ts
            )
            if signal is not None:
                signals.append(signal)

        return signals

    # -----------------------------------------------------------------
    # Sweep detection helpers
    # -----------------------------------------------------------------
    def _check_bullish_sweep(
        self,
        i: int,
        n: int,
        recent_sl: np.ndarray,
        lows: np.ndarray,
        highs: np.ndarray,
        closes: np.ndarray,
        timestamps: np.ndarray,
        atr_val: float,
        sweep_ts: datetime,
    ) -> CandidateSignal | None:
        """Check for a bullish liquidity sweep at bar *i*.

        Bullish: candle wicks below a recent swing low but closes above it.
        """
        if len(recent_sl) == 0:
            return None

        # Check each recent swing low
        swept_levels: list[float] = []
        sweep_level: float | None = None

        for sl_idx in recent_sl:
            sl_level = float(lows[sl_idx])
            # Wick below swing low, close above
            if float(lows[i]) < sl_level <= float(closes[i]):
                swept_levels.append(sl_level)
                if sweep_level is None or sl_level < float(lows[i]):
                    sweep_level = sl_level

        if not swept_levels:
            return None

        # Use the lowest swept level for reference
        sweep_level = min(swept_levels)

        # --- Confirmation: one of next CONFIRM_BARS candles closes above
        #     the sweep candle's high ---
        confirm_idx: int | None = None
        sweep_high = float(highs[i])
        for j in range(i + 1, min(i + 1 + self._CONFIRM_BARS, n)):
            if float(closes[j]) > sweep_high:
                confirm_idx = j
                break

        if confirm_idx is None:
            return None

        # --- Build signal ---
        entry = float(closes[confirm_idx])
        sl = float(lows[i]) - self._SL_ATR_MULT * atr_val
        risk_dist = abs(entry - sl)
        if risk_dist == 0:
            return None

        tp1 = entry + self._TP1_RR * risk_dist
        tp2 = entry + self._TP2_RR * risk_dist
        rr = self._TP1_RR  # by construction

        confidence = self._compute_confidence(
            sweep_wick=abs(float(lows[i]) - sweep_level),
            atr_val=atr_val,
            confirm_close=float(closes[confirm_idx]),
            confirm_high=float(highs[confirm_idx]),
            confirm_low=float(lows[confirm_idx]),
            direction=Direction.BUY,
            sweep_ts=sweep_ts,
            num_swept=len(swept_levels),
        )

        confirm_ts = pd.Timestamp(timestamps[confirm_idx]).to_pydatetime()
        active_sessions = get_active_sessions(confirm_ts)
        session = active_sessions[0] if active_sessions else None

        reasoning = (
            f"Bullish liquidity sweep below swing low at {sweep_level:.2f}, "
            f"confirmed by structure shift. "
            f"Entry at {entry:.2f}, SL below sweep wick at {sl:.2f}."
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
            timestamp=confirm_ts,
            session=session,
        )

    def _check_bearish_sweep(
        self,
        i: int,
        n: int,
        recent_sh: np.ndarray,
        lows: np.ndarray,
        highs: np.ndarray,
        closes: np.ndarray,
        timestamps: np.ndarray,
        atr_val: float,
        sweep_ts: datetime,
    ) -> CandidateSignal | None:
        """Check for a bearish liquidity sweep at bar *i*.

        Bearish: candle wicks above a recent swing high but closes below it.
        """
        if len(recent_sh) == 0:
            return None

        swept_levels: list[float] = []
        sweep_level: float | None = None

        for sh_idx in recent_sh:
            sh_level = float(highs[sh_idx])
            # Wick above swing high, close below
            if float(highs[i]) > sh_level >= float(closes[i]):
                swept_levels.append(sh_level)
                if sweep_level is None or sh_level > float(highs[i]):
                    sweep_level = sh_level

        if not swept_levels:
            return None

        # Use the highest swept level for reference
        sweep_level = max(swept_levels)

        # --- Confirmation: one of next CONFIRM_BARS candles closes below
        #     the sweep candle's low ---
        confirm_idx: int | None = None
        sweep_low = float(lows[i])
        for j in range(i + 1, min(i + 1 + self._CONFIRM_BARS, n)):
            if float(closes[j]) < sweep_low:
                confirm_idx = j
                break

        if confirm_idx is None:
            return None

        # --- Build signal ---
        entry = float(closes[confirm_idx])
        sl = float(highs[i]) + self._SL_ATR_MULT * atr_val
        risk_dist = abs(sl - entry)
        if risk_dist == 0:
            return None

        tp1 = entry - self._TP1_RR * risk_dist
        tp2 = entry - self._TP2_RR * risk_dist
        rr = self._TP1_RR

        confidence = self._compute_confidence(
            sweep_wick=abs(float(highs[i]) - sweep_level),
            atr_val=atr_val,
            confirm_close=float(closes[confirm_idx]),
            confirm_high=float(highs[confirm_idx]),
            confirm_low=float(lows[confirm_idx]),
            direction=Direction.SELL,
            sweep_ts=sweep_ts,
            num_swept=len(swept_levels),
        )

        confirm_ts = pd.Timestamp(timestamps[confirm_idx]).to_pydatetime()
        active_sessions = get_active_sessions(confirm_ts)
        session = active_sessions[0] if active_sessions else None

        reasoning = (
            f"Bearish liquidity sweep above swing high at {sweep_level:.2f}, "
            f"confirmed by structure shift. "
            f"Entry at {entry:.2f}, SL above sweep wick at {sl:.2f}."
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
            timestamp=confirm_ts,
            session=session,
        )

    # -----------------------------------------------------------------
    # Confidence scoring
    # -----------------------------------------------------------------
    def _compute_confidence(
        self,
        sweep_wick: float,
        atr_val: float,
        confirm_close: float,
        confirm_high: float,
        confirm_low: float,
        direction: Direction,
        sweep_ts: datetime,
        num_swept: int,
    ) -> float:
        """Additive confidence score (0-100).

        Base 50, with bonuses for:
          +10  sweep wick > 1 ATR beyond swing level
          +10  strong confirmation candle (close near extreme)
          +10  in London/NY overlap session (12:00-16:00 UTC)
          +10  swept multiple swing levels (stronger liquidity pool)
        """
        score = float(self._BASE_CONFIDENCE)

        # Bonus: deep sweep (wick extends > 1 ATR beyond level)
        if atr_val > 0 and sweep_wick > atr_val:
            score += 10

        # Bonus: strong confirmation candle
        candle_range = confirm_high - confirm_low
        if candle_range > 0:
            if direction == Direction.BUY:
                # Close near the high
                body_ratio = (confirm_close - confirm_low) / candle_range
            else:
                # Close near the low
                body_ratio = (confirm_high - confirm_close) / candle_range
            if body_ratio > 0.7:
                score += 10

        # Bonus: overlap session (highest liquidity)
        if is_in_session(sweep_ts, "overlap"):
            score += 10

        # Bonus: swept multiple swing levels
        if num_swept >= 2:
            score += 10

        return min(score, 100.0)
