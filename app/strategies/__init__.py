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

# Concrete strategy imports will be added as strategies are implemented

__all__ = [
    "BaseStrategy",
    "CandidateSignal",
    "Direction",
    "InsufficientDataError",
    "candles_to_dataframe",
]
