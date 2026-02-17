"""Swing high/low detection using scipy argrelextrema."""

import numpy as np
import pandas as pd
from scipy.signal import argrelextrema


def detect_swing_highs(highs: pd.Series, order: int = 5) -> np.ndarray:
    """Detect swing high indices in a price series.

    A swing high is a local maximum where the high is greater than or equal
    to the surrounding `order` bars on each side.

    Args:
        highs: Series of high prices.
        order: Number of bars on each side to compare (default 5).

    Returns:
        NumPy array of integer indices where swing highs occur.
    """
    indices = argrelextrema(highs.values, np.greater_equal, order=order)[0]
    return indices


def detect_swing_lows(lows: pd.Series, order: int = 5) -> np.ndarray:
    """Detect swing low indices in a price series.

    A swing low is a local minimum where the low is less than or equal
    to the surrounding `order` bars on each side.

    Args:
        lows: Series of low prices.
        order: Number of bars on each side to compare (default 5).

    Returns:
        NumPy array of integer indices where swing lows occur.
    """
    indices = argrelextrema(lows.values, np.less_equal, order=order)[0]
    return indices
