"""Status endpoint response schemas."""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class SchedulerJobInfo(BaseModel):
    """Information about a single APScheduler job."""

    id: str
    name: str
    next_run_time: Optional[datetime] = None
    trigger: str


class StatusResponse(BaseModel):
    """Response model for the /status diagnostic endpoint."""

    status: str  # "ok" or "degraded"
    uptime_seconds: float
    database: str  # "connected" or "disconnected"
    scheduler: str  # "running" or "stopped"
    jobs: list[SchedulerJobInfo]
    active_signals: int
    last_candle_fetch: Optional[datetime] = None
    last_signal_generated: Optional[datetime] = None
    timestamp: datetime
