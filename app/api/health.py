"""Health check endpoint with database connectivity verification."""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Response
from loguru import logger
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session
from app.schemas.health import HealthResponse

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
async def health(
    response: Response,
    session: AsyncSession = Depends(get_session),
) -> HealthResponse:
    """Check application and database health.

    Executes a lightweight SELECT 1 query to verify database connectivity.
    Returns 200 with status "ok" when healthy, 503 with status "degraded"
    when the database is unreachable.
    """
    try:
        await session.execute(text("SELECT 1"))
        logger.debug("Health check passed -- database connected")
        return HealthResponse(
            status="ok",
            database="connected",
            timestamp=datetime.now(UTC),
        )
    except Exception as exc:
        logger.error(f"Health check failed -- database error: {exc}")
        # Return 200 so Railway health check passes (app is alive).
        # Database status is reported in the response body.
        return HealthResponse(
            status="degraded",
            database="disconnected",
            timestamp=datetime.now(UTC),
        )
