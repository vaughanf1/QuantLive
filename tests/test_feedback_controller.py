"""Tests for FeedbackController service.

Covers strategy degradation detection, auto-recovery after 7+ days,
circuit breaker with consecutive loss and drawdown triggers, 24-hour
cooldown reset, and win-based reset. Uses real database for degradation/
recovery tests and mocked sessions for circuit breaker logic.
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models.base import Base
from app.models.backtest_result import BacktestResult
from app.models.outcome import Outcome
from app.models.signal import Signal
from app.models.strategy import Strategy
from app.models.strategy_performance import StrategyPerformance
from app.services.feedback_controller import FeedbackController

# ---------------------------------------------------------------------------
# Test database setup (same pattern as test_performance_tracker.py)
# ---------------------------------------------------------------------------
TEST_DATABASE_URL = "postgresql+asyncpg://vaughanfawcett@localhost:5432/goldsignal_test"

_schema_engine = create_async_engine(TEST_DATABASE_URL, echo=False)
_tables_created = False


async def _ensure_tables():
    global _tables_created
    if not _tables_created:
        async with _schema_engine.begin() as conn:
            # Drop and recreate to ensure schema matches models
            await conn.run_sync(Base.metadata.drop_all)
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
        await conn.execute(text("DELETE FROM backtest_results"))
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
    strat = Strategy(name=name, is_active=True)
    session.add(strat)
    await session.commit()
    await session.refresh(strat)
    return strat


async def _create_backtest(
    session: AsyncSession,
    strategy_id: int,
    win_rate: Decimal,
    profit_factor: Decimal,
    created_at: datetime | None = None,
) -> BacktestResult:
    """Insert a BacktestResult row (non-walk-forward baseline)."""
    bt = BacktestResult(
        strategy_id=strategy_id,
        timeframe="H1",
        window_days=60,
        start_date=datetime(2026, 1, 1, tzinfo=timezone.utc),
        end_date=datetime(2026, 2, 1, tzinfo=timezone.utc),
        win_rate=win_rate,
        profit_factor=profit_factor,
        sharpe_ratio=Decimal("1.5000"),
        max_drawdown=Decimal("5.0000"),
        expectancy=Decimal("2.0000"),
        total_trades=100,
        is_walk_forward=False,
    )
    session.add(bt)
    await session.commit()
    await session.refresh(bt)
    if created_at:
        await session.execute(
            text("UPDATE backtest_results SET created_at = :ts WHERE id = :bid"),
            {"ts": created_at, "bid": bt.id},
        )
        await session.commit()
    return bt


async def _create_performance(
    session: AsyncSession,
    strategy_id: int,
    period: str,
    win_rate: Decimal,
    profit_factor: Decimal,
    is_degraded: bool = False,
    calculated_at: datetime | None = None,
) -> StrategyPerformance:
    """Insert a StrategyPerformance row."""
    perf = StrategyPerformance(
        strategy_id=strategy_id,
        period=period,
        win_rate=win_rate,
        profit_factor=profit_factor,
        avg_rr=Decimal("2.0000"),
        total_signals=10,
        is_degraded=is_degraded,
    )
    session.add(perf)
    await session.commit()
    await session.refresh(perf)
    if calculated_at:
        await session.execute(
            text("UPDATE strategy_performance SET calculated_at = :ts WHERE id = :pid"),
            {"ts": calculated_at, "pid": perf.id},
        )
        await session.commit()
    return perf


async def _create_signal(
    session: AsyncSession,
    strategy_id: int,
    created_at: datetime | None = None,
) -> Signal:
    sig = Signal(
        strategy_id=strategy_id,
        symbol="XAUUSD",
        timeframe="H1",
        direction="BUY",
        entry_price=Decimal("2650.00"),
        stop_loss=Decimal("2645.00"),
        take_profit_1=Decimal("2655.00"),
        take_profit_2=Decimal("2660.00"),
        risk_reward=Decimal("2.00"),
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
    outcome = Outcome(
        signal_id=signal_id,
        result=result,
        exit_price=Decimal("2655.00"),
        pnl_pips=pnl_pips,
    )
    session.add(outcome)
    await session.commit()
    await session.refresh(outcome)
    if created_at:
        await session.execute(
            text("UPDATE outcomes SET created_at = :ts WHERE id = :oid"),
            {"ts": created_at, "oid": outcome.id},
        )
        await session.commit()
    return outcome


# ---------------------------------------------------------------------------
# Degradation Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestDegradation:
    """Test strategy degradation detection."""

    async def test_detect_degradation_low_win_rate(self, db_session: AsyncSession):
        """Strategy with live win_rate 15%+ below baseline -> is_degraded=True."""
        controller = FeedbackController()
        strat = await _create_strategy(db_session)

        # Baseline backtest: win_rate = 0.65
        await _create_backtest(
            db_session, strat.id,
            win_rate=Decimal("0.6500"),
            profit_factor=Decimal("2.0000"),
            created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )

        # Live 30d performance: win_rate = 0.45 (dropped 0.20 > 0.15 threshold)
        await _create_performance(
            db_session, strat.id, "30d",
            win_rate=Decimal("0.4500"),
            profit_factor=Decimal("1.5000"),
        )

        is_degraded, reason = await controller.check_degradation(db_session, strat.id)
        assert is_degraded is True
        assert "win rate" in reason.lower() or "Win rate" in reason

    async def test_detect_degradation_low_profit_factor(self, db_session: AsyncSession):
        """Strategy with profit_factor < 1.0 -> is_degraded=True."""
        controller = FeedbackController()
        strat = await _create_strategy(db_session)

        await _create_backtest(
            db_session, strat.id,
            win_rate=Decimal("0.6000"),
            profit_factor=Decimal("2.0000"),
            created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )

        # Live performance: profit_factor 0.80 < 1.0
        await _create_performance(
            db_session, strat.id, "30d",
            win_rate=Decimal("0.5500"),
            profit_factor=Decimal("0.8000"),
        )

        is_degraded, reason = await controller.check_degradation(db_session, strat.id)
        assert is_degraded is True
        assert "profit factor" in reason.lower() or "Profit factor" in reason

    async def test_no_degradation_healthy_strategy(self, db_session: AsyncSession):
        """Strategy with good metrics -> is_degraded=False."""
        controller = FeedbackController()
        strat = await _create_strategy(db_session)

        await _create_backtest(
            db_session, strat.id,
            win_rate=Decimal("0.6000"),
            profit_factor=Decimal("2.0000"),
            created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )

        # Live performance within acceptable range
        await _create_performance(
            db_session, strat.id, "30d",
            win_rate=Decimal("0.5500"),
            profit_factor=Decimal("1.8000"),
        )

        is_degraded, reason = await controller.check_degradation(db_session, strat.id)
        assert is_degraded is False
        assert reason is None

    async def test_degradation_flag_persisted(self, db_session: AsyncSession):
        """After check_degradation, StrategyPerformance.is_degraded is updated in DB."""
        controller = FeedbackController()
        strat = await _create_strategy(db_session)

        await _create_backtest(
            db_session, strat.id,
            win_rate=Decimal("0.6500"),
            profit_factor=Decimal("2.0000"),
            created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )

        perf = await _create_performance(
            db_session, strat.id, "30d",
            win_rate=Decimal("0.4500"),
            profit_factor=Decimal("0.8000"),
            is_degraded=False,
        )

        is_degraded, reason = await controller.check_degradation(db_session, strat.id)
        assert is_degraded is True

        # Verify flag persisted in DB
        await db_session.refresh(perf)
        assert perf.is_degraded is True


# ---------------------------------------------------------------------------
# Recovery Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestRecovery:
    """Test strategy auto-recovery."""

    async def test_auto_recovery_after_7_days(self, db_session: AsyncSession):
        """Strategy degraded 7+ days ago with good recent metrics -> recovery."""
        controller = FeedbackController()
        strat = await _create_strategy(db_session)

        await _create_backtest(
            db_session, strat.id,
            win_rate=Decimal("0.6000"),
            profit_factor=Decimal("2.0000"),
            created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )

        # Degraded performance set 10 days ago
        now = datetime.now(timezone.utc)
        await _create_performance(
            db_session, strat.id, "30d",
            win_rate=Decimal("0.5800"),  # within 5% of 0.60 baseline
            profit_factor=Decimal("1.5000"),  # above 1.0
            is_degraded=True,
            calculated_at=now - timedelta(days=10),
        )

        # Also add a 7d performance row showing recovery
        await _create_performance(
            db_session, strat.id, "7d",
            win_rate=Decimal("0.5800"),
            profit_factor=Decimal("1.5000"),
            is_degraded=True,
            calculated_at=now - timedelta(days=10),
        )

        recovered = await controller.check_recovery(db_session, strat.id)
        assert recovered is True

    async def test_no_recovery_before_7_days(self, db_session: AsyncSession):
        """Strategy degraded for < 7 days -> stays degraded even if metrics look good."""
        controller = FeedbackController()
        strat = await _create_strategy(db_session)

        await _create_backtest(
            db_session, strat.id,
            win_rate=Decimal("0.6000"),
            profit_factor=Decimal("2.0000"),
            created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )

        # Degraded only 3 days ago
        now = datetime.now(timezone.utc)
        await _create_performance(
            db_session, strat.id, "30d",
            win_rate=Decimal("0.5800"),
            profit_factor=Decimal("1.5000"),
            is_degraded=True,
            calculated_at=now - timedelta(days=3),
        )

        await _create_performance(
            db_session, strat.id, "7d",
            win_rate=Decimal("0.5800"),
            profit_factor=Decimal("1.5000"),
            is_degraded=True,
            calculated_at=now - timedelta(days=3),
        )

        recovered = await controller.check_recovery(db_session, strat.id)
        assert recovered is False


# ---------------------------------------------------------------------------
# Circuit Breaker Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestCircuitBreaker:
    """Test circuit breaker logic."""

    async def test_circuit_breaker_consecutive_losses(self, db_session: AsyncSession):
        """5+ consecutive sl_hit/expired outcomes -> circuit_breaker_active=True."""
        controller = FeedbackController()
        # Reset class-level state
        controller._circuit_breaker_active = False
        controller._circuit_breaker_triggered_at = None

        strat = await _create_strategy(db_session)
        now = datetime.now(timezone.utc)

        # Create 6 consecutive losses
        for i in range(6):
            sig = await _create_signal(db_session, strat.id)
            await _create_outcome(
                db_session, sig.id, "sl_hit", Decimal("-30.00"),
                created_at=now - timedelta(hours=6 - i),
            )

        active = await controller.check_circuit_breaker(db_session)
        assert active is True

    async def test_circuit_breaker_drawdown_exceeded(self, db_session: AsyncSession):
        """Current drawdown > 2x historical max drawdown -> circuit_breaker active."""
        controller = FeedbackController()
        controller._circuit_breaker_active = False
        controller._circuit_breaker_triggered_at = None

        strat = await _create_strategy(db_session)
        now = datetime.now(timezone.utc)

        # History: win, small loss, win (establish max_drawdown = 20 pips)
        sig1 = await _create_signal(db_session, strat.id)
        await _create_outcome(
            db_session, sig1.id, "tp1_hit", Decimal("50.00"),
            created_at=now - timedelta(hours=10),
        )
        sig2 = await _create_signal(db_session, strat.id)
        await _create_outcome(
            db_session, sig2.id, "sl_hit", Decimal("-20.00"),
            created_at=now - timedelta(hours=9),
        )
        sig3 = await _create_signal(db_session, strat.id)
        await _create_outcome(
            db_session, sig3.id, "tp1_hit", Decimal("50.00"),
            created_at=now - timedelta(hours=8),
        )
        # At this point: pnl sequence [50, -20, 50]
        # running: 50, 30, 80. peak: 50, 50, 80. dd: 0, 20, 0
        # max_drawdown = 20, running_drawdown = 0

        # Now add large losses: total running drawdown exceeds 2 * 20 = 40
        sig4 = await _create_signal(db_session, strat.id)
        await _create_outcome(
            db_session, sig4.id, "sl_hit", Decimal("-30.00"),
            created_at=now - timedelta(hours=7),
        )
        sig5 = await _create_signal(db_session, strat.id)
        await _create_outcome(
            db_session, sig5.id, "sl_hit", Decimal("-30.00"),
            created_at=now - timedelta(hours=6),
        )
        # running: 50, 30, 80, 50, 20. peak: 50, 50, 80, 80, 80. dd: 0, 20, 0, 30, 60
        # max_drawdown = 60, running_drawdown = 60
        # For this test, the controller should compute historical max
        # (BEFORE current drawdown phase) vs current drawdown.
        # We'll mock get_drawdown_metrics to return specific values.

        with patch("app.services.feedback_controller.RiskManager") as mock_rm_cls:
            mock_rm = AsyncMock()
            mock_rm_cls.return_value = mock_rm
            mock_rm.get_drawdown_metrics.return_value = {
                "running_drawdown": 60.0,
                "max_drawdown": 60.0,
                "running_pnl": 20.0,
                "peak_pnl": 80.0,
            }
            # For the drawdown check we need running_drawdown > 2 * max_drawdown
            # But if max includes current, they're equal. The controller should
            # handle this by tracking the "pre-current-phase" max drawdown.
            # Actually, let's set max_drawdown to 20 (the historical) and
            # running_drawdown to 60 (the current), which means 60 > 2*20 = 40
            mock_rm.get_drawdown_metrics.return_value = {
                "running_drawdown": 60.0,
                "max_drawdown": 20.0,
                "running_pnl": 20.0,
                "peak_pnl": 80.0,
            }
            active = await controller.check_circuit_breaker(db_session)
            assert active is True

    async def test_circuit_breaker_not_triggered(self, db_session: AsyncSession):
        """3 consecutive losses, drawdown within limits -> circuit_breaker_active=False."""
        controller = FeedbackController()
        controller._circuit_breaker_active = False
        controller._circuit_breaker_triggered_at = None

        strat = await _create_strategy(db_session)
        now = datetime.now(timezone.utc)

        # Only 3 consecutive losses (below threshold of 5)
        for i in range(3):
            sig = await _create_signal(db_session, strat.id)
            await _create_outcome(
                db_session, sig.id, "sl_hit", Decimal("-20.00"),
                created_at=now - timedelta(hours=3 - i),
            )

        with patch("app.services.feedback_controller.RiskManager") as mock_rm_cls:
            mock_rm = AsyncMock()
            mock_rm_cls.return_value = mock_rm
            mock_rm.get_drawdown_metrics.return_value = {
                "running_drawdown": 10.0,
                "max_drawdown": 20.0,
                "running_pnl": 50.0,
                "peak_pnl": 60.0,
            }
            active = await controller.check_circuit_breaker(db_session)
            assert active is False

    async def test_circuit_breaker_24h_cooldown_reset(self, db_session: AsyncSession):
        """Circuit breaker was active, 24h passed -> automatically resets."""
        controller = FeedbackController()
        # Set circuit breaker as active 25 hours ago
        controller._circuit_breaker_active = True
        controller._circuit_breaker_triggered_at = datetime.now(timezone.utc) - timedelta(hours=25)

        strat = await _create_strategy(db_session)
        now = datetime.now(timezone.utc)

        # Only 1 recent loss (not enough to re-trigger)
        sig = await _create_signal(db_session, strat.id)
        await _create_outcome(
            db_session, sig.id, "sl_hit", Decimal("-20.00"),
            created_at=now - timedelta(hours=1),
        )

        with patch("app.services.feedback_controller.RiskManager") as mock_rm_cls:
            mock_rm = AsyncMock()
            mock_rm_cls.return_value = mock_rm
            mock_rm.get_drawdown_metrics.return_value = {
                "running_drawdown": 5.0,
                "max_drawdown": 20.0,
                "running_pnl": 50.0,
                "peak_pnl": 55.0,
            }
            active = await controller.check_circuit_breaker(db_session)
            assert active is False
            assert controller._circuit_breaker_active is False

    async def test_circuit_breaker_resets_on_win(self, db_session: AsyncSession):
        """After consecutive losses trigger CB, a win resets consecutive count."""
        controller = FeedbackController()
        controller._circuit_breaker_active = False
        controller._circuit_breaker_triggered_at = None

        strat = await _create_strategy(db_session)
        now = datetime.now(timezone.utc)

        # 5 losses then 1 win (most recent)
        for i in range(5):
            sig = await _create_signal(db_session, strat.id)
            await _create_outcome(
                db_session, sig.id, "sl_hit", Decimal("-20.00"),
                created_at=now - timedelta(hours=10 - i),
            )
        sig_win = await _create_signal(db_session, strat.id)
        await _create_outcome(
            db_session, sig_win.id, "tp1_hit", Decimal("50.00"),
            created_at=now - timedelta(hours=1),
        )

        with patch("app.services.feedback_controller.RiskManager") as mock_rm_cls:
            mock_rm = AsyncMock()
            mock_rm_cls.return_value = mock_rm
            mock_rm.get_drawdown_metrics.return_value = {
                "running_drawdown": 5.0,
                "max_drawdown": 20.0,
                "running_pnl": 50.0,
                "peak_pnl": 55.0,
            }
            active = await controller.check_circuit_breaker(db_session)
            # Most recent is a win, so consecutive losses = 0
            assert active is False

    async def test_consecutive_losses_count(self, db_session: AsyncSession):
        """Mixed sequence [win, loss, loss, loss, loss, loss] -> 5 consecutive from tail."""
        controller = FeedbackController()

        strat = await _create_strategy(db_session)
        now = datetime.now(timezone.utc)

        # Win first
        sig_w = await _create_signal(db_session, strat.id)
        await _create_outcome(
            db_session, sig_w.id, "tp1_hit", Decimal("50.00"),
            created_at=now - timedelta(hours=10),
        )

        # Then 5 losses
        for i in range(5):
            sig = await _create_signal(db_session, strat.id)
            await _create_outcome(
                db_session, sig.id, "sl_hit", Decimal("-20.00"),
                created_at=now - timedelta(hours=5 - i),
            )

        count = await controller._count_consecutive_losses(db_session)
        assert count == 5
