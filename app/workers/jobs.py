"""Scheduled job functions for candle ingestion.

These run outside the FastAPI request context, so sessions are created
directly from async_session_factory. All exceptions are caught to prevent
scheduler crashes.
"""

from datetime import datetime, timedelta, timezone

from loguru import logger

from app.config import get_settings
from app.database import async_session_factory
from app.services.candle_ingestor import CandleIngestor


async def refresh_candles(timeframe: str) -> None:
    """Fetch and store new candles for a given timeframe, then check for gaps.

    This function is registered as an APScheduler job. It creates its own
    database session (not via FastAPI Depends) and wraps all work in try/except
    to prevent job failures from killing the scheduler.

    Args:
        timeframe: Internal timeframe code (M15, H1, H4, D1).
    """
    try:
        settings = get_settings()
        ingestor = CandleIngestor(api_key=settings.twelve_data_api_key)

        async with async_session_factory() as session:
            count = await ingestor.fetch_and_store(session, "XAUUSD", timeframe)

            # Check for gaps in the last 7 days
            now = datetime.now(timezone.utc)
            seven_days_ago = now - timedelta(days=7)
            gaps = await ingestor.detect_gaps(
                session, "XAUUSD", timeframe, start=seven_days_ago, end=now
            )

            logger.info(
                "refresh_candles complete | timeframe={timeframe} "
                "candles_stored={count} gaps_found={gaps}",
                timeframe=timeframe,
                count=count,
                gaps=len(gaps),
            )

    except Exception:
        logger.exception(
            "refresh_candles failed | timeframe={timeframe}",
            timeframe=timeframe,
        )
