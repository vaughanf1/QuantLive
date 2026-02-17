"""Thin wrappers around pandas-ta for common technical indicators."""

import pandas as pd
import pandas_ta_classic as ta
from loguru import logger


def compute_ema(series: pd.Series, length: int) -> pd.Series:
    """Compute Exponential Moving Average.

    Args:
        series: Price series (typically close prices).
        length: EMA period length.

    Returns:
        EMA series of the same length as input (leading NaNs for warmup).
    """
    return ta.ema(series, length=length)


def compute_atr(
    high: pd.Series, low: pd.Series, close: pd.Series, length: int = 14
) -> pd.Series:
    """Compute Average True Range.

    Args:
        high: High price series.
        low: Low price series.
        close: Close price series.
        length: ATR period length (default 14).

    Returns:
        ATR series of the same length as input (leading NaNs for warmup).
    """
    return ta.atr(high=high, low=low, close=close, length=length)


def compute_vwap(df: pd.DataFrame) -> pd.Series:
    """Compute Volume-Weighted Average Price.

    Requires a DataFrame with columns: timestamp, high, low, close, volume.
    Sets DatetimeIndex internally as required by pandas-ta VWAP.

    Args:
        df: DataFrame with OHLCV data and a 'timestamp' column.

    Returns:
        VWAP series aligned to the original DataFrame index.
        If volume is all zero/NaN, returns a Series of NaN.
    """
    df_copy = df.copy()

    # Check for valid volume data
    if "volume" not in df_copy.columns or df_copy["volume"].fillna(0).eq(0).all():
        logger.warning("VWAP: volume data is all zero or NaN; returning NaN series")
        return pd.Series([float("nan")] * len(df_copy), index=df_copy.index)

    # pandas-ta VWAP requires DatetimeIndex
    df_copy.index = pd.DatetimeIndex(df_copy["timestamp"])
    result = ta.vwap(
        high=df_copy["high"],
        low=df_copy["low"],
        close=df_copy["close"],
        volume=df_copy["volume"],
    )

    # Realign to original index
    if result is not None:
        result.index = df.index
        return result

    logger.warning("VWAP: pandas-ta returned None; returning NaN series")
    return pd.Series([float("nan")] * len(df), index=df.index)


def compute_rsi(series: pd.Series, length: int = 14) -> pd.Series:
    """Compute Relative Strength Index.

    Args:
        series: Price series (typically close prices).
        length: RSI period length (default 14).

    Returns:
        RSI series of the same length as input (leading NaNs for warmup).
    """
    return ta.rsi(series, length=length)
