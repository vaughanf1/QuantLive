"""Health check response schemas."""

from datetime import datetime

from pydantic import BaseModel


class HealthResponse(BaseModel):
    """Response model for the /health endpoint."""

    status: str
    database: str
    timestamp: datetime
    version: str = "0.1.0"
