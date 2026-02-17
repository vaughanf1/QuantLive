"""Pydantic response schemas for candle data."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class CandleResponse(BaseModel):
    """Response model for individual candle data."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    symbol: str
    timeframe: str
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float | None = None
