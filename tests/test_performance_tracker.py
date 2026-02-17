"""Tests for PerformanceTracker service.

Covers rolling 7d and 30d metric recalculation (win_rate, profit_factor, avg_rr),
upsert logic for StrategyPerformance rows, edge cases (no outcomes, all wins,
multiple strategies), and result classification (tp1/tp2 as wins, sl/expired as losses).
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models.base import Base
from app.models.outcome import Outcome
from app.models.signal import Signal
from app.models.strategy import Strategy
from app.models.strategy_performance import StrategyPerformance
from app.services.performance_tracker import PerformanceTracker

# ---------------------------------------------------------------------------
# Test database setup (same pattern as conftest.py)
# ---------------------------------------------------------------------------
TEST_DATABASE_URL = "postgresql+asyncpg://vaughanfawcett@localhost:5432/goldsignal_test"

_schema_engine = create_async_engine(TEST_DATABASE_URL, echo=False)
_tables_created = False


async def _ensure_tables():
    global _tables_created
    if not _tables_created:
        async with _schema_engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        _tables_created = True


@pytest_asyncio.fixture
async def db_session():
    """Provide an isolated database session for each test."""
    await _ensure_tables()
    engine = create_async_engine(TEST_DATABASE_URL, echo=False, pool_size=2)

    async with engine.begin() as conn:
        await conn.execute(text("DELETE FROM outcomes"))
        await conn.execute(text("DELETE FROM signals"))
        await conn.execute(text("DELETE FROM strategy_performance"))
        await conn.execute(text("DELETE FROM strategies"))

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    session = session_factory()

    yield session

    await session.close()
    await engine.dispose()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
async def _create_strategy(session: AsyncSession, name: str = "test_strat") -> Strategy:
    """Insert a strategy row and return it."""
    strat = Strategy(name=name, is_active=True)
    session.add(strat)
    await session.commit()
    await session.refresh(strat)
    return strat


async def _create_signal(
    session: AsyncSession,
    strategy_id: int,
    direction: str = "BUY",
    risk_reward: Decimal = Decimal("2.00"),
    created_at: datetime | None = None,
) -> Signal:
    """Insert a signal row and return it."""
    sig = Signal(
        strategy_id=strategy_id,
        symbol="XAUUSD",
        timeframe="H1",
        direction=direction,
        entry_price=Decimal("2650.00"),
        stop_loss=Decimal("2645.00"),
        take_profit_1=Decimal("2655.00"),
        take_profit_2=Decimal("2660.00"),
        risk_reward=risk_reward,
        confidence=Decimal("75.00"),
        reasoning="test",
        status="active",
    )
    if created_at:
        sig.created_at = created_at
    session.add(sig)
    await session.commit()
    await session.refresh(sig)
    return sig


async def _create_outcome(
    session: AsyncSession,
    signal_id: int,
    result: str,
    pnl_pips: Decimal,
    created_at: datetime | None = None,
) -> Outcome:
    """Insert an outcome row and return it."""
    outcome = Outcome(
        signal_id=signal_id,
        result=result,
        exit_price=Decimal("2655.00"),
        pnl_pips=pnl_pips,
    )
    session.add(outcome)
    await session.commit()
    await session.refresh(outcome)
    # Override created_at if needed (server_default is func.now())
    if created_at:
        await session.execute(
            text("UPDATE outcomes SET created_at = :ts WHERE id = :oid"),
            {"ts": created_at, "oid": outcome.id},
        )
        await session.commit()
    return outcome


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestPerformanceTracker:
    """Test PerformanceTracker service."""

    async def test_recalculate_7d_win_rate(self, db_session: AsyncSession):
        """3 wins + 2 losses in last 7 days -> win_rate = 0.6000."""
        tracker = PerformanceTracker()
        strat = await _create_strategy(db_session)
        now = datetime.now(timezone.utc)

        # 3 wins (tp1_hit)
        for _ in range(3):
            sig = await _create_signal(db_session, strat.id)
            await _create_outcome(
                db_session, sig.id, "tp1_hit", Decimal("50.00"),
                created_at=now - timedelta(days=2),
            )

        # 2 losses (sl_hit)
        for _ in range(2):
            sig = await _create_signal(db_session, strat.id)
            await _create_outcome(
                db_session, sig.id, "sl_hit", Decimal("-30.00"),
                created_at=now - timedelta(days=3),
            )

        rows = await tracker.recalculate_for_strategy(db_session, strat.id)
        perf_7d = next(r for r in rows if r.period == "7d")
        assert perf_7d.win_rate == Decimal("0.6000")
        assert perf_7d.total_signals == 5

    async def test_recalculate_30d_profit_factor(self, db_session: AsyncSession):
        """Sum of winning pnl / abs(sum of losing pnl) -> correct profit_factor."""
        tracker = PerformanceTracker()
        strat = await _create_strategy(db_session)
        now = datetime.now(timezone.utc)

        # 2 wins totaling +100 pips
        for _ in range(2):
            sig = await _create_signal(db_session, strat.id)
            await _create_outcome(
                db_session, sig.id, "tp1_hit", Decimal("50.00"),
                created_at=now - timedelta(days=10),
            )

        # 1 loss totaling -25 pips
        sig = await _create_signal(db_session, strat.id)
        await _create_outcome(
            db_session, sig.id, "sl_hit", Decimal("-25.00"),
            created_at=now - timedelta(days=15),
        )

        rows = await tracker.recalculate_for_strategy(db_session, strat.id)
        perf_30d = next(r for r in rows if r.period == "30d")
        # profit_factor = 100 / 25 = 4.0
        assert perf_30d.profit_factor == Decimal("4.0000")

    async def test_recalculate_avg_rr(self, db_session: AsyncSession):
        """Average of risk_reward values from associated signals -> correct avg_rr."""
        tracker = PerformanceTracker()
        strat = await _create_strategy(db_session)
        now = datetime.now(timezone.utc)

        # Signal with RR 2.00
        sig1 = await _create_signal(db_session, strat.id, risk_reward=Decimal("2.00"))
        await _create_outcome(
            db_session, sig1.id, "tp1_hit", Decimal("50.00"),
            created_at=now - timedelta(days=1),
        )

        # Signal with RR 3.00
        sig2 = await _create_signal(db_session, strat.id, risk_reward=Decimal("3.00"))
        await _create_outcome(
            db_session, sig2.id, "tp2_hit", Decimal("80.00"),
            created_at=now - timedelta(days=2),
        )

        rows = await tracker.recalculate_for_strategy(db_session, strat.id)
        perf_7d = next(r for r in rows if r.period == "7d")
        # avg_rr = (2.00 + 3.00) / 2 = 2.5
        assert perf_7d.avg_rr == Decimal("2.5000")

    async def test_upsert_existing_row(self, db_session: AsyncSession):
        """StrategyPerformance row exists for same strategy+period -> updated (not duplicated)."""
        tracker = PerformanceTracker()
        strat = await _create_strategy(db_session)
        now = datetime.now(timezone.utc)

        # Create 1 win
        sig = await _create_signal(db_session, strat.id)
        await _create_outcome(
            db_session, sig.id, "tp1_hit", Decimal("50.00"),
            created_at=now - timedelta(days=1),
        )

        # First recalculation
        rows1 = await tracker.recalculate_for_strategy(db_session, strat.id)

        # Add another win
        sig2 = await _create_signal(db_session, strat.id)
        await _create_outcome(
            db_session, sig2.id, "tp1_hit", Decimal("40.00"),
            created_at=now - timedelta(days=2),
        )

        # Second recalculation (should UPDATE, not INSERT duplicate)
        rows2 = await tracker.recalculate_for_strategy(db_session, strat.id)

        # Check no duplicates
        stmt = select(StrategyPerformance).where(
            StrategyPerformance.strategy_id == strat.id,
            StrategyPerformance.period == "7d",
        )
        result = await db_session.execute(stmt)
        all_rows = list(result.scalars().all())
        assert len(all_rows) == 1

    async def test_insert_new_row(self, db_session: AsyncSession):
        """No existing StrategyPerformance row -> new row created."""
        tracker = PerformanceTracker()
        strat = await _create_strategy(db_session)
        now = datetime.now(timezone.utc)

        sig = await _create_signal(db_session, strat.id)
        await _create_outcome(
            db_session, sig.id, "tp1_hit", Decimal("50.00"),
            created_at=now - timedelta(days=1),
        )

        rows = await tracker.recalculate_for_strategy(db_session, strat.id)
        assert len(rows) == 2  # 7d and 30d

        # Verify rows exist in DB
        stmt = select(StrategyPerformance).where(
            StrategyPerformance.strategy_id == strat.id,
        )
        result = await db_session.execute(stmt)
        db_rows = list(result.scalars().all())
        assert len(db_rows) == 2
        periods = {r.period for r in db_rows}
        assert periods == {"7d", "30d"}

    async def test_no_outcomes_in_window(self, db_session: AsyncSession):
        """Zero outcomes in the rolling window -> total_signals=0, win_rate=0, profit_factor=0."""
        tracker = PerformanceTracker()
        strat = await _create_strategy(db_session)

        # No outcomes at all
        rows = await tracker.recalculate_for_strategy(db_session, strat.id)
        perf_7d = next(r for r in rows if r.period == "7d")
        assert perf_7d.total_signals == 0
        assert perf_7d.win_rate == Decimal("0.0000")
        assert perf_7d.profit_factor == Decimal("0.0000")
        assert perf_7d.avg_rr == Decimal("0.0000")

    async def test_both_periods_calculated(self, db_session: AsyncSession):
        """recalculate_for_strategy produces rows for both '7d' and '30d'."""
        tracker = PerformanceTracker()
        strat = await _create_strategy(db_session)
        now = datetime.now(timezone.utc)

        sig = await _create_signal(db_session, strat.id)
        await _create_outcome(
            db_session, sig.id, "tp1_hit", Decimal("50.00"),
            created_at=now - timedelta(days=1),
        )

        rows = await tracker.recalculate_for_strategy(db_session, strat.id)
        periods = {r.period for r in rows}
        assert periods == {"7d", "30d"}

    async def test_only_relevant_strategy(self, db_session: AsyncSession):
        """Outcomes from other strategies are NOT included in the calculation."""
        tracker = PerformanceTracker()
        strat_a = await _create_strategy(db_session, name="strat_a")
        strat_b = await _create_strategy(db_session, name="strat_b")
        now = datetime.now(timezone.utc)

        # Strat A: 1 win
        sig_a = await _create_signal(db_session, strat_a.id)
        await _create_outcome(
            db_session, sig_a.id, "tp1_hit", Decimal("50.00"),
            created_at=now - timedelta(days=1),
        )

        # Strat B: 3 losses
        for _ in range(3):
            sig_b = await _create_signal(db_session, strat_b.id)
            await _create_outcome(
                db_session, sig_b.id, "sl_hit", Decimal("-30.00"),
                created_at=now - timedelta(days=1),
            )

        # Recalculate for strat_a only
        rows = await tracker.recalculate_for_strategy(db_session, strat_a.id)
        perf_7d = next(r for r in rows if r.period == "7d")
        assert perf_7d.win_rate == Decimal("1.0000")
        assert perf_7d.total_signals == 1

    async def test_profit_factor_no_losses(self, db_session: AsyncSession):
        """All wins (no losses) -> profit_factor capped at 9999.9999."""
        tracker = PerformanceTracker()
        strat = await _create_strategy(db_session)
        now = datetime.now(timezone.utc)

        for _ in range(3):
            sig = await _create_signal(db_session, strat.id)
            await _create_outcome(
                db_session, sig.id, "tp1_hit", Decimal("50.00"),
                created_at=now - timedelta(days=1),
            )

        rows = await tracker.recalculate_for_strategy(db_session, strat.id)
        perf_7d = next(r for r in rows if r.period == "7d")
        assert perf_7d.profit_factor == Decimal("9999.9999")

    async def test_win_rate_counts_tp_hits_as_wins(self, db_session: AsyncSession):
        """tp1_hit and tp2_hit both count as wins; sl_hit and expired count as losses."""
        tracker = PerformanceTracker()
        strat = await _create_strategy(db_session)
        now = datetime.now(timezone.utc)

        results_data = [
            ("tp1_hit", Decimal("50.00")),
            ("tp2_hit", Decimal("80.00")),
            ("sl_hit", Decimal("-30.00")),
            ("expired", Decimal("-10.00")),
        ]
        for result, pnl in results_data:
            sig = await _create_signal(db_session, strat.id)
            await _create_outcome(
                db_session, sig.id, result, pnl,
                created_at=now - timedelta(days=1),
            )

        rows = await tracker.recalculate_for_strategy(db_session, strat.id)
        perf_7d = next(r for r in rows if r.period == "7d")
        # 2 wins (tp1, tp2) out of 4 total = 0.5000
        assert perf_7d.win_rate == Decimal("0.5000")
        assert perf_7d.total_signals == 4
