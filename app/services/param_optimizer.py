"""Parameter optimization engine for strategy tuning.

Uses Latin Hypercube Sampling to explore parameter spaces, backtests each
combination, ranks by composite score, validates the top candidates via
walk-forward analysis AND Monte Carlo simulation to reject overfitted
or statistically insignificant parameter sets.

Exports:
    ParamOptimizer  -- main service class
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from decimal import Decimal

import numpy as np
from loguru import logger

from app.services.backtester import BacktestRunner
from app.services.metrics_calculator import BacktestMetrics, MetricsCalculator
from app.services.walk_forward import WalkForwardValidator
from app.strategies.base import BaseStrategy

import pandas as pd


# ---------------------------------------------------------------------------
# Parameter search ranges: (min, max, step) per strategy
# ---------------------------------------------------------------------------

PARAM_RANGES: dict[str, dict[str, tuple[float, float, float]]] = {
    "liquidity_sweep": {
        "SWING_ORDER": (3, 8, 1),
        "LOOKBACK": (30, 80, 10),
        "SL_ATR_MULT": (0.2, 1.0, 0.1),
        "TP1_RR": (1.0, 2.5, 0.25),
        "CONFIRM_BARS": (2, 6, 1),
    },
    "trend_continuation": {
        "EMA_FAST": (20, 60, 10),
        "PULLBACK_ATR_MULT": (0.5, 2.0, 0.25),
        "SL_ATR_MULT": (0.5, 2.0, 0.25),
        "TP1_RR": (1.5, 3.0, 0.25),
        "LOOKBACK_PULLBACK": (3, 10, 1),
    },
    "breakout_expansion": {
        "ATR_COMPRESSION": (0.3, 0.8, 0.05),
        "MIN_CONSOL_BARS": (4, 20, 2),
        "VOLUME_MULT": (1.0, 2.5, 0.25),
        "BREAKOUT_BODY_ATR": (1.0, 2.5, 0.25),
    },
}

# Number of parameter combinations to sample per strategy
NUM_SAMPLES = 200

# Minimum trades required for a combination to be considered
MIN_TRADES_OPTIMIZE = 10

# Composite score weights (same as StrategySelector)
SCORE_WEIGHTS: dict[str, float] = {
    "win_rate": 0.30,
    "profit_factor": 0.25,
    "sharpe_ratio": 0.15,
    "expectancy": 0.15,
    "max_drawdown": 0.15,  # inverted: lower is better
}

# Walk-forward: validate top N candidates
TOP_N_VALIDATE = 5

# Monte Carlo settings
MONTE_CARLO_RUNS = 1000
MONTE_CARLO_CONFIDENCE = 0.05  # reject if >5% of shuffles beat original

# Multi-window validation windows
VALIDATION_WINDOWS = [7, 14, 30]
MIN_WINDOWS_PASSING = 2  # must pass at least 2 of 3 windows


@dataclass
class OptimizationResult:
    """Result of optimizing a single strategy's parameters."""

    strategy_name: str
    best_params: dict[str, float]
    metrics: BacktestMetrics
    wfe_ratio: float | None
    is_overfitted: bool
    combinations_tested: int
    monte_carlo_pvalue: float | None = None


class ParamOptimizer:
    """Explores parameter spaces to find optimal strategy configurations.

    Workflow:
        1. Sample NUM_SAMPLES combinations via Latin Hypercube Sampling.
        2. Backtest each on a 30-day rolling window.
        3. Rank by composite score (same weights as StrategySelector).
        4. Walk-forward validate top 5 candidates.
        5. Monte Carlo simulate winning candidates (1000 shuffles).
        6. Multi-window validate across 7d, 14d, 30d.
        7. Return the best non-overfitted, statistically significant candidate.
    """

    def __init__(
        self,
        runner: BacktestRunner | None = None,
        wf_validator: WalkForwardValidator | None = None,
    ) -> None:
        self.runner = runner or BacktestRunner()
        self.wf_validator = wf_validator or WalkForwardValidator(runner=self.runner)
        self.metrics_calculator = MetricsCalculator()

    async def optimize_strategy(
        self,
        strategy_name: str,
        candles: pd.DataFrame,
    ) -> OptimizationResult | None:
        """Optimize parameters for a single strategy.

        Args:
            strategy_name: Registered strategy name.
            candles: Full H1 XAUUSD candle DataFrame.

        Returns:
            OptimizationResult with best params, or None if no viable
            combination found.
        """
        if strategy_name not in PARAM_RANGES:
            logger.warning(
                "No parameter ranges defined for strategy '{}', skipping",
                strategy_name,
            )
            return None

        ranges = PARAM_RANGES[strategy_name]
        strategy_cls = BaseStrategy.get_registry().get(strategy_name)
        if strategy_cls is None:
            logger.error("Strategy '{}' not in registry", strategy_name)
            return None

        # 1. Generate candidates via LHS
        candidates = self._generate_candidates(strategy_name, ranges)
        logger.info(
            "Optimizer: {} candidates for '{}' (including defaults)",
            len(candidates),
            strategy_name,
        )

        # 2. Backtest each candidate
        scored: list[tuple[dict[str, float], BacktestMetrics, float, list]] = []

        for idx, params in enumerate(candidates):
            try:
                strategy = strategy_cls(params=params)
                metrics, trades = self.runner.run_full_backtest(
                    strategy, candles, window_days=30
                )

                if metrics.total_trades < MIN_TRADES_OPTIMIZE:
                    continue

                score = self._composite_score(metrics)
                scored.append((params, metrics, score, trades))

            except Exception:
                logger.debug(
                    "Optimizer: candidate #{} for '{}' failed",
                    idx, strategy_name,
                )

            # Yield to event loop every 10 backtests
            if idx > 0 and idx % 10 == 0:
                await asyncio.sleep(0)

        if not scored:
            logger.warning(
                "Optimizer: no viable candidates for '{}' "
                "(all had <{} trades)",
                strategy_name,
                MIN_TRADES_OPTIMIZE,
            )
            return None

        logger.info(
            "Optimizer: {}/{} candidates viable for '{}'",
            len(scored),
            len(candidates),
            strategy_name,
        )

        # 3. Rank by composite score
        scored.sort(key=lambda x: x[2], reverse=True)

        # 4-6. Validate top N candidates (walk-forward + Monte Carlo + multi-window)
        for rank, (params, metrics, score, trades) in enumerate(scored[:TOP_N_VALIDATE]):
            try:
                strategy = strategy_cls(params=params)

                # 4. Walk-forward validation
                wf_result = self.wf_validator.validate(
                    strategy, candles, window_days=30
                )

                if wf_result.is_overfitted:
                    logger.info(
                        "Optimizer: candidate #{} for '{}' failed walk-forward",
                        rank, strategy_name,
                    )
                    continue

                # 5. Monte Carlo simulation
                mc_pvalue = self._monte_carlo_test(trades, metrics)
                if mc_pvalue > MONTE_CARLO_CONFIDENCE:
                    logger.info(
                        "Optimizer: candidate #{} for '{}' failed Monte Carlo "
                        "(p={:.3f} > {:.3f})",
                        rank, strategy_name, mc_pvalue, MONTE_CARLO_CONFIDENCE,
                    )
                    continue

                # 6. Multi-window validation
                windows_passed = 0
                for window in VALIDATION_WINDOWS:
                    try:
                        w_metrics, _ = self.runner.run_full_backtest(
                            strategy, candles, window_days=window
                        )
                        if (
                            w_metrics.total_trades >= MIN_TRADES_OPTIMIZE
                            and float(w_metrics.profit_factor) > 1.0
                        ):
                            windows_passed += 1
                    except Exception:
                        pass

                if windows_passed < MIN_WINDOWS_PASSING:
                    logger.info(
                        "Optimizer: candidate #{} for '{}' failed multi-window "
                        "({}/{} windows passed, need {})",
                        rank, strategy_name,
                        windows_passed, len(VALIDATION_WINDOWS),
                        MIN_WINDOWS_PASSING,
                    )
                    continue

                # All validations passed
                wfe_values = [
                    v
                    for v in [wf_result.wfe_win_rate, wf_result.wfe_profit_factor]
                    if v is not None
                ]
                avg_wfe = sum(wfe_values) / len(wfe_values) if wfe_values else None

                logger.info(
                    "Optimizer: best params for '{}': {} "
                    "(score={:.4f}, trades={}, wfe={:.3f}, mc_p={:.4f}, "
                    "windows={}/{})",
                    strategy_name,
                    {k: v for k, v in params.items() if k in ranges},
                    score,
                    metrics.total_trades,
                    avg_wfe or 0,
                    mc_pvalue,
                    windows_passed,
                    len(VALIDATION_WINDOWS),
                )

                return OptimizationResult(
                    strategy_name=strategy_name,
                    best_params=params,
                    metrics=metrics,
                    wfe_ratio=avg_wfe,
                    is_overfitted=False,
                    combinations_tested=len(candidates),
                    monte_carlo_pvalue=mc_pvalue,
                )

            except Exception:
                logger.exception(
                    "Optimizer: validation failed for '{}' candidate #{}",
                    strategy_name, rank,
                )

            # Yield between candidates
            await asyncio.sleep(0)

        # All top candidates failed validation -- return best with flag
        best_params, best_metrics, best_score, best_trades = scored[0]
        logger.warning(
            "Optimizer: all top candidates for '{}' failed validation, "
            "returning best with is_overfitted=True",
            strategy_name,
        )
        return OptimizationResult(
            strategy_name=strategy_name,
            best_params=best_params,
            metrics=best_metrics,
            wfe_ratio=None,
            is_overfitted=True,
            combinations_tested=len(candidates),
        )

    def _monte_carlo_test(
        self,
        trades: list,
        original_metrics: BacktestMetrics,
    ) -> float:
        """Run Monte Carlo simulation to test statistical significance.

        Shuffles the trade PnL sequence 1000 times and computes the profit
        factor each time. The p-value is the fraction of shuffled sequences
        that achieve a profit factor >= the original. A low p-value means
        the edge is real, not just a lucky ordering.

        Args:
            trades: List of SimulatedTrade objects.
            original_metrics: Metrics from the actual backtest.

        Returns:
            p-value (0.0 to 1.0). Lower is better.
        """
        if not trades or original_metrics.total_trades < MIN_TRADES_OPTIMIZE:
            return 1.0

        original_pf = float(original_metrics.profit_factor)
        pnl_values = np.array([float(t.pnl_pips) for t in trades])

        rng = np.random.default_rng()
        beats = 0

        for _ in range(MONTE_CARLO_RUNS):
            shuffled = rng.permutation(pnl_values)
            gross_profit = float(np.sum(shuffled[shuffled > 0]))
            gross_loss = float(abs(np.sum(shuffled[shuffled < 0])))

            if gross_loss == 0:
                shuffled_pf = gross_profit if gross_profit > 0 else 0.0
            else:
                shuffled_pf = gross_profit / gross_loss

            if shuffled_pf >= original_pf:
                beats += 1

        pvalue = beats / MONTE_CARLO_RUNS

        logger.debug(
            "Monte Carlo: original PF={:.4f}, {}/{} shuffles beat it (p={:.4f})",
            original_pf, beats, MONTE_CARLO_RUNS, pvalue,
        )
        return pvalue

    def _generate_candidates(
        self,
        strategy_name: str,
        ranges: dict[str, tuple[float, float, float]],
    ) -> list[dict[str, float]]:
        """Generate parameter candidates using Latin Hypercube Sampling.

        Always includes the current defaults as candidate #0.

        Args:
            strategy_name: Strategy name for looking up defaults.
            ranges: Dict of param_name -> (min, max, step).

        Returns:
            List of param dicts (full params, not just optimized ones).
        """
        strategy_cls = BaseStrategy.get_registry()[strategy_name]
        defaults = dict(strategy_cls.DEFAULT_PARAMS)

        # Candidate #0: current defaults
        candidates = [dict(defaults)]

        param_names = list(ranges.keys())
        n_params = len(param_names)
        n_samples = NUM_SAMPLES - 1  # one slot used by defaults

        if n_samples <= 0 or n_params == 0:
            return candidates

        # Build discrete value lists per parameter
        value_lists: list[list[float]] = []
        for name in param_names:
            lo, hi, step = ranges[name]
            values = []
            v = lo
            while v <= hi + step * 0.01:  # floating-point tolerance
                values.append(round(v, 4))
                v += step
            value_lists.append(values)

        # Latin Hypercube Sampling
        rng = np.random.default_rng()
        for _ in range(n_samples):
            param_dict = dict(defaults)
            for dim, name in enumerate(param_names):
                vals = value_lists[dim]
                idx = rng.integers(0, len(vals))
                param_dict[name] = vals[idx]
            candidates.append(param_dict)

        return candidates

    @staticmethod
    def _composite_score(metrics: BacktestMetrics) -> float:
        """Compute a composite score from backtest metrics.

        Uses the same weights as StrategySelector for consistency.
        Values are normalised to rough [0,1] ranges using heuristic caps.
        """
        wr = float(metrics.win_rate)  # already 0-1
        pf = min(float(metrics.profit_factor), 3.0) / 3.0
        sr = max(min(float(metrics.sharpe_ratio), 3.0), -1.0)
        sr_norm = (sr + 1.0) / 4.0  # map [-1, 3] to [0, 1]
        exp = max(min(float(metrics.expectancy), 50.0), -20.0)
        exp_norm = (exp + 20.0) / 70.0  # map [-20, 50] to [0, 1]
        dd = float(metrics.max_drawdown)  # 0-1 range already
        dd_inv = 1.0 - dd  # lower drawdown is better

        return (
            SCORE_WEIGHTS["win_rate"] * wr
            + SCORE_WEIGHTS["profit_factor"] * pf
            + SCORE_WEIGHTS["sharpe_ratio"] * sr_norm
            + SCORE_WEIGHTS["expectancy"] * exp_norm
            + SCORE_WEIGHTS["max_drawdown"] * dd_inv
        )
