"""Tests for the CandleIngestor service: upsert dedup, gap detection, incremental fetch, timezone."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import MagicMock, patch

from sqlalchemy import func, select, text

from app.models.candle import Candle
from app.services.candle_ingestor import CandleIngestor


# ---------------------------------------------------------------------------
# Upsert deduplication tests (DATA-02, critical)
# ---------------------------------------------------------------------------


async def test_upsert_creates_candles(db_session, sample_candles):
    """Inserting 5 candles results in 5 rows in the database."""
    ingestor = CandleIngestor(api_key="test_key")
    count = await ingestor.upsert_candles(db_session, sample_candles)

    assert count == 5

    result = await db_session.execute(select(func.count()).select_from(Candle))
    total = result.scalar()
    assert total == 5


async def test_upsert_deduplication(db_session, sample_candles):
    """Inserting the same 5 candles twice results in 5 rows, not 10."""
    ingestor = CandleIngestor(api_key="test_key")

    await ingestor.upsert_candles(db_session, sample_candles)
    await ingestor.upsert_candles(db_session, sample_candles)

    result = await db_session.execute(select(func.count()).select_from(Candle))
    total = result.scalar()
    assert total == 5


async def test_upsert_updates_on_conflict(db_session, sample_candles):
    """Upserting a candle with a new close price updates the stored value."""
    ingestor = CandleIngestor(api_key="test_key")

    # Insert original candles
    await ingestor.upsert_candles(db_session, sample_candles)

    # Modify close price of first candle and re-upsert
    updated = [sample_candles[0].copy()]
    updated[0]["close"] = Decimal("2999.99")
    await ingestor.upsert_candles(db_session, updated)

    # Verify the stored close is the updated value
    result = await db_session.execute(
        select(Candle.close).where(
            Candle.symbol == "XAUUSD",
            Candle.timeframe == "H1",
            Candle.timestamp == sample_candles[0]["timestamp"],
        )
    )
    stored_close = result.scalar()
    assert stored_close == Decimal("2999.99")


# ---------------------------------------------------------------------------
# Gap detection tests (DATA-03)
# ---------------------------------------------------------------------------


async def test_detect_gaps_finds_missing(db_session, sample_candles):
    """Gap detection finds 12:00 missing when 10:00, 11:00, 13:00 are present."""
    ingestor = CandleIngestor(api_key="test_key")

    # Insert candles at 10:00, 11:00, 13:00 (skip 12:00)
    candles_with_gap = [c for c in sample_candles if c["timestamp"].hour != 12]
    await ingestor.upsert_candles(db_session, candles_with_gap)

    start = datetime(2026, 2, 16, 10, 0, tzinfo=timezone.utc)
    end = datetime(2026, 2, 16, 13, 0, tzinfo=timezone.utc)

    gaps = await ingestor.detect_gaps(db_session, "XAUUSD", "H1", start, end)

    expected_gap = datetime(2026, 2, 16, 12, 0, tzinfo=timezone.utc)
    assert expected_gap in gaps
    assert len(gaps) == 1


async def test_detect_gaps_no_gaps(db_session, sample_candles):
    """No gaps returned when all candles in range are present."""
    ingestor = CandleIngestor(api_key="test_key")

    await ingestor.upsert_candles(db_session, sample_candles)

    start = datetime(2026, 2, 16, 10, 0, tzinfo=timezone.utc)
    end = datetime(2026, 2, 16, 14, 0, tzinfo=timezone.utc)

    gaps = await ingestor.detect_gaps(db_session, "XAUUSD", "H1", start, end)

    assert gaps == []


async def test_detect_gaps_filters_weekends(db_session, sample_candles):
    """Weekend timestamps (Saturday/Sunday) are not reported as gaps."""
    ingestor = CandleIngestor(api_key="test_key")

    # Insert candles on Monday Feb 16 (10:00-14:00)
    await ingestor.upsert_candles(db_session, sample_candles)

    # Check a range that spans Friday Feb 13 through Monday Feb 16.
    # Saturday Feb 14 and Sunday Feb 15 should NOT appear as gaps.
    start = datetime(2026, 2, 13, 10, 0, tzinfo=timezone.utc)
    end = datetime(2026, 2, 16, 14, 0, tzinfo=timezone.utc)

    gaps = await ingestor.detect_gaps(db_session, "XAUUSD", "H1", start, end)

    # No gap should fall on Saturday (dow=6) or Sunday (dow=0)
    for gap_ts in gaps:
        weekday = gap_ts.weekday()  # Python: Monday=0, Sunday=6
        assert weekday not in (5, 6), f"Weekend gap found: {gap_ts} (weekday={weekday})"


# ---------------------------------------------------------------------------
# Incremental fetch tests (DATA-02 caching)
# ---------------------------------------------------------------------------


async def test_get_latest_timestamp_empty(db_session):
    """Returns None when no candles exist in the database."""
    ingestor = CandleIngestor(api_key="test_key")

    result = await ingestor.get_latest_timestamp(db_session, "XAUUSD", "H1")
    assert result is None


async def test_get_latest_timestamp_returns_max(db_session, sample_candles):
    """Returns the maximum timestamp from stored candles."""
    ingestor = CandleIngestor(api_key="test_key")

    await ingestor.upsert_candles(db_session, sample_candles)
    result = await ingestor.get_latest_timestamp(db_session, "XAUUSD", "H1")

    expected = datetime(2026, 2, 16, 14, 0, tzinfo=timezone.utc)
    # Compare without timezone info issues (both should be UTC)
    assert result is not None
    assert result.replace(tzinfo=timezone.utc) == expected or result == expected


async def test_fetch_and_store_backfill(db_session, mock_twelve_data):
    """On empty DB, fetch_and_store does a backfill (no start_date param)."""
    ingestor = CandleIngestor(api_key="test_key")

    count = await ingestor.fetch_and_store(db_session, "XAUUSD", "H1", outputsize=100)

    assert count == 5  # mock returns 5 candles

    # Verify time_series was called WITHOUT start_date
    call_kwargs = mock_twelve_data.time_series.call_args
    params = call_kwargs.kwargs if call_kwargs.kwargs else {}
    # Could be positional or keyword -- check the call
    call_args_dict = call_kwargs[1] if len(call_kwargs) > 1 and isinstance(call_kwargs[1], dict) else {}
    if not call_args_dict:
        call_args_dict = call_kwargs.kwargs if hasattr(call_kwargs, 'kwargs') else {}
    # The key check: start_date should not be in the params
    assert "start_date" not in call_args_dict or call_args_dict.get("start_date") is None


async def test_fetch_and_store_incremental(db_session, sample_candles, mock_twelve_data):
    """With existing candles, fetch_and_store passes start_date to the API."""
    ingestor = CandleIngestor(api_key="test_key")

    # Pre-populate with sample candles
    await ingestor.upsert_candles(db_session, sample_candles)

    # Reset mock call tracking
    mock_twelve_data.time_series.reset_mock()

    # Now do an incremental fetch
    await ingestor.fetch_and_store(db_session, "XAUUSD", "H1", outputsize=100)

    # Verify time_series was called WITH start_date
    call_kwargs = mock_twelve_data.time_series.call_args
    call_args_dict = call_kwargs.kwargs if hasattr(call_kwargs, 'kwargs') else {}
    assert "start_date" in call_args_dict
    assert call_args_dict["start_date"] is not None


# ---------------------------------------------------------------------------
# Timezone correctness tests (DATA-04)
# ---------------------------------------------------------------------------


async def test_stored_candles_have_utc_timezone(db_session, sample_candles):
    """All stored candle timestamps should be UTC-aware."""
    ingestor = CandleIngestor(api_key="test_key")

    await ingestor.upsert_candles(db_session, sample_candles)

    result = await db_session.execute(select(Candle))
    candles = result.scalars().all()

    for candle in candles:
        ts = candle.timestamp
        # Timestamp should have tzinfo set (UTC-aware, not naive)
        assert ts.tzinfo is not None, f"Candle timestamp is timezone-naive: {ts}"


async def test_fetch_passes_utc_to_twelve_data(mock_twelve_data):
    """fetch_candles passes timezone='UTC' to the Twelve Data API call."""
    ingestor = CandleIngestor(api_key="test_key")

    await ingestor.fetch_candles("XAUUSD", "H1", outputsize=5)

    call_kwargs = mock_twelve_data.time_series.call_args.kwargs
    assert call_kwargs.get("timezone") == "UTC"
