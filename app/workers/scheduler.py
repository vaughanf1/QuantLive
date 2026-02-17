"""APScheduler setup for background task scheduling.

Uses AsyncIOScheduler with in-memory job store. Jobs are registered
by other modules (e.g., candle ingestion in Plan 01-02).
"""

from apscheduler.jobstores.memory import MemoryJobStore
from apscheduler.schedulers.asyncio import AsyncIOScheduler

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
