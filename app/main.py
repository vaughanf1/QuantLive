"""FastAPI application entry point with lifespan management."""

from contextlib import asynccontextmanager
from collections.abc import AsyncGenerator

from fastapi import FastAPI
from loguru import logger
from sqlalchemy import func, select

from app.config import get_settings
from app.database import async_session_factory, engine
from app.utils.logging import setup_logging
from app.workers.scheduler import register_jobs, scheduler
from app.api.candles import router as candles_router
from app.api.chart import router as chart_router
from app.api.dashboard import router as dashboard_router
from app.api.health import router as health_router
from app.api.status import router as status_router


async def bootstrap_data() -> None:
    """Seed strategies, backfill candles, and run backtests if DB is empty.

    This ensures a fresh Railway deploy becomes operational immediately
    instead of waiting hours for scheduled jobs to populate data.
    """
    from app.models.candle import Candle
    from app.models.strategy import Strategy
    from app.models.backtest_result import BacktestResult
    from app.strategies.base import BaseStrategy
    import app.strategies.liquidity_sweep  # noqa: F401
    import app.strategies.trend_continuation  # noqa: F401
    import app.strategies.breakout_expansion  # noqa: F401
    from app.services.candle_ingestor import CandleIngestor

    settings = get_settings()

    async with async_session_factory() as session:
        # --- Step 1: Seed strategies ---
        existing = await session.execute(select(Strategy))
        existing_names = {s.name for s in existing.scalars().all()}
        registry = BaseStrategy.get_registry()
        created = []
        for name in registry:
            if name not in existing_names:
                session.add(Strategy(name=name, is_active=True))
                created.append(name)
        if created:
            await session.commit()
            logger.info("Bootstrap: seeded strategies: {}", created)
        else:
            logger.info("Bootstrap: strategies already exist: {}", list(existing_names))

        # --- Step 2: Backfill H1 candles if insufficient ---
        result = await session.execute(
            select(func.count()).select_from(Candle).where(
                Candle.symbol == "XAUUSD", Candle.timeframe == "H1"
            )
        )
        h1_count = result.scalar() or 0
        min_needed = 800  # 30 days * 24 bars + buffer

        if h1_count < min_needed:
            logger.info("Bootstrap: only {} H1 candles (need {}), backfilling...", h1_count, min_needed)
            ingestor = CandleIngestor(api_key=settings.twelve_data_api_key)
            try:
                candles = await ingestor.fetch_candles("XAUUSD", "H1", outputsize=5000)
                count = await ingestor.upsert_candles(session, candles)
                logger.info("Bootstrap: backfilled {} H1 candles", count)
            except Exception:
                logger.exception("Bootstrap: H1 backfill failed")
        else:
            logger.info("Bootstrap: H1 candles OK ({} rows)", h1_count)

        # Also backfill H4 and D1 if empty (needed for confluence checks)
        for tf, size in [("H4", 5000), ("D1", 5000)]:
            result = await session.execute(
                select(func.count()).select_from(Candle).where(
                    Candle.symbol == "XAUUSD", Candle.timeframe == tf
                )
            )
            tf_count = result.scalar() or 0
            if tf_count < 100:
                logger.info("Bootstrap: backfilling {} candles...", tf)
                ingestor = CandleIngestor(api_key=settings.twelve_data_api_key)
                try:
                    candles = await ingestor.fetch_candles("XAUUSD", tf, outputsize=size)
                    count = await ingestor.upsert_candles(session, candles)
                    logger.info("Bootstrap: backfilled {} {} candles", count, tf)
                except Exception:
                    logger.exception("Bootstrap: {} backfill failed", tf)

    # --- Step 3: Run backtests if none exist ---
    async with async_session_factory() as session:
        result = await session.execute(
            select(func.count()).select_from(BacktestResult)
        )
        bt_count = result.scalar() or 0

        if bt_count == 0:
            logger.info("Bootstrap: no backtest results, running initial backtests...")
            try:
                from app.workers.jobs import run_daily_backtests
                await run_daily_backtests()
                logger.info("Bootstrap: initial backtests complete")
            except Exception:
                logger.exception("Bootstrap: backtests failed")
        else:
            logger.info("Bootstrap: backtest results OK ({} rows)", bt_count)

    logger.info("Bootstrap: data initialization complete")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan: setup on startup, teardown on shutdown."""
    settings = get_settings()

    # Configure structured logging first so all startup logs are formatted
    setup_logging(settings.log_level, settings.log_json)

    # Bootstrap data (seed strategies, backfill candles, run backtests)
    try:
        await bootstrap_data()
    except Exception:
        logger.exception("Bootstrap failed -- continuing with scheduler")

    # Start background scheduler and register candle refresh jobs
    scheduler.start()
    register_jobs()
    logger.info("GoldSignal application started")

    yield

    # Graceful shutdown
    scheduler.shutdown(wait=False)
    await engine.dispose()
    logger.info("GoldSignal application stopped")


app = FastAPI(
    title="GoldSignal",
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(health_router)
app.include_router(status_router)
app.include_router(candles_router)
app.include_router(chart_router)
app.include_router(dashboard_router)
