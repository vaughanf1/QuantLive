"""Trade signal model."""

from datetime import datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import BigInteger, DateTime, ForeignKey, Numeric, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class Signal(Base):
    __tablename__ = "signals"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    strategy_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("strategies.id"))
    symbol: Mapped[str] = mapped_column(String(10))
    timeframe: Mapped[str] = mapped_column(String(5))
    direction: Mapped[str] = mapped_column(String(5))  # "BUY" or "SELL"
    entry_price: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    stop_loss: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    take_profit_1: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    take_profit_2: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    risk_reward: Mapped[Decimal] = mapped_column(Numeric(5, 2))
    confidence: Mapped[Decimal] = mapped_column(Numeric(5, 2))
    reasoning: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="active")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    expires_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
