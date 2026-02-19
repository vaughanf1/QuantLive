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
