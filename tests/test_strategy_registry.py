"""Integration tests for the strategy registry.

Proves Phase 2 success criteria:
- All 3 strategies registered (liquidity_sweep, trend_continuation, breakout_expansion)
- All strategies have distinct names, required attributes, and correct types
- Zero-change extensibility pattern (STRAT-07) works: adding a new strategy
  requires only one class definition and one import line
"""

import pandas as pd
import pytest

from app.strategies.base import BaseStrategy, CandidateSignal, Direction
from app.strategies.liquidity_sweep import LiquiditySweepStrategy
from app.strategies.trend_continuation import TrendContinuationStrategy
from app.strategies.breakout_expansion import BreakoutExpansionStrategy


class TestRegistryContents:
    """Verify all strategies are registered correctly."""

    def test_registry_has_three_strategies(self):
        """len(BaseStrategy.get_registry()) == 3."""
        registry = BaseStrategy.get_registry()
        assert len(registry) == 3, (
            f"Expected 3 strategies, got {len(registry)}: {list(registry.keys())}"
        )

    def test_registry_keys(self):
        """Keys are exactly {liquidity_sweep, trend_continuation, breakout_expansion}."""
        registry = BaseStrategy.get_registry()
        expected = {"liquidity_sweep", "trend_continuation", "breakout_expansion"}
        assert set(registry.keys()) == expected, (
            f"Expected keys {expected}, got {set(registry.keys())}"
        )


class TestRegistryAccess:
    """Verify registry access methods work correctly."""

    def test_get_strategy_returns_instance(self):
        """BaseStrategy.get_strategy('trend_continuation') returns a TrendContinuationStrategy."""
        instance = BaseStrategy.get_strategy("trend_continuation")
        assert isinstance(instance, TrendContinuationStrategy)

    def test_get_strategy_returns_liquidity_sweep(self):
        """BaseStrategy.get_strategy('liquidity_sweep') returns a LiquiditySweepStrategy."""
        instance = BaseStrategy.get_strategy("liquidity_sweep")
        assert isinstance(instance, LiquiditySweepStrategy)

    def test_get_strategy_returns_breakout_expansion(self):
        """BaseStrategy.get_strategy('breakout_expansion') returns a BreakoutExpansionStrategy."""
        instance = BaseStrategy.get_strategy("breakout_expansion")
        assert isinstance(instance, BreakoutExpansionStrategy)

    def test_get_strategy_unknown_raises(self):
        """BaseStrategy.get_strategy('nonexistent') raises KeyError."""
        with pytest.raises(KeyError, match="nonexistent"):
            BaseStrategy.get_strategy("nonexistent")


class TestStrategyAttributes:
    """Verify all strategies declare required attributes."""

    def test_all_strategies_have_required_attributes(self):
        """Each registered strategy has name (str), required_timeframes (list),
        min_candles (int > 0), and analyze (callable)."""
        registry = BaseStrategy.get_registry()
        for name, strategy_cls in registry.items():
            instance = strategy_cls()
            assert isinstance(instance.name, str), (
                f"Strategy '{name}' name is not str"
            )
            assert isinstance(instance.required_timeframes, list), (
                f"Strategy '{name}' required_timeframes is not list"
            )
            assert isinstance(instance.min_candles, int), (
                f"Strategy '{name}' min_candles is not int"
            )
            assert instance.min_candles > 0, (
                f"Strategy '{name}' min_candles should be > 0"
            )
            assert callable(instance.analyze), (
                f"Strategy '{name}' analyze is not callable"
            )

    def test_all_strategies_have_distinct_names(self):
        """No duplicate names in registry."""
        registry = BaseStrategy.get_registry()
        names = list(registry.keys())
        assert len(names) == len(set(names)), (
            f"Duplicate strategy names found: {names}"
        )

    def test_each_strategy_declares_min_candles(self):
        """Every strategy's min_candles is > 0."""
        registry = BaseStrategy.get_registry()
        for name, strategy_cls in registry.items():
            instance = strategy_cls()
            assert instance.min_candles > 0, (
                f"Strategy '{name}' min_candles = {instance.min_candles}"
            )

    def test_each_strategy_declares_timeframes(self):
        """Every strategy's required_timeframes is a non-empty list."""
        registry = BaseStrategy.get_registry()
        for name, strategy_cls in registry.items():
            instance = strategy_cls()
            assert len(instance.required_timeframes) > 0, (
                f"Strategy '{name}' has empty required_timeframes"
            )

    def test_distinct_min_candles_values(self):
        """Each strategy has distinct min_candles: 100, 200, 70."""
        ls = LiquiditySweepStrategy()
        tc = TrendContinuationStrategy()
        be = BreakoutExpansionStrategy()
        values = {ls.min_candles, tc.min_candles, be.min_candles}
        assert values == {100, 200, 70}, (
            f"Expected min_candles {{100, 200, 70}}, got {values}"
        )


class TestZeroChangeExtensibility:
    """Prove STRAT-07: adding a new strategy requires zero changes to
    base classes or downstream code."""

    def test_zero_change_extensibility(self):
        """Create an inline test strategy, verify it auto-registers, then
        clean up. This proves: adding a new strategy = one class definition."""
        # Record initial registry state
        initial_count = len(BaseStrategy.get_registry())
        initial_keys = set(BaseStrategy.get_registry().keys())

        # Define a new strategy inline
        class DummyTestStrategy(BaseStrategy):
            name = "test_dummy_extensibility"
            required_timeframes = ["M15"]
            min_candles = 50

            def analyze(self, candles: pd.DataFrame) -> list[CandidateSignal]:
                self.validate_data(candles)
                return []

        # It should auto-register
        registry = BaseStrategy.get_registry()
        assert "test_dummy_extensibility" in registry, (
            "Dummy strategy was not auto-registered"
        )
        assert len(registry) == initial_count + 1, (
            f"Expected {initial_count + 1} strategies, got {len(registry)}"
        )

        # The dummy strategy should be fully functional
        instance = BaseStrategy.get_strategy("test_dummy_extensibility")
        assert isinstance(instance, DummyTestStrategy)
        assert instance.name == "test_dummy_extensibility"
        assert instance.required_timeframes == ["M15"]
        assert instance.min_candles == 50

        # Clean up: remove from registry to avoid polluting other tests
        del BaseStrategy._registry["test_dummy_extensibility"]

        # Verify cleanup
        registry = BaseStrategy.get_registry()
        assert "test_dummy_extensibility" not in registry
        assert set(registry.keys()) == initial_keys
