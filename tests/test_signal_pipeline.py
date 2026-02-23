"""Integration tests for SignalPipeline orchestrator.

Uses mocking to verify pipeline orchestration logic without needing a real
database. All async methods use AsyncMock, sync methods use MagicMock.
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.signal import Signal
from app.services.gold_intelligence import DXYCorrelation, GoldIntelligence
from app.services.risk_manager import RiskCheckResult, RiskManager
from app.services.signal_generator import SignalGenerator
from app.services.signal_pipeline import SignalPipeline
from app.services.strategy_selector import StrategySelector, StrategyScore, VolatilityRegime
from app.strategies.base import CandidateSignal, Direction


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def make_mock_candidate(**overrides) -> CandidateSignal:
    """Create a test CandidateSignal with sensible defaults."""
    defaults = dict(
        strategy_name="liquidity_sweep_reversal",
        symbol="XAUUSD",
        timeframe="H1",
        direction=Direction.BUY,
        entry_price=Decimal("2650.00"),
        stop_loss=Decimal("2645.00"),
        take_profit_1=Decimal("2660.00"),
        take_profit_2=Decimal("2670.00"),
        risk_reward=Decimal("3.00"),
        confidence=Decimal("75.00"),
        reasoning="Test signal",
        timestamp=datetime(2026, 2, 17, 12, 0, 0, tzinfo=timezone.utc),
    )
    defaults.update(overrides)
    return CandidateSignal(**defaults)


def make_mock_strategy_score(**overrides) -> StrategyScore:
    """Create a test StrategyScore with sensible defaults."""
    defaults = dict(
        strategy_name="liquidity_sweep_reversal",
        strategy_id=1,
        composite_score=0.85,
        win_rate=0.65,
        profit_factor=2.1,
        sharpe_ratio=1.3,
        expectancy=0.5,
        max_drawdown=0.12,
        total_trades=120,
        regime=VolatilityRegime.MEDIUM,
        is_degraded=False,
        degradation_reason=None,
    )
    defaults.update(overrides)
    return StrategyScore(**defaults)


def make_pipeline():
    """Create a SignalPipeline with all-mocked services."""
    selector = MagicMock(spec=StrategySelector)
    generator = MagicMock(spec=SignalGenerator)
    risk_manager = MagicMock(spec=RiskManager)
    gold_intel = MagicMock(spec=GoldIntelligence)

    # Set up default async mocks
    selector.select_best = AsyncMock()
    selector.check_h4_confluence = AsyncMock(return_value=False)
    generator.expire_stale_signals = AsyncMock(return_value=0)
    generator.generate = AsyncMock(return_value=[])
    generator.validate = AsyncMock(return_value=[])
    risk_manager.check = AsyncMock(return_value=[])
    gold_intel.get_dxy_correlation = AsyncMock(
        return_value=DXYCorrelation(
            correlation=None, is_divergent=False, available=False, message="N/A"
        )
    )
    gold_intel.enrich = MagicMock(return_value=[])

    pipeline = SignalPipeline(selector, generator, risk_manager, gold_intel)
    session = AsyncMock()

    return pipeline, session


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pipeline_skips_when_no_strategy_qualifies():
    """Pipeline returns empty list when no strategy qualifies (select_best -> None)."""
    pipeline, session = make_pipeline()
    pipeline.selector.select_best.return_value = None

    result = await pipeline.run(session)

    assert result == []
    pipeline.generator.generate.assert_not_called()


@pytest.mark.asyncio
async def test_pipeline_skips_when_no_candidates():
    """Pipeline returns empty list when strategy generates no candidates."""
    pipeline, session = make_pipeline()
    pipeline.selector.select_best.return_value = make_mock_strategy_score()
    pipeline.generator.generate.return_value = []

    result = await pipeline.run(session)

    assert result == []
    pipeline.generator.validate.assert_not_called()


@pytest.mark.asyncio
async def test_pipeline_filters_all_candidates():
    """Pipeline returns empty list when validation filters out all candidates."""
    pipeline, session = make_pipeline()
    pipeline.selector.select_best.return_value = make_mock_strategy_score()
    pipeline.generator.generate.return_value = [make_mock_candidate()]
    pipeline.generator.validate.return_value = []

    result = await pipeline.run(session)

    assert result == []
    pipeline.risk_manager.check.assert_not_called()


@pytest.mark.asyncio
async def test_pipeline_risk_rejects_all():
    """Pipeline returns empty list when risk manager rejects all candidates."""
    pipeline, session = make_pipeline()
    candidate = make_mock_candidate()

    pipeline.selector.select_best.return_value = make_mock_strategy_score()
    pipeline.generator.generate.return_value = [candidate]
    pipeline.generator.validate.return_value = [candidate]
    pipeline.risk_manager.check.return_value = [
        (candidate, RiskCheckResult(approved=False, rejection_reason="Daily loss limit"))
    ]

    result = await pipeline.run(session)

    assert result == []


@pytest.mark.asyncio
async def test_pipeline_full_flow_produces_signal():
    """Full happy-path: pipeline generates, validates, risk-checks, enriches, and persists."""
    pipeline, session = make_pipeline()
    candidate = make_mock_candidate()
    enriched_candidate = make_mock_candidate(
        reasoning="Test signal | London/NY overlap: +5 confidence",
        session="overlap",
    )

    pipeline.selector.select_best.return_value = make_mock_strategy_score()
    pipeline.generator.generate.return_value = [candidate]
    pipeline.generator.validate.return_value = [candidate]
    pipeline.risk_manager.check.return_value = [
        (candidate, RiskCheckResult(approved=True, position_size=Decimal("1.50")))
    ]
    pipeline.selector.check_h4_confluence.return_value = True
    pipeline.gold_intel.get_dxy_correlation.return_value = DXYCorrelation(
        correlation=None, is_divergent=False, available=False, message="N/A"
    )
    pipeline.gold_intel.enrich.return_value = [enriched_candidate]
    pipeline.generator.compute_expiry.return_value = datetime(
        2026, 2, 17, 20, 0, 0, tzinfo=timezone.utc
    )

    # Mock strategy_id lookup
    mock_strategy_row = MagicMock()
    mock_strategy_row.id = 1
    mock_execute_result = MagicMock()
    mock_execute_result.scalar_one_or_none.return_value = mock_strategy_row
    session.execute.return_value = mock_execute_result

    result = await pipeline.run(session)

    assert len(result) == 1
    assert isinstance(result[0], Signal)
    assert result[0].strategy_id == 1
    assert result[0].symbol == "XAUUSD"
    assert result[0].direction == "BUY"
    assert result[0].status == "active"
    session.add.assert_called_once()
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_expire_stale_signals_called_first():
    """Expire stale signals is called before strategy selection."""
    pipeline, session = make_pipeline()

    call_order: list[str] = []

    async def mock_expire(s):
        call_order.append("expire")
        return 0

    async def mock_select(s):
        call_order.append("select")
        return None  # Short-circuit after select

    pipeline.generator.expire_stale_signals = mock_expire
    pipeline.selector.select_best = mock_select

    await pipeline.run(session)

    assert call_order == ["expire", "select"]
    assert call_order.index("expire") < call_order.index("select")
