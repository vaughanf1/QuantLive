"""Dashboard API endpoint — matrix-style operational dashboard."""

import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session
from app.models.backtest_result import BacktestResult
from app.models.candle import Candle
from app.models.optimized_params import OptimizedParams
from app.models.outcome import Outcome
from app.models.signal import Signal
from app.models.strategy import Strategy
from app.models.strategy_performance import StrategyPerformance
from app.workers.scheduler import scheduler

router = APIRouter(prefix="/dashboard", tags=["dashboard"])

templates = Jinja2Templates(
    directory=str(Path(__file__).resolve().parent.parent / "templates")
)

# Track app start time for uptime
_start_time: datetime.datetime = datetime.datetime.now(datetime.UTC)


@router.get("/", response_class=HTMLResponse)
async def dashboard_page(request: Request):
    """Serve the dashboard HTML page."""
    return templates.TemplateResponse(request=request, name="dashboard.html")


@router.get("/data")
async def dashboard_data(
    session: AsyncSession = Depends(get_session),
):
    """Return all dashboard data as a single JSON payload."""
    now = datetime.datetime.now(datetime.UTC)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    uptime = (now - _start_time).total_seconds()

    # --- System status ---
    db_status = "connected"
    try:
        from sqlalchemy import text
        await session.execute(text("SELECT 1"))
    except Exception:
        db_status = "disconnected"

    scheduler_status = "running" if scheduler.running else "stopped"

    # --- Scheduler jobs ---
    jobs = []
    for job in scheduler.get_jobs():
        jobs.append({
            "id": job.id,
            "name": job.name,
            "next_run": job.next_run_time.isoformat() if job.next_run_time else None,
            "trigger": str(job.trigger),
        })

    # --- Signal stats ---
    active_signals = 0
    signals_today = 0
    total_signals = 0
    try:
        result = await session.execute(
            select(func.count()).select_from(Signal).where(Signal.status == "active")
        )
        active_signals = result.scalar_one()

        result = await session.execute(
            select(func.count()).select_from(Signal).where(
                Signal.created_at >= today_start
            )
        )
        signals_today = result.scalar_one()

        result = await session.execute(
            select(func.count()).select_from(Signal)
        )
        total_signals = result.scalar_one()
    except Exception:
        pass

    # --- Recent signals (last 20) ---
    recent_signals = []
    try:
        query = (
            select(Signal, Outcome, Strategy.name)
            .outerjoin(Outcome, Signal.id == Outcome.signal_id)
            .outerjoin(Strategy, Signal.strategy_id == Strategy.id)
            .order_by(Signal.created_at.desc())
            .limit(20)
        )
        result = await session.execute(query)
        for signal, outcome, strategy_name in result.all():
            recent_signals.append({
                "id": signal.id,
                "direction": signal.direction,
                "entry": float(signal.entry_price),
                "sl": float(signal.stop_loss),
                "tp1": float(signal.take_profit_1),
                "tp2": float(signal.take_profit_2),
                "rr": float(signal.risk_reward),
                "confidence": float(signal.confidence),
                "status": signal.status,
                "strategy": strategy_name or "Unknown",
                "created": signal.created_at.isoformat() if signal.created_at else None,
                "result": outcome.result if outcome else None,
                "pnl": float(outcome.pnl_pips) if outcome else None,
            })
    except Exception:
        pass

    # --- Outcome stats ---
    wins = 0
    losses = 0
    total_pnl = 0.0
    try:
        result = await session.execute(
            select(
                func.count().filter(Outcome.result.in_(["tp1_hit", "tp2_hit"])).label("wins"),
                func.count().filter(Outcome.result == "sl_hit").label("losses"),
                func.coalesce(func.sum(Outcome.pnl_pips), 0).label("total_pnl"),
            ).select_from(Outcome)
        )
        row = result.one()
        wins = row.wins
        losses = row.losses
        total_pnl = float(row.total_pnl)
    except Exception:
        pass

    # --- Strategy performance ---
    strategies = []
    try:
        query = (
            select(Strategy.name, StrategyPerformance)
            .join(Strategy, StrategyPerformance.strategy_id == Strategy.id)
            .where(StrategyPerformance.period == "30d")
            .order_by(StrategyPerformance.win_rate.desc())
        )
        result = await session.execute(query)
        for name, perf in result.all():
            strategies.append({
                "name": name,
                "win_rate": float(perf.win_rate),
                "profit_factor": float(perf.profit_factor),
                "avg_rr": float(perf.avg_rr),
                "total_signals": perf.total_signals,
                "is_degraded": perf.is_degraded,
            })
    except Exception:
        pass

    # --- Last candle fetch ---
    last_candle = None
    try:
        result = await session.execute(select(func.max(Candle.timestamp)))
        ts = result.scalar_one()
        if ts:
            last_candle = ts.isoformat()
    except Exception:
        pass

    # --- Backtest results (latest per strategy per window) ---
    backtests = []
    total_backtests = 0
    try:
        # Count total backtest results
        result = await session.execute(
            select(func.count()).select_from(BacktestResult)
        )
        total_backtests = result.scalar_one()

        # Get latest backtest result per strategy per window_days (non-walk-forward)
        from sqlalchemy import and_

        # Subquery: max created_at per (strategy_id, window_days)
        latest_sub = (
            select(
                BacktestResult.strategy_id,
                BacktestResult.window_days,
                func.max(BacktestResult.created_at).label("max_created"),
            )
            .where(
                BacktestResult.is_walk_forward.isnot(True),
            )
            .group_by(BacktestResult.strategy_id, BacktestResult.window_days)
            .subquery()
        )

        bt_query = (
            select(BacktestResult, Strategy.name)
            .join(Strategy, BacktestResult.strategy_id == Strategy.id)
            .join(
                latest_sub,
                and_(
                    BacktestResult.strategy_id == latest_sub.c.strategy_id,
                    BacktestResult.window_days == latest_sub.c.window_days,
                    BacktestResult.created_at == latest_sub.c.max_created,
                ),
            )
            .order_by(Strategy.name, BacktestResult.window_days)
        )
        result = await session.execute(bt_query)
        for bt, strat_name in result.all():
            backtests.append({
                "strategy": strat_name,
                "window_days": bt.window_days,
                "win_rate": float(bt.win_rate) if bt.win_rate is not None else None,
                "profit_factor": float(bt.profit_factor) if bt.profit_factor is not None else None,
                "sharpe_ratio": float(bt.sharpe_ratio) if bt.sharpe_ratio is not None else None,
                "max_drawdown": float(bt.max_drawdown) if bt.max_drawdown is not None else None,
                "expectancy": float(bt.expectancy) if bt.expectancy is not None else None,
                "total_trades": bt.total_trades,
                "is_walk_forward": bt.is_walk_forward or False,
                "is_overfitted": bt.is_overfitted,
                "created": bt.created_at.isoformat() if bt.created_at else None,
            })
    except Exception:
        pass

    # --- Walk-forward validation results (latest per strategy) ---
    walk_forward = []
    try:
        wf_latest_sub = (
            select(
                BacktestResult.strategy_id,
                func.max(BacktestResult.created_at).label("max_created"),
            )
            .where(BacktestResult.is_walk_forward.is_(True))
            .group_by(BacktestResult.strategy_id)
            .subquery()
        )
        wf_query = (
            select(BacktestResult, Strategy.name)
            .join(Strategy, BacktestResult.strategy_id == Strategy.id)
            .join(
                wf_latest_sub,
                and_(
                    BacktestResult.strategy_id == wf_latest_sub.c.strategy_id,
                    BacktestResult.created_at == wf_latest_sub.c.max_created,
                ),
            )
            .order_by(Strategy.name)
        )
        result = await session.execute(wf_query)
        for bt, strat_name in result.all():
            walk_forward.append({
                "strategy": strat_name,
                "win_rate": float(bt.win_rate) if bt.win_rate is not None else None,
                "profit_factor": float(bt.profit_factor) if bt.profit_factor is not None else None,
                "total_trades": bt.total_trades,
                "is_overfitted": bt.is_overfitted,
                "wfe": float(bt.walk_forward_efficiency) if bt.walk_forward_efficiency is not None else None,
                "created": bt.created_at.isoformat() if bt.created_at else None,
            })
    except Exception:
        pass

    # --- Optimized params (latest active per strategy) ---
    opt_params_list = []
    try:
        opt_latest_sub = (
            select(
                OptimizedParams.strategy_name,
                func.max(OptimizedParams.created_at).label("max_created"),
            )
            .where(OptimizedParams.is_active.is_(True))
            .group_by(OptimizedParams.strategy_name)
            .subquery()
        )
        opt_query = (
            select(OptimizedParams)
            .join(
                opt_latest_sub,
                and_(
                    OptimizedParams.strategy_name == opt_latest_sub.c.strategy_name,
                    OptimizedParams.created_at == opt_latest_sub.c.max_created,
                ),
            )
            .order_by(OptimizedParams.strategy_name)
        )
        result = await session.execute(opt_query)
        for opt in result.scalars().all():
            opt_params_list.append({
                "strategy": opt.strategy_name,
                "win_rate": float(opt.win_rate) if opt.win_rate is not None else None,
                "profit_factor": float(opt.profit_factor) if opt.profit_factor is not None else None,
                "total_trades": opt.total_trades,
                "wfe_ratio": float(opt.wfe_ratio) if opt.wfe_ratio is not None else None,
                "is_overfitted": opt.is_overfitted,
                "combinations_tested": opt.combinations_tested,
                "created": opt.created_at.isoformat() if opt.created_at else None,
            })
    except Exception:
        pass

    return {
        "system": {
            "status": "operational" if db_status == "connected" and scheduler_status == "running" else "degraded",
            "database": db_status,
            "scheduler": scheduler_status,
            "uptime_seconds": round(uptime, 1),
            "last_candle": last_candle,
            "timestamp": now.isoformat(),
        },
        "jobs": jobs,
        "signals": {
            "active": active_signals,
            "today": signals_today,
            "total": total_signals,
            "recent": recent_signals,
        },
        "performance": {
            "wins": wins,
            "losses": losses,
            "win_rate": round(wins / (wins + losses) * 100, 1) if (wins + losses) > 0 else 0,
            "total_pnl": round(total_pnl, 2),
        },
        "strategies": strategies,
        "backtests": {
            "total": total_backtests,
            "results": backtests,
            "walk_forward": walk_forward,
            "optimized_params": opt_params_list,
        },
    }
