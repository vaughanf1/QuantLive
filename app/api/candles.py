"""Candles query API endpoints.

Provides REST endpoints for querying stored OHLCV candle data and
detecting gaps in the time series.
"""

from datetime import datetime, timedelta, timezone
from enum import Enum

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_session
from app.models.candle import Candle
from app.schemas.candle import CandleResponse
from app.services.candle_ingestor import CandleIngestor

router = APIRouter(prefix="/candles", tags=["candles"])


class TimeframeEnum(str, Enum):
    """Valid timeframe values."""

    M15 = "M15"
    H1 = "H1"
    H4 = "H4"
    D1 = "D1"


@router.get("/{timeframe}", response_model=list[CandleResponse])
async def get_candles(
    timeframe: TimeframeEnum,
    limit: int = Query(default=100, ge=1, le=5000),
    start: datetime | None = Query(default=None, description="Filter candles from this timestamp (inclusive)"),
    end: datetime | None = Query(default=None, description="Filter candles until this timestamp (inclusive)"),
    session: AsyncSession = Depends(get_session),
) -> list[CandleResponse]:
    """Query stored candles for a given timeframe.

    Returns candles ordered by timestamp descending (most recent first),
    with optional date range filtering and pagination via limit.
    """
    query = (
        select(Candle)
        .where(Candle.symbol == "XAUUSD")
        .where(Candle.timeframe == timeframe.value)
    )

    if start is not None:
        query = query.where(Candle.timestamp >= start)
    if end is not None:
        query = query.where(Candle.timestamp <= end)

    query = query.order_by(Candle.timestamp.desc()).limit(limit)

    result = await session.execute(query)
    candles = result.scalars().all()

    return [CandleResponse.model_validate(c) for c in candles]


@router.get("/{timeframe}/gaps", response_model=list[datetime])
async def get_gaps(
    timeframe: TimeframeEnum,
    days: int = Query(default=7, ge=1, le=30, description="Number of days to check for gaps"),
    session: AsyncSession = Depends(get_session),
) -> list[datetime]:
    """Detect missing candles in the stored time series.

    Uses PostgreSQL generate_series to identify expected timestamps that
    have no corresponding candle. Filters out weekends (Saturday/Sunday)
    since forex markets are closed.
    """
    settings = get_settings()
    ingestor = CandleIngestor(api_key=settings.twelve_data_api_key)

    now = datetime.now(timezone.utc)
    start = now - timedelta(days=days)

    gaps = await ingestor.detect_gaps(
        session, "XAUUSD", timeframe.value, start=start, end=now
    )

    return gaps
