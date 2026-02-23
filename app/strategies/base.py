"""Strategy engine foundation: BaseStrategy ABC, CandidateSignal model, and registry."""

from abc import ABC, abstractmethod
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import ClassVar

import pandas as pd
from pydantic import BaseModel, Field


class Direction(str, Enum):
    """Trade direction."""

    BUY = "BUY"
    SELL = "SELL"


class CandidateSignal(BaseModel):
    """Pydantic model for strategy signal output.

    Field types and precision aligned with the Signal DB model
    (app/models/signal.py) to ensure seamless persistence.
    """

    strategy_name: str
    symbol: str = "XAUUSD"
    timeframe: str  # e.g. "M15", "H1", "H4", "D1"
    direction: Direction
    entry_price: Decimal = Field(max_digits=10, decimal_places=2)
    stop_loss: Decimal = Field(max_digits=10, decimal_places=2)
    take_profit_1: Decimal = Field(max_digits=10, decimal_places=2)
    take_profit_2: Decimal = Field(max_digits=10, decimal_places=2)
    risk_reward: Decimal = Field(max_digits=5, decimal_places=2)
    confidence: Decimal = Field(
        ge=Decimal("0"), le=Decimal("100"), max_digits=5, decimal_places=2
    )
    reasoning: str
    timestamp: datetime  # Candle timestamp that triggered the signal
    invalidation_price: Decimal | None = None
    session: str | None = None  # e.g. "london", "new_york", "asian"


class InsufficientDataError(Exception):
    """Raised when candle count is below a strategy's minimum requirement."""

    pass


class BaseStrategy(ABC):
    """Abstract base class for all trading strategies.

    Concrete strategies MUST define these class attributes:
        name: str              -- unique strategy identifier
        required_timeframes: list[str]  -- e.g. ["M15", "H1"]
        min_candles: int       -- minimum candle count for analysis

    Concrete strategies MUST implement:
        analyze(candles: pd.DataFrame) -> list[CandidateSignal]

    Auto-registration: When a concrete subclass (one that defines a string
    ``name`` attribute and has no remaining abstract methods) is created,
    it is automatically added to ``_registry`` via ``__init_subclass__``.
    """

    _registry: ClassVar[dict[str, type["BaseStrategy"]]] = {}

    # Concrete strategies define these as class attributes:
    name: ClassVar[str]
    required_timeframes: ClassVar[list[str]]
    min_candles: ClassVar[int]

    # Default parameters — concrete strategies override with their own defaults.
    DEFAULT_PARAMS: ClassVar[dict[str, float]] = {}

    def __init__(self, params: dict[str, float] | None = None) -> None:
        """Merge optional param overrides onto class DEFAULT_PARAMS.

        Args:
            params: Optional dict of parameter overrides.  Keys that match
                    DEFAULT_PARAMS are replaced; unmatched keys are ignored.
                    ``None`` or ``{}`` means use all defaults.
        """
        self.params: dict[str, float] = dict(self.DEFAULT_PARAMS)
        if params:
            for key, value in params.items():
                if key in self.params:
                    self.params[key] = value

    def __init_subclass__(cls, **kwargs: object) -> None:
        super().__init_subclass__(**kwargs)
        # Only register concrete subclasses that define a string name
        if hasattr(cls, "name") and isinstance(cls.name, str):
            # Skip classes that still have unresolved abstract methods
            if not getattr(cls, "__abstractmethods__", set()):
                BaseStrategy._registry[cls.name] = cls

    @abstractmethod
    def analyze(self, candles: pd.DataFrame) -> list[CandidateSignal]:
        """Analyze candle data and return candidate trade signals.

        Args:
            candles: DataFrame with columns [timestamp, open, high, low, close]
                     and optionally [volume]. Values are floats (use
                     candles_to_dataframe to convert from ORM objects).

        Returns:
            List of CandidateSignal instances (may be empty).

        Raises:
            InsufficientDataError: If len(candles) < self.min_candles.
        """
        ...

    def validate_data(self, candles: pd.DataFrame) -> None:
        """Validate candle DataFrame before analysis.

        Raises:
            InsufficientDataError: If candle count is below min_candles.
            ValueError: If required columns are missing.
        """
        required_columns = {"timestamp", "open", "high", "low", "close"}
        missing = required_columns - set(candles.columns)
        if missing:
            raise ValueError(f"Missing required columns: {missing}")

        if len(candles) < self.min_candles:
            raise InsufficientDataError(
                f"Strategy '{self.name}' requires at least {self.min_candles} "
                f"candles, but received {len(candles)}."
            )

    @classmethod
    def get_registry(cls) -> dict[str, type["BaseStrategy"]]:
        """Return a copy of the strategy registry."""
        return dict(cls._registry)

    @classmethod
    def get_strategy(
        cls, name: str, params: dict[str, float] | None = None
    ) -> "BaseStrategy":
        """Instantiate and return a strategy by name.

        Args:
            name: The registered strategy name.
            params: Optional parameter overrides passed to the constructor.

        Returns:
            An instance of the requested strategy.

        Raises:
            KeyError: If no strategy with the given name is registered.
        """
        if name not in cls._registry:
            available = list(cls._registry.keys())
            raise KeyError(
                f"Strategy '{name}' not found. Available: {available}"
            )
        return cls._registry[name](params=params)


def candles_to_dataframe(candles: list) -> pd.DataFrame:
    """Convert Candle ORM objects to a float-based DataFrame for pandas-ta.

    Converts Decimal fields (open, high, low, close, volume) to float.
    Sorts by timestamp ascending. Resets index.

    Args:
        candles: List of Candle ORM model instances.

    Returns:
        DataFrame with columns: timestamp, open, high, low, close, volume.
        Numeric columns are float64.
    """
    rows = []
    for c in candles:
        row = {
            "timestamp": c.timestamp,
            "open": float(c.open),
            "high": float(c.high),
            "low": float(c.low),
            "close": float(c.close),
            "volume": float(c.volume) if c.volume is not None else 0.0,
        }
        rows.append(row)

    df = pd.DataFrame(rows)
    df = df.sort_values("timestamp", ascending=True).reset_index(drop=True)
    return df
