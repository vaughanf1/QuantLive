"""Tests for the /candles REST API endpoints."""

from datetime import datetime, timezone
from decimal import Decimal

from app.services.candle_ingestor import CandleIngestor


async def test_get_candles_returns_list(db_session, client, sample_candles):
    """GET /candles/H1 returns a list of candle objects."""
    ingestor = CandleIngestor(api_key="test_key")
    await ingestor.upsert_candles(db_session, sample_candles)

    response = await client.get("/candles/H1")

    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert len(data) == 5

    # Verify candle structure
    candle = data[0]
    assert "symbol" in candle
    assert "timeframe" in candle
    assert "timestamp" in candle
    assert "open" in candle
    assert "high" in candle
    assert "low" in candle
    assert "close" in candle


async def test_get_candles_respects_limit(db_session, client, sample_candles):
    """GET /candles/H1?limit=3 returns exactly 3 candles."""
    ingestor = CandleIngestor(api_key="test_key")
    await ingestor.upsert_candles(db_session, sample_candles)

    response = await client.get("/candles/H1?limit=3")

    assert response.status_code == 200
    data = response.json()
    assert len(data) == 3


async def test_get_candles_invalid_timeframe(client):
    """GET /candles/INVALID returns 422 validation error."""
    response = await client.get("/candles/INVALID")

    assert response.status_code == 422


async def test_get_candles_empty(client):
    """GET /candles/H1 with no data returns empty list, not an error."""
    response = await client.get("/candles/H1")

    assert response.status_code == 200
    data = response.json()
    assert data == []
