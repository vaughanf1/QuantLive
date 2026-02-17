"""Twelve Data candle ingestion service with upsert deduplication and gap detection.

Fetches XAUUSD OHLCV candles from Twelve Data, stores them in PostgreSQL
using upsert (ON CONFLICT DO UPDATE) for deduplication, supports incremental
fetching from the latest stored timestamp, and detects gaps in the time series.
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from loguru import logger
from sqlalchemy import func, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from tenacity import retry, stop_after_attempt, wait_exponential
from twelvedata import TDClient

from app.models.candle import Candle

# Map internal timeframe codes to Twelve Data interval strings
INTERVAL_MAP = {
    "M15": "15min",
    "H1": "1h",
    "H4": "4h",
    "D1": "1day",
}

# Map internal timeframe codes to timedelta objects for arithmetic
INTERVAL_TIMEDELTA = {
    "M15": timedelta(minutes=15),
    "H1": timedelta(hours=1),
    "H4": timedelta(hours=4),
    "D1": timedelta(days=1),
}

# Map internal timeframe codes to PostgreSQL interval strings for generate_series
INTERVAL_PG = {
    "M15": "15 minutes",
    "H1": "1 hour",
    "H4": "4 hours",
    "D1": "1 day",
}


class CandleIngestor:
    """Fetches XAUUSD candles from Twelve Data and upserts into PostgreSQL."""

    def __init__(self, api_key: str) -> None:
        self.client = TDClient(apikey=api_key)

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, max=30))
    def _fetch_from_api(
        self,
        symbol: str,
        interval: str,
        outputsize: int,
        start_date: str | None = None,
    ) -> list[dict]:
        """Call Twelve Data API with retry logic.

        This is a synchronous method because the twelvedata library uses
        synchronous HTTP requests internally. Wrapped with tenacity retry.
        Returns raw list of dicts from the JSON response.
        """
        params: dict = {
            "symbol": symbol,
            "interval": interval,
            "outputsize": outputsize,
            "timezone": "UTC",
            "order": "asc",
        }
        if start_date is not None:
            params["start_date"] = start_date

        ts = self.client.time_series(**params)
        data = ts.as_json()

        # Twelve Data returns a dict with "code"/"message" on errors
        if isinstance(data, dict) and "code" in data:
            raise RuntimeError(
                f"Twelve Data API error {data.get('code')}: {data.get('message', 'unknown')}"
            )

        return data if isinstance(data, list) else []

    async def fetch_candles(
        self,
        symbol: str,
        timeframe: str,
        outputsize: int = 100,
        start_date: str | None = None,
    ) -> list[dict]:
        """Fetch candles from Twelve Data and parse into database-ready dicts.

        Args:
            symbol: Trading pair (e.g. "XAUUSD"). Converted to "XAU/USD" for API.
            timeframe: Internal timeframe code (M15, H1, H4, D1).
            outputsize: Number of candles to request.
            start_date: ISO date string for incremental fetch start.

        Returns:
            List of dicts ready for PostgreSQL upsert.
        """
        interval = INTERVAL_MAP.get(timeframe)
        if interval is None:
            raise ValueError(f"Unknown timeframe: {timeframe}. Valid: {list(INTERVAL_MAP.keys())}")

        # Convert internal symbol format to Twelve Data format
        api_symbol = "XAU/USD" if symbol == "XAUUSD" else symbol

        raw = self._fetch_from_api(api_symbol, interval, outputsize, start_date)

        if not raw:
            logger.warning(
                "Empty response from Twelve Data | symbol={symbol} timeframe={timeframe}",
                symbol=symbol,
                timeframe=timeframe,
            )
            return []

        candles = []
        for row in raw:
            ts = datetime.strptime(row["datetime"], "%Y-%m-%d %H:%M:%S").replace(
                tzinfo=timezone.utc
            )
            candles.append(
                {
                    "symbol": symbol,
                    "timeframe": timeframe,
                    "timestamp": ts,
                    "open": Decimal(row["open"]),
                    "high": Decimal(row["high"]),
                    "low": Decimal(row["low"]),
                    "close": Decimal(row["close"]),
                    "volume": Decimal(row["volume"]) if row.get("volume") else None,
                }
            )

        logger.info(
            "Fetched {count} candles | symbol={symbol} timeframe={timeframe}",
            count=len(candles),
            symbol=symbol,
            timeframe=timeframe,
        )
        return candles

    async def upsert_candles(self, session: AsyncSession, candles: list[dict]) -> int:
        """Upsert candles into PostgreSQL using ON CONFLICT DO UPDATE.

        Args:
            session: Async SQLAlchemy session.
            candles: List of candle dicts with keys matching Candle model columns.

        Returns:
            Number of rows affected.
        """
        if not candles:
            return 0

        stmt = pg_insert(Candle).values(candles)
        stmt = stmt.on_conflict_do_update(
            index_elements=["symbol", "timeframe", "timestamp"],
            set_={
                "open": stmt.excluded.open,
                "high": stmt.excluded.high,
                "low": stmt.excluded.low,
                "close": stmt.excluded.close,
                "volume": stmt.excluded.volume,
            },
        )

        result = await session.execute(stmt)
        await session.commit()

        count = result.rowcount
        logger.info("Upserted {count} candles", count=count)
        return count

    async def get_latest_timestamp(
        self, session: AsyncSession, symbol: str, timeframe: str
    ) -> datetime | None:
        """Get the most recent stored candle timestamp for a symbol/timeframe.

        Args:
            session: Async SQLAlchemy session.
            symbol: Trading symbol (e.g. "XAUUSD").
            timeframe: Timeframe code (M15, H1, H4, D1).

        Returns:
            Latest timestamp or None if no data exists.
        """
        result = await session.execute(
            text(
                "SELECT MAX(timestamp) FROM candles "
                "WHERE symbol = :symbol AND timeframe = :timeframe"
            ),
            {"symbol": symbol, "timeframe": timeframe},
        )
        row = result.scalar()
        return row

    async def fetch_and_store(
        self,
        session: AsyncSession,
        symbol: str,
        timeframe: str,
        outputsize: int = 100,
    ) -> int:
        """Orchestrate incremental fetch and upsert of candles.

        Determines the latest stored timestamp and fetches only newer candles.
        On first run (no data), performs a full backfill of `outputsize` candles.

        Args:
            session: Async SQLAlchemy session.
            symbol: Trading symbol (e.g. "XAUUSD").
            timeframe: Timeframe code (M15, H1, H4, D1).
            outputsize: Number of candles for initial backfill.

        Returns:
            Number of candles stored.
        """
        latest = await self.get_latest_timestamp(session, symbol, timeframe)
        is_backfill = latest is None

        start_date: str | None = None
        if latest is not None:
            # Start from next interval after the latest stored candle
            delta = INTERVAL_TIMEDELTA[timeframe]
            next_start = latest + delta
            start_date = next_start.strftime("%Y-%m-%d %H:%M:%S")
            logger.info(
                "Incremental fetch | symbol={symbol} timeframe={timeframe} start_date={start_date}",
                symbol=symbol,
                timeframe=timeframe,
                start_date=start_date,
            )

        candles = await self.fetch_candles(symbol, timeframe, outputsize, start_date)
        count = await self.upsert_candles(session, candles)

        logger.info(
            "fetch_and_store complete | symbol={symbol} timeframe={timeframe} "
            "is_backfill={is_backfill} count={count}",
            symbol=symbol,
            timeframe=timeframe,
            is_backfill=is_backfill,
            count=count,
        )
        return count

    async def detect_gaps(
        self,
        session: AsyncSession,
        symbol: str,
        timeframe: str,
        start: datetime,
        end: datetime,
    ) -> list[datetime]:
        """Detect missing candles in a time range using PostgreSQL generate_series.

        Uses a LEFT JOIN anti-pattern to find expected timestamps that have no
        corresponding candle in the database. Filters out weekends (Saturday/Sunday)
        since forex markets are closed.

        Args:
            session: Async SQLAlchemy session.
            symbol: Trading symbol (e.g. "XAUUSD").
            timeframe: Timeframe code (M15, H1, H4, D1).
            start: Start of the range to check (UTC-aware).
            end: End of the range to check (UTC-aware).

        Returns:
            List of missing timestamps (UTC-aware).
        """
        pg_interval = INTERVAL_PG.get(timeframe)
        if pg_interval is None:
            raise ValueError(f"Unknown timeframe: {timeframe}")

        # Embed interval as a SQL literal (safe: pg_interval is from our controlled mapping).
        # asyncpg cannot bind a plain string as a PostgreSQL interval parameter.
        query = text(f"""
            SELECT expected_ts
            FROM generate_series(
                CAST(:start_ts AS timestamptz),
                CAST(:end_ts AS timestamptz),
                '{pg_interval}'::interval
            ) AS expected_ts
            LEFT JOIN candles c
                ON c.symbol = :symbol
                AND c.timeframe = :timeframe
                AND c.timestamp = expected_ts
            WHERE c.id IS NULL
                AND EXTRACT(DOW FROM expected_ts) NOT IN (0, 6)
            ORDER BY expected_ts
        """)

        result = await session.execute(
            query,
            {
                "start_ts": start,
                "end_ts": end,
                "symbol": symbol,
                "timeframe": timeframe,
            },
        )

        gaps = [row[0].replace(tzinfo=timezone.utc) if row[0].tzinfo is None else row[0] for row in result.fetchall()]

        if gaps:
            logger.warning(
                "Detected {count} gaps | symbol={symbol} timeframe={timeframe} "
                "range={start} to {end}",
                count=len(gaps),
                symbol=symbol,
                timeframe=timeframe,
                start=start.isoformat(),
                end=end.isoformat(),
            )
        else:
            logger.info(
                "No gaps detected | symbol={symbol} timeframe={timeframe} "
                "range={start} to {end}",
                symbol=symbol,
                timeframe=timeframe,
                start=start.isoformat(),
                end=end.isoformat(),
            )

        return gaps
