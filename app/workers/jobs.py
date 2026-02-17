"""Scheduled job functions for candle ingestion, backtesting, and outcome detection.

These run outside the FastAPI request context, so sessions are created
directly from async_session_factory. All exceptions are caught to prevent
scheduler crashes.
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from loguru import logger
from sqlalchemy import select

from app.config import get_settings
from app.database import async_session_factory
from app.models.backtest_result import BacktestResult
from app.models.candle import Candle
from app.models.signal import Signal
from app.models.strategy import Strategy as StrategyModel
from app.services.candle_ingestor import CandleIngestor
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

    except Exception:
        logger.exception(
            "refresh_candles failed | timeframe={timeframe}",
            timeframe=timeframe,
        )


async def run_daily_backtests() -> None:
    """Run daily backtests for all registered strategies.

    Executed at 02:00 UTC via APScheduler CronTrigger. For each registered
    strategy, runs rolling backtests on 30-day and 60-day windows, persists
    results to the backtest_results table, then runs walk-forward validation
    and persists OOS results with overfitting flags.

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

            # Minimum candle count: 30 days * 24 H1 bars + 72 forward bars
            min_candles = 30 * 24 + 72
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

            for strategy_name, strategy_cls in registry.items():
                strategy = strategy_cls()
                strategy_id = db_strategies.get(strategy_name)

                if strategy_id is None:
                    logger.warning(
                        f"run_daily_backtests: strategy '{strategy_name}' "
                        f"not found in strategies table, skipping"
                    )
                    continue

                # Run standard backtests on 30-day and 60-day windows
                for window_days in [30, 60]:
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


# Module-level variable for stale data detection (SIG-08)
_last_scanned_ts: datetime | None = None


async def run_signal_scanner() -> None:
    """Run the signal pipeline to scan for new trade signals.

    Executed at :02 every hour via APScheduler CronTrigger. Checks for new
    H1 candle data before running the full pipeline to avoid duplicate
    processing (SIG-08 stale data guard).

    Creates its own database session. All exceptions are caught at the top
    level to prevent scheduler crashes.
    """
    global _last_scanned_ts

    try:
        from app.services.strategy_selector import StrategySelector
        from app.services.signal_generator import SignalGenerator
        from app.services.risk_manager import RiskManager
        from app.services.gold_intelligence import GoldIntelligence
        from app.services.signal_pipeline import SignalPipeline

        async with async_session_factory() as session:
            # Stale data guard: check if there's a new H1 candle since last scan
            stmt = (
                select(Candle.timestamp)
                .where(Candle.symbol == "XAUUSD", Candle.timeframe == "H1")
                .order_by(Candle.timestamp.desc())
                .limit(1)
            )
            result = await session.execute(stmt)
            latest_candle_ts = result.scalar_one_or_none()

            if latest_candle_ts is None:
                logger.warning("run_signal_scanner: no H1 XAUUSD candles found, skipping")
                return

            if _last_scanned_ts is not None and latest_candle_ts == _last_scanned_ts:
                logger.info(
                    "run_signal_scanner: no new candle data since last scan "
                    "(latest={}), skipping",
                    latest_candle_ts,
                )
                return

            _last_scanned_ts = latest_candle_ts
            logger.info(
                "run_signal_scanner: new candle data detected (ts={}), running pipeline",
                latest_candle_ts,
            )

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

    except Exception:
        logger.exception("run_signal_scanner failed")


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

                logger.info(
                    "check_outcomes complete | outcomes_detected={}",
                    len(outcomes),
                )
            # Only log at debug level when no outcomes (runs every 30s, avoid log spam)
            else:
                logger.debug("check_outcomes: no outcomes detected")
    except Exception:
        logger.exception("check_outcomes failed")
