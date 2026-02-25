"""Scheduled job functions for candle ingestion, backtesting, outcome detection,
data retention, and health digest.

These run outside the FastAPI request context, so sessions are created
directly from async_session_factory. All exceptions are caught to prevent
scheduler crashes. FailureTracker wires into each job to detect consecutive
failures and send Telegram system alerts.
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from loguru import logger
from sqlalchemy import func, select

from app.config import get_settings
from app.database import async_session_factory
from app.models.backtest_result import BacktestResult
from app.models.candle import Candle
from app.models.signal import Signal
from app.models.strategy import Strategy as StrategyModel
from app.services.candle_ingestor import CandleIngestor
from app.services.data_retention import DataRetentionService
from app.services.failure_tracker import FailureTracker
from app.services.outcome_detector import OutcomeDetector
from app.services.telegram_notifier import TelegramNotifier


async def refresh_candles(timeframe: str) -> None:
    """Fetch and store new candles for a given timeframe, then check for gaps.

    This function is registered as an APScheduler job. It creates its own
    database session (not via FastAPI Depends) and wraps all work in try/except
    to prevent job failures from killing the scheduler.

    Args:
        timeframe: Internal timeframe code (M15, H1, H4, D1).
    """
    job_id = f"refresh_candles_{timeframe}"
    try:
        settings = get_settings()
        ingestor = CandleIngestor(api_key=settings.twelve_data_api_key)

        async with async_session_factory() as session:
            count = await ingestor.fetch_and_store(session, "XAUUSD", timeframe)

            # Check for gaps in the last 7 days
            now = datetime.now(timezone.utc)
            seven_days_ago = now - timedelta(days=7)
            gaps = await ingestor.detect_gaps(
                session, "XAUUSD", timeframe, start=seven_days_ago, end=now
            )

            logger.info(
                "refresh_candles complete | timeframe={timeframe} "
                "candles_stored={count} gaps_found={gaps}",
                timeframe=timeframe,
                count=count,
                gaps=len(gaps),
            )

        FailureTracker.record_success(job_id)

    except Exception as exc:
        logger.exception(
            "refresh_candles failed | timeframe={timeframe}",
            timeframe=timeframe,
        )
        count = FailureTracker.record_failure(job_id)
        if FailureTracker.should_alert(job_id):
            settings = get_settings()
            notifier = TelegramNotifier(
                bot_token=settings.telegram_bot_token,
                chat_id=settings.telegram_chat_id,
            )
            err_type = type(exc).__name__
            err_msg = str(exc)[:200]
            await notifier.notify_system_alert(
                "Candle Refresh Failing",
                f"{timeframe} refresh has failed {count} consecutive times\n\n"
                f"<b>Error:</b> {err_type}: {err_msg}",
            )


async def run_daily_backtests() -> None:
    """Run backtests for all registered strategies.

    Executed every 4 hours via APScheduler CronTrigger. For each registered
    strategy, runs rolling backtests on 7, 14, 30, and 60-day windows,
    persists results to the backtest_results table, then runs walk-forward
    validation and persists OOS results with overfitting flags.

    Shorter windows (7d, 14d) capture recent regime changes faster so the
    strategy selector can adapt quickly to shifting market conditions.

    Creates its own database session. All exceptions are caught at the top
    level to prevent scheduler crashes.
    """
    try:
        # Import strategies inside function to trigger registration
        # and avoid circular imports at module level
        from app.strategies import BaseStrategy  # noqa: F401
        from app.strategies import candles_to_dataframe  # noqa: F401
        from app.strategies.liquidity_sweep import LiquiditySweepStrategy  # noqa: F401
        from app.strategies.trend_continuation import TrendContinuationStrategy  # noqa: F401
        from app.strategies.breakout_expansion import BreakoutExpansionStrategy  # noqa: F401

        from app.services.backtester import BacktestRunner
        from app.services.walk_forward import WalkForwardValidator

        runner = BacktestRunner()
        wf_validator = WalkForwardValidator(runner=runner)

        async with async_session_factory() as session:
            # Query all H1 XAUUSD candles ordered by timestamp ascending
            stmt = (
                select(Candle)
                .where(Candle.symbol == "XAUUSD", Candle.timeframe == "H1")
                .order_by(Candle.timestamp.asc())
            )
            result = await session.execute(stmt)
            candle_rows = result.scalars().all()

            if not candle_rows:
                logger.warning("run_daily_backtests: no H1 XAUUSD candles found, skipping")
                return

            df = candles_to_dataframe(candle_rows)

            # Minimum candle count: 7 days * 24 H1 bars + 72 forward bars
            min_candles = 7 * 24 + 72
            if len(df) < min_candles:
                logger.warning(
                    f"run_daily_backtests: insufficient candles "
                    f"(have {len(df)}, need {min_candles}), skipping"
                )
                return

            # Build strategy_name -> strategy_id lookup from DB
            strat_stmt = select(StrategyModel)
            strat_result = await session.execute(strat_stmt)
            db_strategies = {s.name: s.id for s in strat_result.scalars().all()}

            registry = BaseStrategy.get_registry()
            total_results = 0
            total_wf_results = 0

            # Load optimized params for each strategy
            from app.models.optimized_params import OptimizedParams
            from sqlalchemy import and_
            opt_params_lookup: dict[str, dict[str, float]] = {}
            for sname in registry:
                opt_stmt = (
                    select(OptimizedParams.params)
                    .where(
                        and_(
                            OptimizedParams.strategy_name == sname,
                            OptimizedParams.is_active.is_(True),
                            OptimizedParams.is_overfitted.isnot(True),
                        )
                    )
                    .order_by(OptimizedParams.created_at.desc())
                    .limit(1)
                )
                opt_result = await session.execute(opt_stmt)
                opt_row = opt_result.scalar_one_or_none()
                if opt_row:
                    opt_params_lookup[sname] = opt_row

            for strategy_name, strategy_cls in registry.items():
                opt_params = opt_params_lookup.get(strategy_name)
                strategy = strategy_cls(params=opt_params)
                strategy_id = db_strategies.get(strategy_name)

                if strategy_id is None:
                    logger.warning(
                        f"run_daily_backtests: strategy '{strategy_name}' "
                        f"not found in strategies table, skipping"
                    )
                    continue

                # Run standard backtests on 7, 14, 30, and 60-day windows
                for window_days in [7, 14, 30, 60]:
                    try:
                        metrics, trades = runner.run_full_backtest(
                            strategy, df, window_days
                        )

                        if metrics.total_trades == 0:
                            logger.info(
                                f"run_daily_backtests: 0 trades for "
                                f"'{strategy_name}' window={window_days}d, skipping persist"
                            )
                            continue

                        # Determine date range from candle data
                        start_date = df["timestamp"].iloc[0]
                        end_date = df["timestamp"].iloc[-1]

                        backtest_result = BacktestResult(
                            strategy_id=strategy_id,
                            timeframe="H1",
                            window_days=window_days,
                            start_date=start_date,
                            end_date=end_date,
                            win_rate=metrics.win_rate,
                            profit_factor=metrics.profit_factor,
                            sharpe_ratio=metrics.sharpe_ratio,
                            max_drawdown=metrics.max_drawdown,
                            expectancy=metrics.expectancy,
                            total_trades=metrics.total_trades,
                            is_walk_forward=False,
                            is_overfitted=None,
                            walk_forward_efficiency=None,
                            spread_model="session_aware",
                        )
                        session.add(backtest_result)
                        total_results += 1

                    except Exception:
                        logger.exception(
                            f"run_daily_backtests: error running backtest for "
                            f"'{strategy_name}' window={window_days}d"
                        )

                # Run walk-forward validation
                try:
                    wf_result = wf_validator.validate(strategy, df, window_days=30)

                    if wf_result.oos_metrics.total_trades == 0:
                        logger.info(
                            f"run_daily_backtests: 0 OOS trades for "
                            f"'{strategy_name}' walk-forward, skipping persist"
                        )
                    else:
                        # Compute average WFE for persistence
                        wfe_values = [
                            v for v in [wf_result.wfe_win_rate, wf_result.wfe_profit_factor]
                            if v is not None
                        ]
                        avg_wfe = (
                            Decimal(str(round(sum(wfe_values) / len(wfe_values), 4)))
                            if wfe_values
                            else None
                        )

                        start_date = df["timestamp"].iloc[0]
                        end_date = df["timestamp"].iloc[-1]

                        wf_backtest_result = BacktestResult(
                            strategy_id=strategy_id,
                            timeframe="H1",
                            window_days=30,
                            start_date=start_date,
                            end_date=end_date,
                            win_rate=wf_result.oos_metrics.win_rate,
                            profit_factor=wf_result.oos_metrics.profit_factor,
                            sharpe_ratio=wf_result.oos_metrics.sharpe_ratio,
                            max_drawdown=wf_result.oos_metrics.max_drawdown,
                            expectancy=wf_result.oos_metrics.expectancy,
                            total_trades=wf_result.oos_metrics.total_trades,
                            is_walk_forward=True,
                            is_overfitted=wf_result.is_overfitted,
                            walk_forward_efficiency=avg_wfe,
                            spread_model="session_aware",
                        )
                        session.add(wf_backtest_result)
                        total_wf_results += 1

                except Exception:
                    logger.exception(
                        f"run_daily_backtests: error in walk-forward validation "
                        f"for '{strategy_name}'"
                    )

            await session.commit()

            logger.info(
                f"run_daily_backtests complete | "
                f"strategies={len(registry)} | "
                f"backtest_results={total_results} | "
                f"walk_forward_results={total_wf_results}"
            )

    except Exception:
        logger.exception("run_daily_backtests failed")


async def run_signal_scanner() -> None:
    """Run the signal pipeline to scan for new trade signals.

    Executed every 30 minutes at :02 and :32 via APScheduler CronTrigger.
    The dedup window in SignalGenerator prevents duplicate signals, so no
    stale data guard is needed.

    Creates its own database session. All exceptions are caught at the top
    level to prevent scheduler crashes.
    """
    try:
        from app.services.strategy_selector import StrategySelector
        from app.services.signal_generator import SignalGenerator
        from app.services.risk_manager import RiskManager
        from app.services.gold_intelligence import GoldIntelligence
        from app.services.signal_pipeline import SignalPipeline

        async with async_session_factory() as session:

            # Instantiate services and run pipeline
            selector = StrategySelector()
            generator = SignalGenerator()
            risk_manager = RiskManager()
            gold_intel = GoldIntelligence()

            pipeline = SignalPipeline(selector, generator, risk_manager, gold_intel)
            signals = await pipeline.run(session)

            # Send Telegram notifications for new signals (fire-and-forget)
            if signals:
                settings = get_settings()
                notifier = TelegramNotifier(
                    bot_token=settings.telegram_bot_token,
                    chat_id=settings.telegram_chat_id,
                )
                if notifier.enabled:
                    # Look up strategy names via session.get() (cached per strategy_id)
                    strat_lookup: dict[int, str] = {}
                    for sig in signals:
                        if sig.strategy_id not in strat_lookup:
                            strat_row = await session.get(StrategyModel, sig.strategy_id)
                            strat_lookup[sig.strategy_id] = (
                                strat_row.name if strat_row else "Unknown"
                            )
                        await notifier.notify_signal(
                            sig, strategy_name=strat_lookup[sig.strategy_id]
                        )

            logger.info(
                "run_signal_scanner complete | signals_generated={}",
                len(signals),
            )

        FailureTracker.record_success("run_signal_scanner")

    except Exception as exc:
        logger.exception("run_signal_scanner failed")
        count = FailureTracker.record_failure("run_signal_scanner")
        if FailureTracker.should_alert("run_signal_scanner"):
            settings = get_settings()
            notifier = TelegramNotifier(
                bot_token=settings.telegram_bot_token,
                chat_id=settings.telegram_chat_id,
            )
            err_type = type(exc).__name__
            err_msg = str(exc)[:200]
            await notifier.notify_system_alert(
                "Signal Scanner Failing",
                f"Signal scanner has failed {count} consecutive times\n\n"
                f"<b>Error:</b> {err_type}: {err_msg}",
            )


async def check_outcomes() -> None:
    """Check all active signals for outcome detection.

    Runs every 30 seconds via APScheduler IntervalTrigger. Fetches current
    XAUUSD price, checks against all active signal SL/TP/expiry levels,
    records outcomes, updates signal status, and sends Telegram notifications.

    Creates its own database session. All exceptions are caught at the top
    level to prevent scheduler crashes.
    """
    try:
        settings = get_settings()
        detector = OutcomeDetector(api_key=settings.twelve_data_api_key)

        async with async_session_factory() as session:
            outcomes = await detector.check_outcomes(session)

            if outcomes:
                # Send Telegram notifications for each outcome
                notifier = TelegramNotifier(
                    bot_token=settings.telegram_bot_token,
                    chat_id=settings.telegram_chat_id,
                )
                if notifier.enabled:
                    for outcome in outcomes:
                        signal = await session.get(Signal, outcome.signal_id)
                        if signal:
                            await notifier.notify_outcome(signal, outcome)

                # Run feedback checks (degradation detection, circuit breaker)
                from app.services.feedback_controller import FeedbackController

                feedback = FeedbackController()

                # Collect unique strategy IDs from detected outcomes
                strategy_ids: set[int] = set()
                for outcome in outcomes:
                    signal = await session.get(Signal, outcome.signal_id)
                    if signal:
                        strategy_ids.add(signal.strategy_id)

                # Check degradation/recovery for each affected strategy
                for sid in strategy_ids:
                    strat_row = await session.get(StrategyModel, sid)
                    strat_name = strat_row.name if strat_row else f"strategy_{sid}"

                    is_degraded, reason = await feedback.check_degradation(
                        session, sid
                    )
                    if is_degraded and reason:
                        await notifier.notify_degradation(strat_name, reason)

                    recovered = await feedback.check_recovery(session, sid)
                    if recovered:
                        await notifier.notify_degradation(
                            strat_name,
                            "Metrics recovered above thresholds",
                            is_recovery=True,
                        )

                # Check circuit breaker (global, not per-strategy)
                await feedback.check_circuit_breaker(session)

                logger.info(
                    "check_outcomes complete | outcomes_detected={}",
                    len(outcomes),
                )
            # Only log at debug level when no outcomes (runs every 30s, avoid log spam)
            else:
                logger.debug("check_outcomes: no outcomes detected")

        FailureTracker.record_success("check_outcomes")

    except Exception as exc:
        logger.exception("check_outcomes failed")
        count = FailureTracker.record_failure("check_outcomes")
        if FailureTracker.should_alert("check_outcomes"):
            settings = get_settings()
            notifier = TelegramNotifier(
                bot_token=settings.telegram_bot_token,
                chat_id=settings.telegram_chat_id,
            )
            err_type = type(exc).__name__
            err_msg = str(exc)[:200]
            await notifier.notify_system_alert(
                "Outcome Detection Failing",
                f"Outcome detection has failed {count} consecutive times\n\n"
                f"<b>Error:</b> {err_type}: {err_msg}",
            )


async def run_data_retention() -> None:
    """Run data retention policies to prune old candle and backtest data.

    Executed daily at 03:00 UTC (after backtests at 02:00). Deletes M15
    candles >90 days old, H1 candles >365 days old, and backtest results
    >180 days old. H4/D1 candles and signals/outcomes are never pruned.

    Creates its own database session. All exceptions are caught at the top
    level to prevent scheduler crashes.
    """
    try:
        retention = DataRetentionService()

        async with async_session_factory() as session:
            results = await retention.run(session)

            total_pruned = sum(results.values())
            logger.info(
                "run_data_retention complete | total_pruned={total} | details={results}",
                total=total_pruned,
                results=results,
            )

        FailureTracker.record_success("run_data_retention")

    except Exception as exc:
        logger.exception("run_data_retention failed")
        count = FailureTracker.record_failure("run_data_retention")
        if FailureTracker.should_alert("run_data_retention"):
            settings = get_settings()
            notifier = TelegramNotifier(
                bot_token=settings.telegram_bot_token,
                chat_id=settings.telegram_chat_id,
            )
            err_type = type(exc).__name__
            err_msg = str(exc)[:200]
            await notifier.notify_system_alert(
                "Data Retention Failing",
                f"Data retention job has failed {count} consecutive times\n\n"
                f"<b>Error:</b> {err_type}: {err_msg}",
            )


async def send_health_digest() -> None:
    """Send a daily health digest via Telegram with operational statistics.

    Executed daily at 06:00 UTC. Collects counts of active signals, today's
    outcomes, candle data per timeframe, and any consecutive job failure
    counts. Formats and sends as a Telegram message.

    Creates its own database session. All exceptions are caught at the top
    level to prevent scheduler crashes.
    """
    try:
        from app.models.outcome import Outcome

        settings = get_settings()

        async with async_session_factory() as session:
            # Count active signals
            stmt = select(func.count()).select_from(Signal).where(
                Signal.status == "active"
            )
            result = await session.execute(stmt)
            active_signals = result.scalar() or 0

            # Count today's outcomes
            today_start = datetime.now(timezone.utc).replace(
                hour=0, minute=0, second=0, microsecond=0
            )
            stmt = select(func.count()).select_from(Outcome).where(
                Outcome.detected_at >= today_start
            )
            result = await session.execute(stmt)
            outcomes_today = result.scalar() or 0

            # Count candles per timeframe
            candle_counts: dict[str, int] = {}
            for tf in ["M15", "H1", "H4", "D1"]:
                stmt = select(func.count()).select_from(Candle).where(
                    Candle.symbol == "XAUUSD",
                    Candle.timeframe == tf,
                )
                result = await session.execute(stmt)
                candle_counts[tf] = result.scalar() or 0

            # Collect job failure counts
            job_ids = [
                "refresh_candles_M15",
                "refresh_candles_H1",
                "refresh_candles_H4",
                "refresh_candles_D1",
                "run_daily_backtests",
                "run_signal_scanner",
                "check_outcomes",
                "run_data_retention",
            ]
            job_failures = {
                jid: FailureTracker.get_count(jid) for jid in job_ids
            }

            stats = {
                "active_signals": active_signals,
                "outcomes_today": outcomes_today,
                "candles_m15": candle_counts["M15"],
                "candles_h1": candle_counts["H1"],
                "candles_h4": candle_counts["H4"],
                "candles_d1": candle_counts["D1"],
                "job_failures": job_failures,
            }

            notifier = TelegramNotifier(
                bot_token=settings.telegram_bot_token,
                chat_id=settings.telegram_chat_id,
            )
            await notifier.notify_health_digest(stats)

            logger.info(
                "send_health_digest complete | active_signals={active} outcomes_today={outcomes}",
                active=active_signals,
                outcomes=outcomes_today,
            )

        FailureTracker.record_success("send_health_digest")

    except Exception as exc:
        logger.exception("send_health_digest failed")
        count = FailureTracker.record_failure("send_health_digest")
        if FailureTracker.should_alert("send_health_digest"):
            settings = get_settings()
            notifier = TelegramNotifier(
                bot_token=settings.telegram_bot_token,
                chat_id=settings.telegram_chat_id,
            )
            err_type = type(exc).__name__
            err_msg = str(exc)[:200]
            await notifier.notify_system_alert(
                "Health Digest Failing",
                f"Health digest job has failed {count} consecutive times\n\n"
                f"<b>Error:</b> {err_type}: {err_msg}",
            )


async def run_param_optimization() -> None:
    """Run parameter optimization for all registered strategies.

    Executed every 6 hours via APScheduler CronTrigger. For each strategy,
    samples parameter combinations, backtests them, validates the best via
    walk-forward, and persists the winning params to the optimized_params table.

    Creates its own database session. All exceptions are caught at the top
    level to prevent scheduler crashes.
    """
    try:
        from app.strategies.base import BaseStrategy, candles_to_dataframe
        from app.strategies.liquidity_sweep import LiquiditySweepStrategy  # noqa: F401
        from app.strategies.trend_continuation import TrendContinuationStrategy  # noqa: F401
        from app.strategies.breakout_expansion import BreakoutExpansionStrategy  # noqa: F401

        from app.services.param_optimizer import ParamOptimizer, PARAM_RANGES
        from app.models.optimized_params import OptimizedParams

        optimizer = ParamOptimizer()

        async with async_session_factory() as session:
            # Load all H1 XAUUSD candles
            stmt = (
                select(Candle)
                .where(Candle.symbol == "XAUUSD", Candle.timeframe == "H1")
                .order_by(Candle.timestamp.asc())
            )
            result = await session.execute(stmt)
            candle_rows = result.scalars().all()

            if not candle_rows:
                logger.warning("run_param_optimization: no H1 XAUUSD candles, skipping")
                return

            df = candles_to_dataframe(candle_rows)

            # Need enough data for 30-day backtest + walk-forward
            min_candles = 30 * 24 + 72
            if len(df) < min_candles:
                logger.warning(
                    "run_param_optimization: insufficient candles "
                    f"(have {len(df)}, need {min_candles}), skipping"
                )
                return

            # Build strategy_name -> strategy_id lookup
            strat_stmt = select(StrategyModel)
            strat_result = await session.execute(strat_stmt)
            db_strategies = {s.name: s.id for s in strat_result.scalars().all()}

            settings = get_settings()
            notifier = TelegramNotifier(
                bot_token=settings.telegram_bot_token,
                chat_id=settings.telegram_chat_id,
            )

            optimized_count = 0

            for strategy_name in PARAM_RANGES:
                strategy_id = db_strategies.get(strategy_name)
                if strategy_id is None:
                    logger.warning(
                        "run_param_optimization: strategy '{}' not in DB, skipping",
                        strategy_name,
                    )
                    continue

                try:
                    opt_result = await optimizer.optimize_strategy(strategy_name, df)

                    if opt_result is None:
                        logger.info(
                            "run_param_optimization: no viable params for '{}'",
                            strategy_name,
                        )
                        continue

                    # Deactivate previous active params for this strategy
                    from sqlalchemy import update, and_
                    deactivate_stmt = (
                        update(OptimizedParams)
                        .where(
                            and_(
                                OptimizedParams.strategy_name == strategy_name,
                                OptimizedParams.is_active.is_(True),
                            )
                        )
                        .values(is_active=False)
                    )
                    await session.execute(deactivate_stmt)

                    # Persist new optimized params
                    opt_row = OptimizedParams(
                        strategy_id=strategy_id,
                        strategy_name=strategy_name,
                        params=opt_result.best_params,
                        win_rate=opt_result.metrics.win_rate,
                        profit_factor=opt_result.metrics.profit_factor,
                        sharpe_ratio=opt_result.metrics.sharpe_ratio,
                        expectancy=opt_result.metrics.expectancy,
                        total_trades=opt_result.metrics.total_trades,
                        wfe_ratio=(
                            Decimal(str(round(opt_result.wfe_ratio, 4)))
                            if opt_result.wfe_ratio is not None
                            else None
                        ),
                        is_overfitted=opt_result.is_overfitted,
                        is_active=not opt_result.is_overfitted,
                        combinations_tested=opt_result.combinations_tested,
                    )
                    session.add(opt_row)
                    optimized_count += 1

                    # Send Telegram notification
                    if notifier.enabled:
                        # Build param summary (only optimized params)
                        opt_params_summary = {
                            k: v
                            for k, v in opt_result.best_params.items()
                            if k in PARAM_RANGES.get(strategy_name, {})
                        }
                        text = (
                            f"\U0001f527 <b>Params Optimized: {strategy_name}</b>\n\n"
                            f"<b>Params:</b> {opt_params_summary}\n"
                            f"<b>Win Rate:</b> {opt_result.metrics.win_rate}\n"
                            f"<b>Profit Factor:</b> {opt_result.metrics.profit_factor}\n"
                            f"<b>Trades:</b> {opt_result.metrics.total_trades}\n"
                            f"<b>Overfitted:</b> {opt_result.is_overfitted}\n"
                            f"<b>Combinations:</b> {opt_result.combinations_tested}"
                        )
                        try:
                            await notifier._send_message(text)
                        except Exception:
                            logger.exception(
                                "Telegram notification failed for param optimization"
                            )

                except Exception:
                    logger.exception(
                        "run_param_optimization: error optimizing '{}'",
                        strategy_name,
                    )

            await session.commit()

            logger.info(
                "run_param_optimization complete | optimized={}",
                optimized_count,
            )

        FailureTracker.record_success("run_param_optimization")

    except Exception as exc:
        logger.exception("run_param_optimization failed")
        count = FailureTracker.record_failure("run_param_optimization")
        if FailureTracker.should_alert("run_param_optimization"):
            settings = get_settings()
            notifier = TelegramNotifier(
                bot_token=settings.telegram_bot_token,
                chat_id=settings.telegram_chat_id,
            )
            err_type = type(exc).__name__
            err_msg = str(exc)[:200]
            await notifier.notify_system_alert(
                "Param Optimization Failing",
                f"Parameter optimization has failed {count} consecutive times\n\n"
                f"<b>Error:</b> {err_type}: {err_msg}",
            )
