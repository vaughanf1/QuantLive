"""OHLCV candle data model."""

from datetime import datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import BigInteger, DateTime, Index, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class Candle(Base):
    __tablename__ = "candles"

    __table_args__ = (
        UniqueConstraint("symbol", "timeframe", "timestamp", name="uq_candle_identity"),
        Index("idx_candles_lookup", "symbol", "timeframe", "timestamp"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(10), default="XAUUSD")
    timeframe: Mapped[str] = mapped_column(String(5))
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    open: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    high: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    low: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    close: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    volume: Mapped[Optional[Decimal]] = mapped_column(Numeric(15, 2), nullable=True)
