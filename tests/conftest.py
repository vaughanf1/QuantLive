"""Shared async test fixtures for database, HTTP client, and mocks."""

import os
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.models.base import Base

# ---------------------------------------------------------------------------
# Test database URL
# ---------------------------------------------------------------------------
TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://vaughanfawcett@localhost:5432/goldsignal_test",
)

# Module-level engine for schema management (create/drop tables)
_schema_engine = create_async_engine(TEST_DATABASE_URL, echo=False)
_tables_created = False


async def _ensure_tables():
    global _tables_created
    if not _tables_created:
        async with _schema_engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        _tables_created = True


# ---------------------------------------------------------------------------
# Database session (function-scoped)
# Each test gets its own engine + session to avoid connection conflicts.
# Tables are truncated before each test for isolation.
# ---------------------------------------------------------------------------
@pytest_asyncio.fixture
async def db_session():
    """Provide an isolated database session for each test.

    Creates a fresh engine per test to avoid asyncpg connection contention.
    Truncates candles table before the test starts for clean state.
    """
    await _ensure_tables()

    engine = create_async_engine(TEST_DATABASE_URL, echo=False, pool_size=2)

    # Clean before each test
    async with engine.begin() as conn:
        await conn.execute(text("DELETE FROM candles"))

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    session = session_factory()

    yield session

    await session.close()
    await engine.dispose()


# ---------------------------------------------------------------------------
# FastAPI test client (function-scoped)
# ---------------------------------------------------------------------------
@pytest_asyncio.fixture
async def client(db_session):
    """Async HTTP client with test database session injected."""
    from app.database import get_session
    from app.main import app

    async def _override_get_session():
        yield db_session

    app.dependency_overrides[get_session] = _override_get_session

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c

    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Mock Twelve Data client (function-scoped)
# ---------------------------------------------------------------------------
@pytest.fixture
def mock_twelve_data():
    """Patch TDClient so no real API calls are made during tests.

    Returns a mock whose .time_series().as_json() yields 5 candles
    with realistic XAUUSD prices.
    """
    fake_candles = [
        {
            "datetime": "2026-02-16 10:00:00",
            "open": "2645.50",
            "high": "2647.00",
            "low": "2644.00",
            "close": "2646.00",
            "volume": "1234",
        },
        {
            "datetime": "2026-02-16 11:00:00",
            "open": "2646.00",
            "high": "2648.50",
            "low": "2645.00",
            "close": "2647.50",
            "volume": "1456",
        },
        {
            "datetime": "2026-02-16 12:00:00",
            "open": "2647.50",
            "high": "2650.00",
            "low": "2646.50",
            "close": "2649.00",
            "volume": "1678",
        },
        {
            "datetime": "2026-02-16 13:00:00",
            "open": "2649.00",
            "high": "2651.00",
            "low": "2648.00",
            "close": "2650.50",
            "volume": "1890",
        },
        {
            "datetime": "2026-02-16 14:00:00",
            "open": "2650.50",
            "high": "2652.00",
            "low": "2649.50",
            "close": "2651.00",
            "volume": "2012",
        },
    ]

    with patch("app.services.candle_ingestor.TDClient") as mock_cls:
        mock_instance = MagicMock()
        mock_cls.return_value = mock_instance

        mock_ts = MagicMock()
        mock_instance.time_series.return_value = mock_ts
        mock_ts.as_json.return_value = fake_candles

        yield mock_instance


# ---------------------------------------------------------------------------
# Sample candle dicts (function-scoped)
# ---------------------------------------------------------------------------
@pytest.fixture
def sample_candles():
    """Return 5 candle dicts with known values on a Monday (2026-02-16)."""
    return [
        {
            "symbol": "XAUUSD",
            "timeframe": "H1",
            "timestamp": datetime(2026, 2, 16, 10, 0, tzinfo=timezone.utc),
            "open": Decimal("2645.50"),
            "high": Decimal("2647.00"),
            "low": Decimal("2644.00"),
            "close": Decimal("2646.00"),
            "volume": Decimal("1234"),
        },
        {
            "symbol": "XAUUSD",
            "timeframe": "H1",
            "timestamp": datetime(2026, 2, 16, 11, 0, tzinfo=timezone.utc),
            "open": Decimal("2646.00"),
            "high": Decimal("2648.50"),
            "low": Decimal("2645.00"),
            "close": Decimal("2647.50"),
            "volume": Decimal("1456"),
        },
        {
            "symbol": "XAUUSD",
            "timeframe": "H1",
            "timestamp": datetime(2026, 2, 16, 12, 0, tzinfo=timezone.utc),
            "open": Decimal("2647.50"),
            "high": Decimal("2650.00"),
            "low": Decimal("2646.50"),
            "close": Decimal("2649.00"),
            "volume": Decimal("1678"),
        },
        {
            "symbol": "XAUUSD",
            "timeframe": "H1",
            "timestamp": datetime(2026, 2, 16, 13, 0, tzinfo=timezone.utc),
            "open": Decimal("2649.00"),
            "high": Decimal("2651.00"),
            "low": Decimal("2648.00"),
            "close": Decimal("2650.50"),
            "volume": Decimal("1890"),
        },
        {
            "symbol": "XAUUSD",
            "timeframe": "H1",
            "timestamp": datetime(2026, 2, 16, 14, 0, tzinfo=timezone.utc),
            "open": Decimal("2650.50"),
            "high": Decimal("2652.00"),
            "low": Decimal("2649.50"),
            "close": Decimal("2651.00"),
            "volume": Decimal("2012"),
        },
    ]
