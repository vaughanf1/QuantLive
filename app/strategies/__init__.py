"""Strategy engine package.

Re-exports base classes for convenient access:
    from app.strategies import BaseStrategy, CandidateSignal
"""

from app.strategies.base import (
    BaseStrategy,
    CandidateSignal,
    Direction,
    InsufficientDataError,
    candles_to_dataframe,
)

# Concrete strategy imports -- triggers auto-registration via __init_subclass__
from app.strategies.liquidity_sweep import LiquiditySweepStrategy
from app.strategies.trend_continuation import TrendContinuationStrategy
from app.strategies.breakout_expansion import BreakoutExpansionStrategy
from app.strategies.ema_momentum import EMAMomentumStrategy

__all__ = [
    "BaseStrategy",
    "CandidateSignal",
    "Direction",
    "InsufficientDataError",
    "candles_to_dataframe",
    "LiquiditySweepStrategy",
    "TrendContinuationStrategy",
    "BreakoutExpansionStrategy",
    "EMAMomentumStrategy",
]
