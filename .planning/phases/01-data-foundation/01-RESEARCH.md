# Phase 1: Data Foundation - Research

**Researched:** 2026-02-17
**Domain:** FastAPI + async SQLAlchemy + PostgreSQL + Twelve Data ingestion + APScheduler
**Confidence:** HIGH (verified against official docs and multiple sources)

## Summary

Phase 1 establishes the entire application skeleton and data pipeline: a FastAPI application with async SQLAlchemy/PostgreSQL, Alembic migrations, structured logging, and a Twelve Data integration that fetches XAUUSD OHLCV candles across M15, H1, H4, D1 timeframes with gap detection, caching, and scheduled refresh.

The standard approach is well-established: FastAPI lifespan events manage startup/shutdown of both the async database engine and APScheduler. SQLAlchemy 2.x with asyncpg provides async database access. Alembic's async template handles migrations. The Twelve Data Python SDK fetches candle data via REST, which is upserted into PostgreSQL using `INSERT ... ON CONFLICT DO UPDATE` to prevent duplicates. APScheduler's AsyncIOScheduler runs in-process with coalescing and misfire grace time to handle missed jobs gracefully.

**Primary recommendation:** Build a clean async FastAPI skeleton first (health check, database, migrations, logging, config), then layer on the Twelve Data ingestion service with aggressive caching and gap detection. Use PostgreSQL `generate_series()` for gap detection and SQLAlchemy's PostgreSQL-specific `insert().on_conflict_do_update()` for deduplication.

## Standard Stack

### Core

| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| FastAPI | `>=0.115.0` | Async web framework | Industry standard for Python async APIs, auto-docs, Pydantic integration |
| uvicorn | `>=0.32.0` (with `[standard]`) | ASGI server | Recommended FastAPI server, includes uvloop for performance |
| SQLAlchemy | `>=2.0.36` (with `[asyncio]`) | Async ORM | Industry standard, v2.0+ has first-class async support |
| asyncpg | `>=0.30.0` | PostgreSQL async driver | Fastest Python PostgreSQL driver, designed for asyncio |
| Alembic | `>=1.14.0` | Database migrations | SQLAlchemy's companion migration tool, async template available |
| APScheduler | `>=3.10.4,<4.0` | Background job scheduling | In-process scheduler with AsyncIOScheduler for FastAPI compatibility. Do NOT use v4.x (unstable rewrite) |
| pydantic | `>=2.10.0` | Data validation | Ships with FastAPI, v2 has major performance improvements |
| pydantic-settings | `>=2.7.0` | Environment config | Type-safe env var parsing with `.env` file support |
| loguru | `>=0.7.3` | Structured logging | Drop-in replacement for stdlib logging, JSON output, rotation, context binding |
| twelvedata | `>=1.2.5` (with `[websocket]`) | Market data SDK | Official Twelve Data Python client, REST + WebSocket |

### Supporting

| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| httpx | `>=0.28.0` | Async HTTP client | For any HTTP calls outside Twelve Data SDK (fallback data sources, health checks) |
| tenacity | `>=9.0.0` | Retry logic | Wrap all external API calls (Twelve Data, health checks) with exponential backoff |
| python-dotenv | `>=1.0.1` | .env file loading | Local development environment variable loading |
| pytest | `>=8.3.0` | Testing | Test framework |
| pytest-asyncio | `>=0.24.0` | Async test support | Required for testing async FastAPI + SQLAlchemy code |

### Alternatives Considered

| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| SQLAlchemy 2.x | SQLModel | SQLModel is simpler but less mature; use SQLAlchemy directly for production reliability |
| asyncpg | psycopg3 (async) | psycopg3 is newer with async support, but asyncpg is more battle-tested and faster for PostgreSQL |
| APScheduler 3.x | APScheduler 4.x | v4 is a full rewrite, still in alpha/beta as of early 2026 -- too unstable for production |
| loguru | structlog | structlog is more composable but requires more configuration; loguru is simpler for the same result |
| pydantic-settings | python-decouple | pydantic-settings integrates natively with FastAPI's Pydantic ecosystem |

**Installation:**
```bash
pip install "fastapi>=0.115.0" "uvicorn[standard]>=0.32.0" "sqlalchemy[asyncio]>=2.0.36" "asyncpg>=0.30.0" "alembic>=1.14.0" "APScheduler>=3.10.4,<4.0" "pydantic>=2.10.0" "pydantic-settings>=2.7.0" "loguru>=0.7.3" "twelvedata[websocket]>=1.2.5" "httpx>=0.28.0" "tenacity>=9.0.0" "python-dotenv>=1.0.1" "pytest>=8.3.0" "pytest-asyncio>=0.24.0"
```

## Architecture Patterns

### Recommended Project Structure

```
app/
├── main.py                  # FastAPI app factory with lifespan
├── config.py                # pydantic-settings Settings class
├── database.py              # Async engine, sessionmaker, get_session dependency
├── models/                  # SQLAlchemy ORM models
│   ├── __init__.py
│   ├── base.py              # DeclarativeBase
│   ├── candle.py            # Candle model
│   ├── strategy.py          # Strategy model (stub for Phase 2)
│   ├── backtest_result.py   # BacktestResult model (stub for Phase 3)
│   ├── signal.py            # Signal model (stub for Phase 4)
│   ├── outcome.py           # Outcome model (stub for Phase 6)
│   └── strategy_performance.py  # StrategyPerformance (stub for Phase 6)
├── schemas/                 # Pydantic request/response schemas
│   ├── __init__.py
│   ├── candle.py
│   └── health.py
├── api/                     # FastAPI routers
│   ├── __init__.py
│   ├── health.py            # GET /health
│   └── candles.py           # GET /candles/{timeframe} (Phase 1)
├── services/                # Business logic
│   ├── __init__.py
│   └── candle_ingestor.py   # Twelve Data fetch, validate, store
├── workers/                 # Background jobs
│   ├── __init__.py
│   ├── scheduler.py         # APScheduler setup
│   └── jobs.py              # Job functions (data refresh per timeframe)
├── utils/                   # Shared utilities
│   ├── __init__.py
│   └── logging.py           # Loguru configuration
├── alembic/                 # Alembic migrations
│   ├── env.py               # Async env.py (use `alembic init -t async`)
│   └── versions/
├── alembic.ini
├── requirements.txt
├── .env                     # Local env vars (gitignored)
└── .env.example             # Template for env vars (committed)
```

### Pattern 1: FastAPI Lifespan for Startup/Shutdown

**What:** Use FastAPI's `lifespan` asynccontextmanager to initialize and teardown the database engine, run Alembic migrations (optionally), and start/stop APScheduler.

**When to use:** Always -- this is the modern pattern replacing deprecated `@app.on_event("startup")`.

**Example:**
```python
# Source: FastAPI official docs + community best practice
from contextlib import asynccontextmanager
from fastapi import FastAPI
from app.database import engine, async_session_factory
from app.workers.scheduler import scheduler

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    scheduler.start()
    yield
    # Shutdown
    scheduler.shutdown(wait=False)
    await engine.dispose()

app = FastAPI(title="GoldSignal", lifespan=lifespan)
```

### Pattern 2: Async Session Dependency with Yield

**What:** Provide an async SQLAlchemy session per request via FastAPI dependency injection, ensuring proper cleanup.

**When to use:** Every route that needs database access.

**Example:**
```python
# Source: SQLAlchemy 2.0 docs + FastAPI patterns
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from typing import AsyncGenerator
from fastapi import Depends

DATABASE_URL = "postgresql+asyncpg://user:pass@localhost:5432/goldsignal"

engine = create_async_engine(
    DATABASE_URL,
    pool_size=5,
    max_overflow=10,
    pool_pre_ping=True,
    pool_recycle=300,
)
async_session_factory = async_sessionmaker(engine, expire_on_commit=False)

async def get_session() -> AsyncGenerator[AsyncSession, None]:
    async with async_session_factory() as session:
        yield session

# Usage in route:
@router.get("/candles/{timeframe}")
async def get_candles(timeframe: str, session: AsyncSession = Depends(get_session)):
    ...
```

### Pattern 3: PostgreSQL Upsert for Candle Deduplication

**What:** Use SQLAlchemy's PostgreSQL-specific `insert().on_conflict_do_update()` to handle repeated fetches without creating duplicate candles.

**When to use:** Every candle ingestion operation.

**Example:**
```python
# Source: SQLAlchemy 2.0 PostgreSQL dialect docs
from sqlalchemy.dialects.postgresql import insert as pg_insert
from app.models.candle import Candle

async def upsert_candles(session: AsyncSession, candles: list[dict]):
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
    await session.execute(stmt)
    await session.commit()
```

### Pattern 4: Gap Detection with generate_series()

**What:** Use PostgreSQL `generate_series()` to detect missing candles by comparing expected timestamps against actual stored timestamps.

**When to use:** After each ingestion run and as a periodic health check.

**Example:**
```python
# Source: PostgreSQL docs + community pattern for time series gap detection
GAP_DETECTION_SQL = """
SELECT expected_ts
FROM generate_series(
    :start_ts,
    :end_ts,
    :interval_val::interval
) AS expected_ts
LEFT JOIN candles c
    ON c.symbol = :symbol
    AND c.timeframe = :timeframe
    AND c.timestamp = expected_ts
WHERE c.id IS NULL
ORDER BY expected_ts;
"""
```

### Pattern 5: APScheduler AsyncIOScheduler with Coalescing

**What:** Use APScheduler's AsyncIOScheduler with coalescing enabled and misfire_grace_time to handle missed jobs after restarts.

**When to use:** All scheduled data refresh jobs.

**Example:**
```python
# Source: APScheduler 3.x docs
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore

scheduler = AsyncIOScheduler(
    jobstores={
        "default": SQLAlchemyJobStore(url=DATABASE_URL_SYNC),  # sync URL for job store
    },
    job_defaults={
        "coalesce": True,           # Roll missed executions into one
        "misfire_grace_time": 300,  # 5-minute grace for missed jobs
        "max_instances": 1,         # Prevent concurrent runs of same job
    },
    timezone="UTC",
)
```

### Pattern 6: Pydantic Settings for Configuration

**What:** Use pydantic-settings BaseSettings for type-safe environment variable management with `.env` file support and `@lru_cache` for singleton.

**When to use:** All configuration access.

**Example:**
```python
# Source: pydantic-settings docs + FastAPI official docs
from pydantic_settings import BaseSettings, SettingsConfigDict
from functools import lru_cache

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    database_url: str
    twelve_data_api_key: str
    log_level: str = "INFO"
    candle_refresh_interval_m15: int = 900   # seconds
    candle_refresh_interval_h1: int = 3600
    candle_refresh_interval_h4: int = 14400
    candle_refresh_interval_d1: int = 86400

@lru_cache
def get_settings() -> Settings:
    return Settings()
```

### Anti-Patterns to Avoid

- **Mixing sync and async SQLAlchemy:** Never use synchronous `Session` in an async FastAPI app. Always use `AsyncSession` with `create_async_engine`. Mixing causes event loop blocking and deadlocks.
- **Using `@app.on_event("startup")`:** Deprecated in FastAPI 0.95+. Use `lifespan` asynccontextmanager instead.
- **Global mutable session objects:** Never create a single session at module level. Use dependency injection with `yield` to get a session per request.
- **APScheduler v4.x in production:** Still in alpha. Stick with v3.10+ for stability.
- **SELECT * queries:** Always specify columns explicitly. Prevents breakage when schema evolves.
- **Storing timestamps without timezone:** Always use `TIMESTAMPTZ` in PostgreSQL and `timezone=True` in SQLAlchemy column definitions.
- **Polling Twelve Data too aggressively:** Free tier has 800 req/day. With 4 timeframes, aggressive polling exhausts the budget quickly. Cache heavily.

## Don't Hand-Roll

Problems that look simple but have existing solutions:

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Database connection pooling | Custom connection manager | SQLAlchemy's built-in `AsyncAdaptedQueuePool` (default with `create_async_engine`) | Handles connection lifecycle, recycling, pre-ping, overflow automatically |
| Schema migrations | Manual SQL scripts | Alembic with `alembic init -t async` template | Version-controlled, reversible, autogenerate from models, async-compatible |
| Retry logic for API calls | Custom retry loops | `tenacity` library with `@retry(stop=stop_after_attempt(3), wait=wait_exponential())` | Handles backoff, jitter, logging, exception filtering |
| Environment config parsing | Custom env parser | `pydantic-settings` BaseSettings | Type validation, `.env` file support, nested models, secret management |
| Candle deduplication | Custom check-then-insert | PostgreSQL `ON CONFLICT DO UPDATE` via SQLAlchemy's `pg_insert` | Atomic, no race conditions, single round-trip |
| Time series gap detection | Python-side iteration | PostgreSQL `generate_series()` LEFT JOIN anti-pattern | Database-side, handles timezone-aware intervals, much faster than Python |
| Logging with context | stdlib logging config | `loguru` with `bind()` for context and `serialize=True` for JSON | Automatic rotation, compression, retention, structured output, no handler configuration |
| Job scheduling | Custom asyncio.sleep loops | APScheduler `AsyncIOScheduler` with cron/interval triggers | Persistence, coalescing, misfire handling, timezone support |

**Key insight:** Every "simple" problem above has edge cases that consume days of debugging. Connection pool exhaustion, migration conflicts, retry storms, timezone-naive timestamps, race conditions in dedup -- these are all solved problems. Use the libraries.

## Common Pitfalls

### Pitfall 1: Twelve Data XAU/USD Plan Tier Ambiguity

**What goes wrong:** The Twelve Data free/Basic tier advertises "forex" access, but XAU/USD is classified as a "commodity" on their exchange page with a "Grow+" badge. Development may begin assuming free tier access, then discover that XAU/USD requires a paid plan ($79/mo Grow or higher).

**Why it happens:** XAU/USD sits at the boundary between forex and commodities in Twelve Data's classification. The free tier explicitly covers "US markets, forex, and cryptocurrencies" but commodities are a separate category.

**How to avoid:** Before writing any ingestion code, make a test API call with a free-tier API key to `GET /time_series?symbol=XAU/USD&interval=15min&outputsize=5`. If it returns data, the free tier works. If it returns an access error, the Grow plan ($79/mo) is required. Budget accordingly.

**Warning signs:** 401 or 403 errors from Twelve Data when requesting XAU/USD data. Error messages mentioning "upgrade your plan."

**Confidence:** LOW -- conflicting information between pricing page and support docs. Must be validated empirically.

### Pitfall 2: Twelve Data Free Tier Rate Limit Exhaustion (800 req/day)

**What goes wrong:** With 4 timeframes (M15, H1, H4, D1), each requiring periodic refresh, the 800 daily request budget gets consumed quickly. During development with frequent restarts and testing, the budget is exhausted before end of day.

**Why it happens:** Each `time_series` API call consumes 1 credit. Refreshing 4 timeframes every candle close = up to 96 M15 calls + 24 H1 calls + 6 H4 calls + 1 D1 call = 127 calls/day minimum. That leaves ~670 for historical backfill, testing, and retries. Tight but manageable -- unless development involves many restarts or you need larger historical pulls.

**How to avoid:**
1. Cache aggressively: after initial historical backfill, only fetch candles newer than the latest stored timestamp.
2. Use `outputsize` parameter wisely: request only the number of new candles expected since last fetch, not the maximum 5000.
3. Use `start_date` parameter to fetch only new data: `start_date = latest_stored_timestamp + 1 interval`.
4. Track API credit usage in the application and log warnings at 50%, 75%, 90% thresholds.
5. Consider separate API keys for development and production.

**Warning signs:** 429 rate limit errors. Empty response bodies. Twelve Data dashboard showing high credit usage.

### Pitfall 3: Timezone Inconsistency Between Data Sources and Storage

**What goes wrong:** Twelve Data returns timestamps in the timezone you request (default: Exchange timezone). If you don't explicitly request UTC, candle timestamps may be in a broker's local timezone. Storing these without conversion means every downstream calculation (gap detection, session filtering, schedule alignment) is wrong.

**Why it happens:** The `timezone` parameter in Twelve Data's `time_series()` defaults to "Exchange" which for forex/commodities varies. Developers forget to set `timezone="UTC"` explicitly.

**How to avoid:**
1. ALWAYS pass `timezone="UTC"` in every Twelve Data API call.
2. Store all timestamps as `TIMESTAMPTZ` in PostgreSQL (not `TIMESTAMP`).
3. Use `DateTime(timezone=True)` in SQLAlchemy model columns.
4. Add a validation check in the ingestion pipeline that rejects any candle with a timezone-naive timestamp.
5. Write a test that verifies stored candle timestamps are UTC.

**Warning signs:** Gap detection finds "gaps" that correspond to timezone offset hours (e.g., missing candles at UTC midnight that exist at EST midnight). Candle counts per day don't match expectations.

### Pitfall 4: APScheduler Job Store Requires Sync Database URL

**What goes wrong:** APScheduler 3.x's `SQLAlchemyJobStore` uses synchronous SQLAlchemy internally. If you pass the async database URL (`postgresql+asyncpg://...`), it fails because asyncpg is an async-only driver.

**Why it happens:** APScheduler 3.x predates SQLAlchemy's async support. The job store was built for sync engines only.

**How to avoid:** Provide a separate synchronous database URL for the job store:
```python
# Async URL for the app: postgresql+asyncpg://user:pass@localhost/goldsignal
# Sync URL for APScheduler: postgresql+psycopg2://user:pass@localhost/goldsignal
```
This requires adding `psycopg2-binary` (or `psycopg2`) as a dependency alongside `asyncpg`. Alternatively, use the simpler `MemoryJobStore` (no persistence across restarts) if job persistence is not critical in Phase 1.

**Warning signs:** Error on startup: `No module named 'asyncpg'` from APScheduler, or `Cannot use async driver with synchronous engine`.

### Pitfall 5: Missing Candles During Non-Trading Hours

**What goes wrong:** Gold (XAU/USD) trades nearly 24 hours on weekdays but has a daily maintenance break (~5pm ET for ~1 hour) and is closed on weekends (Friday 5pm ET to Sunday 5pm ET). Gap detection flags these expected non-trading periods as "missing" candles, flooding logs with false positives.

**Why it happens:** Gap detection uses `generate_series()` which creates evenly spaced timestamps 24/7. It doesn't know about market hours.

**How to avoid:**
1. Define a trading hours calendar for XAUUSD: Monday 00:00 UTC to Friday 22:00 UTC (approximately), with a daily break around 21:00-22:00 UTC.
2. Filter `generate_series()` output through the trading calendar before comparing with actual candles.
3. Alternatively, only flag gaps during known trading hours and ignore gaps during non-trading periods.
4. Mark weekend/holiday gaps differently from mid-session gaps in logging.

**Warning signs:** Gap detection reports hundreds of "missing" candles every weekend. False alerts for the daily maintenance window.

### Pitfall 6: Concurrent APScheduler Job Execution

**What goes wrong:** If a data refresh job takes longer than its interval (e.g., M15 job takes 20 minutes due to API slowness), APScheduler starts a second instance of the same job. Both jobs fetch and upsert the same data, doubling API credit usage and potentially causing deadlocks.

**Why it happens:** APScheduler's default `max_instances` is 1, but if not explicitly set, or if coalescing is not enabled, multiple executions can queue up.

**How to avoid:** Set `max_instances=1` and `coalesce=True` in job defaults:
```python
scheduler = AsyncIOScheduler(
    job_defaults={"coalesce": True, "max_instances": 1}
)
```

**Warning signs:** Duplicate log entries for the same ingestion cycle. API credit usage higher than expected. Database deadlock warnings.

### Pitfall 7: DECIMAL Precision for Gold Prices

**What goes wrong:** Gold prices are quoted to 2 decimal places (e.g., 2645.50) but some data sources provide more precision. Using `DECIMAL(10,5)` as shown in some examples wastes storage on unnecessary decimal places and may cause rounding confusion. Using `FLOAT` causes floating-point arithmetic errors that compound in indicator calculations.

**Why it happens:** Different data providers have different precision levels. Gold is typically quoted to 2 decimal places but Twelve Data may return more.

**How to avoid:** Use `NUMERIC(10,2)` for OHLC prices (matches gold's 2-decimal-place quoting convention). Use `NUMERIC(15,2)` for volume. Verify Twelve Data's actual precision for XAU/USD and match it. Never use `FLOAT` or `DOUBLE PRECISION` for financial prices -- always use `NUMERIC/DECIMAL`.

**Warning signs:** Prices stored as `2645.50000` with trailing zeros. Indicator calculations producing slightly different results than TradingView.

## Code Examples

Verified patterns from official sources:

### Alembic Async env.py Setup

```python
# Source: Alembic async template (alembic init -t async)
# alembic/env.py
import asyncio
from logging.config import fileConfig
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import async_engine_from_config
from alembic import context
from app.models.base import Base  # Import your DeclarativeBase

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()

def do_run_migrations(connection):
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()

async def run_async_migrations():
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()

def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())

if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
```

### SQLAlchemy Candle Model

```python
# Source: SQLAlchemy 2.0 docs + project schema design
from sqlalchemy import String, Numeric, DateTime, BigInteger, UniqueConstraint, Index
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from datetime import datetime

class Base(DeclarativeBase):
    pass

class Candle(Base):
    __tablename__ = "candles"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(10), nullable=False, default="XAUUSD")
    timeframe: Mapped[str] = mapped_column(String(5), nullable=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    open: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)
    high: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)
    low: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)
    close: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)
    volume: Mapped[float] = mapped_column(Numeric(15, 2), nullable=True)

    __table_args__ = (
        UniqueConstraint("symbol", "timeframe", "timestamp", name="uq_candle_identity"),
        Index("idx_candles_lookup", "symbol", "timeframe", "timestamp"),
    )
```

### Twelve Data Candle Ingestion Service

```python
# Source: Twelve Data Python SDK GitHub + project requirements
from twelvedata import TDClient
from datetime import datetime, timezone
from loguru import logger

class CandleIngestor:
    # Twelve Data interval mapping
    INTERVAL_MAP = {
        "M15": "15min",
        "H1":  "1h",
        "H4":  "4h",
        "D1":  "1day",
    }

    def __init__(self, api_key: str):
        self.client = TDClient(apikey=api_key)

    def fetch_candles(
        self,
        symbol: str = "XAU/USD",
        timeframe: str = "M15",
        outputsize: int = 100,
        start_date: str | None = None,
    ) -> list[dict]:
        """Fetch OHLCV candles from Twelve Data."""
        interval = self.INTERVAL_MAP[timeframe]

        params = {
            "symbol": symbol,
            "interval": interval,
            "outputsize": outputsize,
            "timezone": "UTC",       # CRITICAL: always UTC
            "order": "asc",
        }
        if start_date:
            params["start_date"] = start_date

        ts = self.client.time_series(**params)
        df = ts.as_pandas()

        candles = []
        for idx, row in df.iterrows():
            candles.append({
                "symbol": "XAUUSD",
                "timeframe": timeframe,
                "timestamp": idx.to_pydatetime().replace(tzinfo=timezone.utc),
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
                "volume": float(row["volume"]) if "volume" in row else None,
            })

        logger.info(
            "Fetched candles",
            symbol=symbol,
            timeframe=timeframe,
            count=len(candles),
        )
        return candles
```

### Loguru Configuration

```python
# Source: loguru docs + FastAPI community patterns
import sys
from loguru import logger

def setup_logging(log_level: str = "INFO", json_output: bool = False):
    """Configure loguru for the application."""
    logger.remove()  # Remove default handler

    if json_output:
        logger.add(
            sys.stderr,
            level=log_level,
            serialize=True,  # JSON output for production
            enqueue=True,    # Thread-safe, async-safe
        )
    else:
        logger.add(
            sys.stderr,
            level=log_level,
            format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
                   "<level>{level: <8}</level> | "
                   "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> | "
                   "<level>{message}</level>",
            enqueue=True,
        )

    # Intercept standard library logging (uvicorn, sqlalchemy, etc.)
    import logging

    class InterceptHandler(logging.Handler):
        def emit(self, record):
            level = logger.level(record.levelname).name
            logger.opt(depth=6, exception=record.exc_info).log(level, record.getMessage())

    logging.basicConfig(handlers=[InterceptHandler()], level=0, force=True)
```

### Async Test Fixture Pattern

```python
# Source: pytest-asyncio + FastAPI async testing docs
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from httpx import AsyncClient, ASGITransport
from app.main import app
from app.models.base import Base
from app.database import get_session

TEST_DATABASE_URL = "postgresql+asyncpg://test:test@localhost:5432/goldsignal_test"

@pytest_asyncio.fixture
async def db_engine():
    engine = create_async_engine(TEST_DATABASE_URL)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()

@pytest_asyncio.fixture
async def db_session(db_engine):
    session_factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with session_factory() as session:
        yield session

@pytest_asyncio.fixture
async def client(db_session):
    async def override_get_session():
        yield db_session

    app.dependency_overrides[get_session] = override_get_session
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        yield ac
    app.dependency_overrides.clear()
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| `@app.on_event("startup")` | `lifespan` asynccontextmanager | FastAPI 0.95.0 (2023) | Old approach is deprecated; lifespan is cleaner, supports context sharing |
| SQLAlchemy 1.x sync Session | SQLAlchemy 2.x `AsyncSession` | SQLAlchemy 2.0 (Jan 2023) | Full async support, new `select()` style queries, better typing |
| `Session(bind=engine)` | `async_sessionmaker(engine)` | SQLAlchemy 2.0 | sessionmaker is the factory pattern, not manual Session construction |
| `alembic init` (sync only) | `alembic init -t async` | Alembic 1.7+ | Built-in async template with `async_engine_from_config` |
| APScheduler `BackgroundScheduler` | APScheduler `AsyncIOScheduler` | APScheduler 3.7+ | Integrates with asyncio event loop, required for FastAPI |
| stdlib `logging` | `loguru` | loguru 0.5+ (mature since 2020) | No handler configuration needed, structured output, rotation built-in |
| `python-dotenv` for config | `pydantic-settings` BaseSettings | pydantic-settings 2.0 (2023) | Type-safe, validates at startup, integrates with FastAPI's Pydantic |

**Deprecated/outdated:**
- `databases` library (encode/databases): Was popular for async DB access pre-SQLAlchemy 2.0. No longer needed -- SQLAlchemy 2.x has native async support.
- `@app.on_event("startup"/"shutdown")`: Deprecated in FastAPI, replaced by `lifespan`.
- APScheduler v4.x: Exists but is unstable alpha. Do not use in production.

## Open Questions

1. **Twelve Data XAU/USD Free Tier Access**
   - What we know: The pricing page lists XAU/USD under "commodities" with a "Grow+" badge. The free tier covers "forex." XAU/USD is ambiguously classified.
   - What's unclear: Whether the free tier API key can actually fetch XAU/USD time_series data.
   - Recommendation: Make a test API call with a free-tier key BEFORE writing ingestion code. If blocked, budget $79/mo for the Grow plan. Design the ingestion service with a clear interface so the data source can be swapped later if needed.
   - **Confidence:** LOW -- must be validated empirically.

2. **APScheduler Job Store: Sync Driver Requirement**
   - What we know: APScheduler 3.x SQLAlchemyJobStore requires a synchronous database URL. The app uses asyncpg (async only).
   - What's unclear: Whether the overhead of adding `psycopg2-binary` as a second driver is worthwhile vs. using `MemoryJobStore`.
   - Recommendation: For Phase 1, use `MemoryJobStore` (simpler, no extra dependency). Jobs are re-registered on startup anyway since they're defined in code. If job persistence becomes important in later phases, add `psycopg2-binary` then.
   - **Confidence:** HIGH -- verified in APScheduler docs.

3. **DECIMAL Precision for Gold Prices**
   - What we know: Gold is quoted to 2 decimal places on most platforms. Twelve Data may return different precision.
   - What's unclear: Exact precision returned by Twelve Data for XAU/USD.
   - Recommendation: Start with `NUMERIC(10, 2)`. Check actual Twelve Data response during the first integration test. Adjust via Alembic migration if needed.
   - **Confidence:** MEDIUM -- standard convention is 2 decimal places but should be verified.

4. **XAUUSD Trading Hours Calendar**
   - What we know: Gold trades ~23 hours/day on weekdays (with a ~1hr daily break near 5pm ET/22:00 UTC) and is closed weekends.
   - What's unclear: Exact break times which vary by broker/exchange. Twelve Data's specific trading hours for XAU/USD.
   - Recommendation: Implement a conservative trading calendar (Mon 00:00 - Fri 21:00 UTC, daily break 21:45-22:15 UTC). Refine based on actual gap patterns observed after initial data ingestion.
   - **Confidence:** MEDIUM -- approximate hours known, exact break times need empirical validation.

5. **Candle Refresh Schedule Alignment**
   - What we know: DATA-05 requires refresh "aligned to candle close times." M15 closes every 15 minutes, H1 every hour, etc.
   - What's unclear: How much delay to add after candle close to ensure the data provider has processed the candle. Fetching immediately at close may return incomplete data.
   - Recommendation: Add a 30-60 second delay after expected candle close before fetching. For example, M15 candle closing at 14:15 UTC should be fetched at ~14:16 UTC. This gives Twelve Data time to finalize the candle.
   - **Confidence:** MEDIUM -- common practice but exact delay depends on Twelve Data's processing latency.

## Sources

### Primary (HIGH confidence)
- [SQLAlchemy 2.0 Async Documentation](https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html) -- AsyncSession, create_async_engine patterns
- [SQLAlchemy 2.0 PostgreSQL Dialect](https://docs.sqlalchemy.org/en/20/dialects/postgresql.html) -- ON CONFLICT DO UPDATE (upsert)
- [Alembic Async Template](https://github.com/sqlalchemy/alembic/blob/main/alembic/templates/async/env.py) -- Official async env.py
- [Alembic Cookbook](https://alembic.sqlalchemy.org/en/latest/cookbook.html) -- Async engine patterns
- [APScheduler 3.x User Guide](https://apscheduler.readthedocs.io/en/3.x/userguide.html) -- AsyncIOScheduler, job stores, misfire handling
- [FastAPI Lifespan Events](https://fastapi.tiangolo.com/advanced/events/) -- Modern startup/shutdown pattern
- [FastAPI Settings & Environment Variables](https://fastapi.tiangolo.com/advanced/settings/) -- pydantic-settings integration
- [Twelve Data Python SDK](https://github.com/twelvedata/twelvedata-python) -- Client initialization, time_series API
- [Twelve Data API Docs](https://twelvedata.com/docs) -- Endpoints, parameters, intervals, output formats
- [PostgreSQL NUMERIC Type](https://www.postgresql.org/docs/current/datatype-numeric.html) -- DECIMAL/NUMERIC equivalence, precision

### Secondary (MEDIUM confidence)
- [Setup FastAPI with Async SQLAlchemy 2, Alembic, PostgreSQL & Docker](https://berkkaraal.com/blog/2024/09/19/setup-fastapi-project-with-async-sqlalchemy-2-alembic-postgresql-and-docker/) -- Complete project setup guide
- [FastAPI + SQLAlchemy 2.0: Modern Async Database Patterns](https://dev-faizan.medium.com/fastapi-sqlalchemy-2-0-modern-async-database-patterns-7879d39b6843) -- Production patterns (Dec 2025)
- [Detecting Gaps in Time-Series Data in PostgreSQL](https://www.endpointdev.com/blog/2020/10/postgresql-finding-gaps-in-time-series-data/) -- generate_series() gap detection
- [Loguru Structured Logging Guide](https://www.dash0.com/guides/python-logging-with-loguru) -- Production loguru configuration
- [Scheduled Jobs with FastAPI and APScheduler](https://sentry.io/answers/schedule-tasks-with-fastapi/) -- Lifespan integration pattern

### Tertiary (LOW confidence)
- [Twelve Data Pricing](https://twelvedata.com/pricing) -- Plan tiers and credit limits (conflicting info on XAU/USD access tier)
- [Twelve Data Trial Support Article](https://support.twelvedata.com/en/articles/5335783-trial) -- Free tier limits

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH -- all libraries are well-established, versions verified against official sources
- Architecture: HIGH -- patterns verified from official FastAPI, SQLAlchemy, Alembic documentation
- Pitfalls: MEDIUM -- most pitfalls sourced from project research docs and community experience; Twelve Data tier question is LOW
- Code examples: HIGH -- derived from official documentation templates and verified patterns

**Research date:** 2026-02-17
**Valid until:** 2026-03-17 (30 days -- stack is stable, Twelve Data pricing may change)
