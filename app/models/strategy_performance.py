"""Rolling strategy performance metrics model."""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, Numeric, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class StrategyPerformance(Base):
    __tablename__ = "strategy_performance"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    strategy_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("strategies.id"))
    period: Mapped[str] = mapped_column(String(10))  # e.g. "7d", "30d"
    win_rate: Mapped[Decimal] = mapped_column(Numeric(10, 4))
    profit_factor: Mapped[Decimal] = mapped_column(Numeric(10, 4))
    avg_rr: Mapped[Decimal] = mapped_column(Numeric(10, 4))
    total_signals: Mapped[int] = mapped_column(Integer)
    is_degraded: Mapped[bool] = mapped_column(Boolean, default=False)
    calculated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
