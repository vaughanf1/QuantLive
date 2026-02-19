"""Detailed /status diagnostic endpoint for operational visibility."""

import traceback
from datetime import UTC, datetime

from fastapi import APIRouter, Depends
from loguru import logger
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session
from app.models.candle import Candle
from app.models.signal import Signal
from app.schemas.status import SchedulerJobInfo, StatusResponse
from app.workers.scheduler import scheduler

router = APIRouter(tags=["status"])

# Track application start time for uptime calculation
_start_time: datetime = datetime.now(UTC)


@router.get("/status", response_model=StatusResponse)
async def status(
    session: AsyncSession = Depends(get_session),
) -> StatusResponse:
    """Return detailed operational diagnostics.

    Checks database connectivity, scheduler state, active signal count,
    last candle fetch timestamp, and application uptime. Intended for
    on-demand monitoring -- not called by Railway healthcheck.
    """
    now = datetime.now(UTC)
    uptime = (now - _start_time).total_seconds()

    # Database connectivity check
    db_status = "connected"
    try:
        await session.execute(text("SELECT 1"))
    except Exception as exc:
        logger.error(f"Status check -- database error: {exc}")
        db_status = "disconnected"

    # Scheduler state and job listing
    scheduler_status = "running" if scheduler.running else "stopped"
    jobs: list[SchedulerJobInfo] = []
    for job in scheduler.get_jobs():
        jobs.append(
            SchedulerJobInfo(
                id=job.id,
                name=job.name,
                next_run_time=job.next_run_time,
                trigger=str(job.trigger),
            )
        )

    # Active signal count
    active_signals = 0
    try:
        result = await session.execute(
            select(func.count()).select_from(Signal).where(Signal.status == "active")
        )
        active_signals = result.scalar_one()
    except Exception as exc:
        logger.error(f"Status check -- active signals query error: {exc}")

    # Last candle fetch timestamp
    last_candle_fetch = None
    try:
        result = await session.execute(select(func.max(Candle.timestamp)))
        last_candle_fetch = result.scalar_one()
    except Exception as exc:
        logger.error(f"Status check -- last candle fetch query error: {exc}")

    # Last signal generated timestamp
    last_signal_generated = None
    try:
        result = await session.execute(select(func.max(Signal.created_at)))
        last_signal_generated = result.scalar_one()
    except Exception as exc:
        logger.error(f"Status check -- last signal query error: {exc}")

    # Determine overall status
    overall_status = (
        "ok" if db_status == "connected" and scheduler_status == "running" else "degraded"
    )

    return StatusResponse(
        status=overall_status,
        uptime_seconds=round(uptime, 1),
        database=db_status,
        scheduler=scheduler_status,
        jobs=jobs,
        active_signals=active_signals,
        last_candle_fetch=last_candle_fetch,
        last_signal_generated=last_signal_generated,
        timestamp=now,
    )


@router.get("/debug/api-test")
async def debug_api_test():
    """Test Twelve Data API connectivity from Railway."""
    import httpx
    from app.config import get_settings

    settings = get_settings()
    key = settings.twelve_data_api_key
    key_preview = f"{key[:6]}...{key[-4:]}" if len(key) > 10 else "TOO_SHORT"

    results = {"api_key_preview": key_preview}

    # Test price endpoint
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                "https://api.twelvedata.com/price",
                params={"symbol": "XAU/USD", "apikey": key},
            )
            results["price_status"] = resp.status_code
            results["price_body"] = resp.json()
    except Exception as exc:
        results["price_error"] = f"{type(exc).__name__}: {exc}"

    # Test time_series endpoint
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                "https://api.twelvedata.com/time_series",
                params={
                    "symbol": "XAU/USD",
                    "interval": "1h",
                    "outputsize": "3",
                    "apikey": key,
                },
            )
            results["candle_status"] = resp.status_code
            body = resp.json()
            results["candle_ok"] = body.get("status") == "ok"
            results["candle_count"] = len(body.get("values", []))
    except Exception as exc:
        results["candle_error"] = f"{type(exc).__name__}: {exc}"

    # Test DB write with a direct ingestor call
    try:
        from app.database import async_session_factory
        from app.services.candle_ingestor import CandleIngestor

        ingestor = CandleIngestor(api_key=key)
        async with async_session_factory() as session:
            count = await ingestor.fetch_and_store(session, "XAUUSD", "H1")
            results["ingest_h1_count"] = count
    except Exception as exc:
        results["ingest_error"] = f"{type(exc).__name__}: {exc}"

    return results


@router.post("/trigger/{job_name}")
async def trigger_job(job_name: str):
    """Manually trigger a job and return the result or error.

    Supported: refresh_candles_H1, run_daily_backtests, run_signal_scanner
    """
    from app.workers.jobs import (
        check_outcomes,
        refresh_candles,
        run_daily_backtests,
        run_signal_scanner,
    )

    job_map = {
        "refresh_candles_M15": lambda: refresh_candles("M15"),
        "refresh_candles_H1": lambda: refresh_candles("H1"),
        "refresh_candles_H4": lambda: refresh_candles("H4"),
        "refresh_candles_D1": lambda: refresh_candles("D1"),
        "run_daily_backtests": run_daily_backtests,
        "run_signal_scanner": run_signal_scanner,
        "check_outcomes": check_outcomes,
    }

    if job_name not in job_map:
        return {"error": f"Unknown job: {job_name}", "available": list(job_map.keys())}

    try:
        await job_map[job_name]()
        return {"status": "ok", "job": job_name}
    except Exception as exc:
        tb = traceback.format_exc()
        logger.exception("Manual trigger failed: {}", job_name)
        return {"status": "error", "job": job_name, "error": str(exc), "traceback": tb}
