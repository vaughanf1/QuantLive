"""APScheduler setup for background task scheduling.

Uses AsyncIOScheduler with in-memory job store. Candle refresh jobs and
outcome detection are registered via register_jobs() called from the
application lifespan.
"""

from apscheduler.jobstores.memory import MemoryJobStore
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from loguru import logger

from app.workers.jobs import (
    check_outcomes,
    refresh_candles,
    run_daily_backtests,
    run_data_retention,
    run_param_optimization,
    run_signal_scanner,
    send_health_digest,
)

scheduler = AsyncIOScheduler(
    jobstores={
        "default": MemoryJobStore(),
    },
    job_defaults={
        "coalesce": True,
        "misfire_grace_time": 300,
        "max_instances": 1,
    },
    timezone="UTC",
)


def register_jobs() -> None:
    """Register all candle refresh jobs with cron triggers.

    Each job is offset by 1 minute after the candle close to ensure the
    candle is fully closed before fetching (per Twelve Data recommendation
    of 30-60 second delay).

    Schedule:
        M15: every 15 minutes at :01, :16, :31, :46
        H1:  every hour at :01
        H4:  every 4 hours at :01 (00:01, 04:01, 08:01, 12:01, 16:01, 20:01)
        D1:  daily at 00:01 UTC
    """
    scheduler.add_job(
        refresh_candles,
        trigger=CronTrigger(minute="1,16,31,46", timezone="UTC"),
        args=["M15"],
        id="refresh_candles_M15",
        name="Refresh M15 candles",
        replace_existing=True,
    )
    logger.info("Registered job: refresh_candles_M15 (every 15min at :01,:16,:31,:46)")

    scheduler.add_job(
        refresh_candles,
        trigger=CronTrigger(minute=1, timezone="UTC"),
        args=["H1"],
        id="refresh_candles_H1",
        name="Refresh H1 candles",
        replace_existing=True,
    )
    logger.info("Registered job: refresh_candles_H1 (every hour at :01)")

    scheduler.add_job(
        refresh_candles,
        trigger=CronTrigger(hour="0,4,8,12,16,20", minute=1, timezone="UTC"),
        args=["H4"],
        id="refresh_candles_H4",
        name="Refresh H4 candles",
        replace_existing=True,
    )
    logger.info("Registered job: refresh_candles_H4 (every 4h at :01)")

    scheduler.add_job(
        refresh_candles,
        trigger=CronTrigger(hour=0, minute=1, timezone="UTC"),
        args=["D1"],
        id="refresh_candles_D1",
        name="Refresh D1 candles",
        replace_existing=True,
    )
    logger.info("Registered job: refresh_candles_D1 (daily at 00:01 UTC)")

    scheduler.add_job(
        run_daily_backtests,
        trigger=CronTrigger(hour="1,5,9,13,17,21", minute=0, timezone="UTC"),
        id="run_daily_backtests",
        name="Run backtests (4h)",
        replace_existing=True,
    )
    logger.info("Registered job: run_daily_backtests (every 4h at 01,05,09,13,17,21 UTC)")

    scheduler.add_job(
        run_signal_scanner,
        trigger=CronTrigger(minute="2,32", timezone="UTC"),
        id="run_signal_scanner",
        name="Run signal scanner (30min)",
        replace_existing=True,
    )
    logger.info("Registered job: run_signal_scanner (every 30min at :02, :32 UTC)")

    scheduler.add_job(
        run_param_optimization,
        trigger=CronTrigger(hour="3,7,11,15,19,23", minute=30, timezone="UTC"),
        id="run_param_optimization",
        name="Run param optimization (4h)",
        replace_existing=True,
    )
    logger.info("Registered job: run_param_optimization (every 4h at 03,07,11,15,19,23 UTC)")

    scheduler.add_job(
        check_outcomes,
        trigger=IntervalTrigger(seconds=90),
        id="check_outcomes",
        name="Check signal outcomes",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    logger.info("Registered job: check_outcomes (every 90 seconds)")

    scheduler.add_job(
        run_data_retention,
        trigger=CronTrigger(hour=3, minute=0, timezone="UTC"),
        id="run_data_retention",
        name="Run data retention",
        replace_existing=True,
    )
    logger.info("Registered job: run_data_retention (daily at 03:00 UTC)")

    scheduler.add_job(
        send_health_digest,
        trigger=CronTrigger(hour=6, minute=0, timezone="UTC"),
        id="send_health_digest",
        name="Send health digest",
        replace_existing=True,
    )
    logger.info("Registered job: send_health_digest (daily at 06:00 UTC)")

    logger.info("All {count} jobs registered", count=10)
