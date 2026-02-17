"""FastAPI application entry point with lifespan management."""

from contextlib import asynccontextmanager
from collections.abc import AsyncGenerator

from fastapi import FastAPI
from loguru import logger

from app.config import get_settings
from app.database import engine
from app.utils.logging import setup_logging
from app.workers.scheduler import scheduler
from app.api.health import router as health_router


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan: setup on startup, teardown on shutdown."""
    settings = get_settings()

    # Configure structured logging first so all startup logs are formatted
    setup_logging(settings.log_level, settings.log_json)

    # Start background scheduler (jobs registered by other modules)
    scheduler.start()
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
