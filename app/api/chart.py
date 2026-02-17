"""Chart visualization API endpoints.

Provides REST endpoints for rendering a browser-based XAUUSD candlestick
chart with signal overlays, and JSON data endpoints for candles and signals.
"""

import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session
from app.models.candle import Candle
from app.models.outcome import Outcome
from app.models.signal import Signal

router = APIRouter(prefix="/chart", tags=["chart"])

templates = Jinja2Templates(
    directory=str(Path(__file__).resolve().parent.parent / "templates")
)


def _to_unix_seconds(dt: datetime.datetime) -> int:
    """Convert a datetime to Unix seconds (int), assuming UTC if naive."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    return int(dt.timestamp())


def _outcome_color(result: str | None, status: str) -> str:
    """Determine signal marker color based on outcome and status."""
    if result in ("tp1_hit", "tp2_hit"):
        return "#26a69a"  # green
    if result == "sl_hit":
        return "#ef5350"  # red
    if status == "active" and result is None:
        return "#3179F5"  # blue
    return "#888888"  # gray (expired or other)


@router.get("/", response_class=HTMLResponse)
async def chart_page(request: Request):
    """Serve the chart HTML page."""
    return templates.TemplateResponse(request=request, name="chart.html")


@router.get("/candles")
async def get_chart_candles(
    limit: int = 500,
    session: AsyncSession = Depends(get_session),
):
    """Return H1 XAUUSD candle data as JSON with Unix timestamp seconds.

    Candles are returned in chronological order (oldest first) as required
    by TradingView Lightweight Charts.
    """
    query = (
        select(Candle)
        .where(Candle.symbol == "XAUUSD")
        .where(Candle.timeframe == "H1")
        .order_by(Candle.timestamp.desc())
        .limit(limit)
    )
    result = await session.execute(query)
    candles = result.scalars().all()

    # Reverse to chronological order (oldest first)
    candles.reverse()

    return [
        {
            "time": _to_unix_seconds(c.timestamp),
            "open": float(c.open),
            "high": float(c.high),
            "low": float(c.low),
            "close": float(c.close),
        }
        for c in candles
    ]


@router.get("/signals")
async def get_chart_signals(
    limit: int = 100,
    session: AsyncSession = Depends(get_session),
):
    """Return signal data with outcome colors for chart markers.

    Each signal includes entry/SL/TP prices, direction, outcome color,
    and timing information for marker placement.
    """
    query = (
        select(Signal, Outcome)
        .outerjoin(Outcome, Signal.id == Outcome.signal_id)
        .order_by(Signal.created_at.desc())
        .limit(limit)
    )
    result = await session.execute(query)
    rows = result.all()

    signals = []
    for signal, outcome in rows:
        result_str = outcome.result if outcome else None
        signals.append(
            {
                "time": _to_unix_seconds(signal.created_at),
                "direction": signal.direction,
                "entry_price": float(signal.entry_price),
                "stop_loss": float(signal.stop_loss),
                "take_profit_1": float(signal.take_profit_1),
                "take_profit_2": float(signal.take_profit_2),
                "status": signal.status,
                "outcome_color": _outcome_color(result_str, signal.status),
                "confidence": float(signal.confidence),
            }
        )

    return signals
