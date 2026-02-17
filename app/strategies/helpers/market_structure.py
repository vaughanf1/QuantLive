"""Market structure analysis: Break of Structure (BOS) and Change of Character (CHoCH).

Detects structural shifts in price action using swing highs and swing lows.
All functions operate without lookahead bias -- only data up to the current
bar is considered.
"""

import numpy as np
import pandas as pd


def detect_structure_shift(
    candles: pd.DataFrame,
    swing_highs: np.ndarray,
    swing_lows: np.ndarray,
) -> list[dict]:
    """Detect Break of Structure (BOS) events.

    A bullish BOS occurs when a candle's high breaks above the most recent
    swing high that was established after the most recent swing low.

    A bearish BOS occurs when a candle's low breaks below the most recent
    swing low that was established after the most recent swing high.

    Args:
        candles: DataFrame with 'high' and 'low' columns.
        swing_highs: Array of indices where swing highs occur.
        swing_lows: Array of indices where swing lows occur.

    Returns:
        List of dicts with keys: index, type, broken_level.
        - type: "bullish_bos" or "bearish_bos"
        - broken_level: the price level that was broken
    """
    events: list[dict] = []

    if len(swing_highs) == 0 or len(swing_lows) == 0:
        return events

    highs = candles["high"].values
    lows = candles["low"].values
    n = len(candles)

    # Combine swings into a chronological sequence for tracking
    # Track which swing levels have already been broken to avoid duplicates
    broken_high_levels: set[int] = set()  # swing high indices already broken
    broken_low_levels: set[int] = set()   # swing low indices already broken

    for i in range(n):
        # Find the most recent swing high before bar i
        valid_sh = swing_highs[swing_highs < i]
        valid_sl = swing_lows[swing_lows < i]

        if len(valid_sh) == 0 or len(valid_sl) == 0:
            continue

        last_sh_idx = valid_sh[-1]
        last_sl_idx = valid_sl[-1]

        # Bullish BOS: price breaks above a swing high that formed after a swing low
        if last_sh_idx > last_sl_idx and last_sh_idx not in broken_high_levels:
            sh_level = highs[last_sh_idx]
            if highs[i] > sh_level:
                events.append({
                    "index": i,
                    "type": "bullish_bos",
                    "broken_level": float(sh_level),
                })
                broken_high_levels.add(last_sh_idx)

        # Bearish BOS: price breaks below a swing low that formed after a swing high
        if last_sl_idx > last_sh_idx and last_sl_idx not in broken_low_levels:
            sl_level = lows[last_sl_idx]
            if lows[i] < sl_level:
                events.append({
                    "index": i,
                    "type": "bearish_bos",
                    "broken_level": float(sl_level),
                })
                broken_low_levels.add(last_sl_idx)

    return events


# Alias for the plan's artifact export name
detect_bos = detect_structure_shift


def detect_choch(
    candles: pd.DataFrame,
    swing_highs: np.ndarray,
    swing_lows: np.ndarray,
) -> list[dict]:
    """Detect Change of Character (CHoCH) events.

    A bullish CHoCH is the first higher high after a series of lower highs,
    signaling a potential shift from bearish to bullish structure.

    A bearish CHoCH is the first lower low after a series of higher lows,
    signaling a potential shift from bullish to bearish structure.

    Args:
        candles: DataFrame with 'high' and 'low' columns.
        swing_highs: Array of indices where swing highs occur.
        swing_lows: Array of indices where swing lows occur.

    Returns:
        List of dicts with keys: index, type, level.
        - type: "bullish_choch" or "bearish_choch"
        - level: the price level where character changed
    """
    events: list[dict] = []

    highs = candles["high"].values
    lows = candles["low"].values

    # Detect bullish CHoCH: series of lower highs, then a higher high
    if len(swing_highs) >= 2:
        in_lower_highs = False
        for i in range(1, len(swing_highs)):
            prev_sh_val = highs[swing_highs[i - 1]]
            curr_sh_val = highs[swing_highs[i]]

            if curr_sh_val < prev_sh_val:
                # Continuing or starting a lower-high sequence
                in_lower_highs = True
            elif curr_sh_val > prev_sh_val and in_lower_highs:
                # First higher high after lower highs = bullish CHoCH
                events.append({
                    "index": int(swing_highs[i]),
                    "type": "bullish_choch",
                    "level": float(curr_sh_val),
                })
                in_lower_highs = False

    # Detect bearish CHoCH: series of higher lows, then a lower low
    if len(swing_lows) >= 2:
        in_higher_lows = False
        for i in range(1, len(swing_lows)):
            prev_sl_val = lows[swing_lows[i - 1]]
            curr_sl_val = lows[swing_lows[i]]

            if curr_sl_val > prev_sl_val:
                # Continuing or starting a higher-low sequence
                in_higher_lows = True
            elif curr_sl_val < prev_sl_val and in_higher_lows:
                # First lower low after higher lows = bearish CHoCH
                events.append({
                    "index": int(swing_lows[i]),
                    "type": "bearish_choch",
                    "level": float(curr_sl_val),
                })
                in_higher_lows = False

    # Sort by index for chronological order
    events.sort(key=lambda e: e["index"])
    return events
