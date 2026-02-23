"""Optimized strategy parameters model.

Stores the best parameter sets discovered by the parameter optimization engine,
along with their performance metrics and walk-forward validation results.
"""

from datetime import datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    func,
)
from sqlalchemy.dialects.postgresql import JSON
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class OptimizedParams(Base):
    __tablename__ = "optimized_params"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    strategy_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("strategies.id"))
    strategy_name: Mapped[str] = mapped_column(String(50))
    params: Mapped[dict] = mapped_column(JSON, nullable=False)
    win_rate: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 4), nullable=True)
    profit_factor: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 4), nullable=True)
    sharpe_ratio: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 4), nullable=True)
    expectancy: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 4), nullable=True)
    total_trades: Mapped[int] = mapped_column(Integer)
    wfe_ratio: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 4), nullable=True)
    is_overfitted: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False)
    combinations_tested: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
