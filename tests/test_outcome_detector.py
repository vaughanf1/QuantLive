"""Tests for OutcomeDetector service.

Covers all outcome types (sl_hit, tp1_hit, tp2_hit, expired) for both
BUY and SELL directions with spread accounting, PnL calculation, duration,
signal status updates, and edge cases.
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.outcome import Outcome
from app.models.signal import Signal
from app.services.outcome_detector import OutcomeDetector


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_signal(
    direction: str = "BUY",
    entry_price: Decimal = Decimal("2650.00"),
    stop_loss: Decimal = Decimal("2645.00"),
    take_profit_1: Decimal = Decimal("2655.00"),
    take_profit_2: Decimal = Decimal("2660.00"),
    status: str = "active",
    expires_at: datetime | None = None,
    created_at: datetime | None = None,
    signal_id: int = 1,
) -> Signal:
    """Build a Signal-like object for testing without touching the DB."""
    sig = Signal()
    sig.id = signal_id
    sig.strategy_id = 1
    sig.symbol = "XAUUSD"
    sig.timeframe = "H1"
    sig.direction = direction
    sig.entry_price = entry_price
    sig.stop_loss = stop_loss
    sig.take_profit_1 = take_profit_1
    sig.take_profit_2 = take_profit_2
    sig.risk_reward = Decimal("1.50")
    sig.confidence = Decimal("75.00")
    sig.reasoning = "test signal"
    sig.status = status
    sig.created_at = created_at or datetime(2026, 2, 17, 10, 0, tzinfo=timezone.utc)
    sig.expires_at = expires_at
    return sig


# ---------------------------------------------------------------------------
# Pure logic tests: _evaluate_signal
# ---------------------------------------------------------------------------

class TestEvaluateSignal:
    """Test the pure evaluation logic with no I/O."""

    def setup_method(self):
        self.detector = OutcomeDetector(api_key="test-key")

    def test_buy_sl_hit(self):
        """BUY signal where bid price drops below SL -> sl_hit."""
        signal = _make_signal(direction="BUY", stop_loss=Decimal("2645.00"))
        spread = Decimal("0.30")
        # Bid price at 2644.00 is below SL of 2645.00
        result = self.detector._evaluate_signal(signal, 2644.00, spread)
        assert result == "sl_hit"

    def test_buy_tp1_hit(self):
        """BUY signal where bid price rises above TP1 (below TP2) -> tp1_hit."""
        signal = _make_signal(
            direction="BUY",
            take_profit_1=Decimal("2655.00"),
            take_profit_2=Decimal("2660.00"),
        )
        spread = Decimal("0.30")
        # Bid at 2656.00 -> above TP1 (2655), below TP2 (2660)
        result = self.detector._evaluate_signal(signal, 2656.00, spread)
        assert result == "tp1_hit"

    def test_buy_tp2_hit(self):
        """BUY signal where bid price rises above TP2 -> tp2_hit (priority over TP1)."""
        signal = _make_signal(
            direction="BUY",
            take_profit_1=Decimal("2655.00"),
            take_profit_2=Decimal("2660.00"),
        )
        spread = Decimal("0.30")
        # Bid at 2661.00 -> above both TP1 and TP2
        result = self.detector._evaluate_signal(signal, 2661.00, spread)
        assert result == "tp2_hit"

    def test_sell_sl_hit_with_spread(self):
        """SELL signal where ask price (bid + spread) rises above SL -> sl_hit."""
        signal = _make_signal(
            direction="SELL",
            entry_price=Decimal("2650.00"),
            stop_loss=Decimal("2655.00"),
            take_profit_1=Decimal("2645.00"),
            take_profit_2=Decimal("2640.00"),
        )
        spread = Decimal("0.30")
        # Bid = 2654.80 -> ask = 2654.80 + 0.30 = 2655.10 >= SL 2655.00
        result = self.detector._evaluate_signal(signal, 2654.80, spread)
        assert result == "sl_hit"

    def test_sell_tp1_hit(self):
        """SELL signal where bid price drops below TP1 -> tp1_hit."""
        signal = _make_signal(
            direction="SELL",
            entry_price=Decimal("2650.00"),
            stop_loss=Decimal("2655.00"),
            take_profit_1=Decimal("2645.00"),
            take_profit_2=Decimal("2640.00"),
        )
        spread = Decimal("0.30")
        # Bid at 2644.00 -> below TP1 (2645), above TP2 (2640)
        result = self.detector._evaluate_signal(signal, 2644.00, spread)
        assert result == "tp1_hit"

    def test_expired_signal(self):
        """Signal past expires_at -> expired."""
        past = datetime.now(timezone.utc) - timedelta(hours=1)
        signal = _make_signal(expires_at=past)
        spread = Decimal("0.30")
        # Price is between SL and TP, but signal is expired
        result = self.detector._evaluate_signal(signal, 2650.00, spread)
        assert result == "expired"

    def test_sl_priority_over_tp(self):
        """When both SL and TP could trigger, SL wins (decision 03-01)."""
        # BUY signal where price is BOTH below SL and above TP1
        # This happens if SL is set above TP1 (pathological but tests priority)
        signal = _make_signal(
            direction="BUY",
            entry_price=Decimal("2650.00"),
            stop_loss=Decimal("2656.00"),  # SL above entry (weird but tests priority)
            take_profit_1=Decimal("2655.00"),
            take_profit_2=Decimal("2660.00"),
        )
        spread = Decimal("0.30")
        # Price at 2655.50 -> below SL (2656) AND above TP1 (2655)
        result = self.detector._evaluate_signal(signal, 2655.50, spread)
        assert result == "sl_hit"

    def test_no_outcome_when_price_between_sl_tp(self):
        """Price between SL and TP levels -> no outcome, signal stays active."""
        signal = _make_signal(
            direction="BUY",
            stop_loss=Decimal("2645.00"),
            take_profit_1=Decimal("2655.00"),
        )
        spread = Decimal("0.30")
        # Price at 2650.00 -> between SL and TP
        result = self.detector._evaluate_signal(signal, 2650.00, spread)
        assert result is None


# ---------------------------------------------------------------------------
# PnL and duration calculation tests
# ---------------------------------------------------------------------------

class TestPnlAndDuration:
    """Test PnL pip calculation and duration minutes."""

    def setup_method(self):
        self.detector = OutcomeDetector(api_key="test-key")

    def test_pnl_calculation_buy(self):
        """BUY: pnl_pips = (exit - entry) / PIP_VALUE."""
        signal = _make_signal(
            direction="BUY",
            entry_price=Decimal("2650.00"),
        )
        # Exit at 2655.00 -> profit of 5.00 / 0.10 = 50 pips
        pnl = self.detector._calculate_pnl(signal, 2655.00)
        assert pnl == Decimal("50.00")

    def test_pnl_calculation_sell(self):
        """SELL: pnl_pips = (entry - exit) / PIP_VALUE."""
        signal = _make_signal(
            direction="SELL",
            entry_price=Decimal("2650.00"),
        )
        # Exit at 2645.00 -> profit of 5.00 / 0.10 = 50 pips
        pnl = self.detector._calculate_pnl(signal, 2645.00)
        assert pnl == Decimal("50.00")

    def test_pnl_negative_buy(self):
        """BUY that hits SL -> negative pnl."""
        signal = _make_signal(
            direction="BUY",
            entry_price=Decimal("2650.00"),
        )
        # Exit at 2645.00 -> loss of 5.00 / 0.10 = -50 pips
        pnl = self.detector._calculate_pnl(signal, 2645.00)
        assert pnl == Decimal("-50.00")

    def test_duration_minutes(self):
        """duration_minutes = (now - signal.created_at) in minutes."""
        created = datetime(2026, 2, 17, 10, 0, tzinfo=timezone.utc)
        signal = _make_signal(created_at=created)
        now = datetime(2026, 2, 17, 12, 30, tzinfo=timezone.utc)
        duration = self.detector._calculate_duration(signal, now)
        assert duration == 150  # 2.5 hours = 150 minutes


# ---------------------------------------------------------------------------
# Async integration tests (mocked I/O)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestCheckOutcomesAsync:
    """Test the async check_outcomes flow with mocked DB and API."""

    async def test_no_active_signals(self):
        """No active signals -> returns empty list, no API call made."""
        detector = OutcomeDetector(api_key="test-key")

        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        mock_session.execute.return_value = mock_result

        with patch.object(detector, "_fetch_current_price") as mock_fetch:
            outcomes = await detector.check_outcomes(mock_session)
            assert outcomes == []
            mock_fetch.assert_not_called()

    async def test_price_fetch_failure(self):
        """API returns None -> logs warning, returns empty list."""
        detector = OutcomeDetector(api_key="test-key")

        signal = _make_signal()
        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [signal]
        mock_session.execute.return_value = mock_result

        with patch.object(detector, "_fetch_current_price", return_value=None):
            outcomes = await detector.check_outcomes(mock_session)
            assert outcomes == []

    async def test_signal_status_updated(self):
        """After outcome detected, signal.status changes to result string."""
        detector = OutcomeDetector(api_key="test-key")

        signal = _make_signal(
            direction="BUY",
            stop_loss=Decimal("2645.00"),
            entry_price=Decimal("2650.00"),
        )
        assert signal.status == "active"

        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [signal]
        mock_session.execute.return_value = mock_result

        with patch.object(detector, "_fetch_current_price", return_value=2644.00):
            outcomes = await detector.check_outcomes(mock_session)
            assert len(outcomes) == 1
            assert signal.status == "sl_hit"
            assert outcomes[0].result == "sl_hit"

    async def test_outcome_fields_populated(self):
        """Outcome has correct result, exit_price, pnl_pips, duration_minutes."""
        created = datetime(2026, 2, 17, 10, 0, tzinfo=timezone.utc)
        detector = OutcomeDetector(api_key="test-key")

        signal = _make_signal(
            direction="BUY",
            entry_price=Decimal("2650.00"),
            take_profit_1=Decimal("2655.00"),
            take_profit_2=Decimal("2660.00"),
            stop_loss=Decimal("2645.00"),
            created_at=created,
        )

        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [signal]
        mock_session.execute.return_value = mock_result

        # Bid at 2656 -> TP1 hit
        with patch.object(detector, "_fetch_current_price", return_value=2656.00):
            with patch("app.services.outcome_detector.datetime") as mock_dt:
                now = datetime(2026, 2, 17, 12, 30, tzinfo=timezone.utc)
                mock_dt.now.return_value = now
                mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
                outcomes = await detector.check_outcomes(mock_session)

        assert len(outcomes) == 1
        outcome = outcomes[0]
        assert outcome.result == "tp1_hit"
        assert outcome.exit_price == Decimal("2656.00")
        # (2656 - 2650) / 0.10 = 60 pips
        assert outcome.pnl_pips == Decimal("60.00")
        assert outcome.duration_minutes == 150

    async def test_sell_tp2_hit_outcome(self):
        """SELL signal where price drops below TP2 -> tp2_hit."""
        detector = OutcomeDetector(api_key="test-key")

        signal = _make_signal(
            direction="SELL",
            entry_price=Decimal("2650.00"),
            stop_loss=Decimal("2655.00"),
            take_profit_1=Decimal("2645.00"),
            take_profit_2=Decimal("2640.00"),
        )

        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [signal]
        mock_session.execute.return_value = mock_result

        # Bid at 2639 -> below TP2 (2640) -> tp2_hit
        with patch.object(detector, "_fetch_current_price", return_value=2639.00):
            outcomes = await detector.check_outcomes(mock_session)

        assert len(outcomes) == 1
        assert outcomes[0].result == "tp2_hit"
        assert signal.status == "tp2_hit"

    async def test_expired_outcome_uses_current_price(self):
        """Expired signal records exit_price = current price."""
        detector = OutcomeDetector(api_key="test-key")

        past = datetime(2026, 2, 17, 8, 0, tzinfo=timezone.utc)
        signal = _make_signal(
            direction="BUY",
            entry_price=Decimal("2650.00"),
            expires_at=past,
            created_at=datetime(2026, 2, 17, 5, 0, tzinfo=timezone.utc),
        )

        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [signal]
        mock_session.execute.return_value = mock_result

        with patch.object(detector, "_fetch_current_price", return_value=2648.00):
            outcomes = await detector.check_outcomes(mock_session)

        assert len(outcomes) == 1
        assert outcomes[0].result == "expired"
        assert outcomes[0].exit_price == Decimal("2648.00")
        assert signal.status == "expired"
