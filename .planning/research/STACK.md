# STACK.md — Automated XAUUSD Trade Signal System

> **Research Date:** 2026-02-17
> **Research Type:** Greenfield Stack Selection
> **Confidence Disclaimer:** Versions verified against training data through May 2025. WebSearch/WebFetch were unavailable during this research session — verify all version numbers against PyPI/npm before locking `requirements.txt`.

---

## 1. Executive Summary

A production-grade, signals-only XAUUSD system running three rule-based strategies with rolling-window backtesting, dynamic strategy selection, and Telegram delivery. The stack is Python-centric (FastAPI + PostgreSQL + APScheduler), uses Twelve Data for market data, vectorbt for backtesting, pandas-ta for indicators, and python-telegram-bot for alerts. Deployed on Railway.

---

## 2. Stack Decisions

### 2.1 Market Data API

| Option | Verdict | Rationale |
|--------|---------|-----------|
| **Twelve Data** | **SELECTED** | Best XAUUSD coverage for the price. Free tier: 800 req/day, 8 req/min. Paid "Grow" plan (~$29/mo): 5000 req/day, WebSocket streaming, 30+ years historical forex/metals data. Native Python SDK (`twelvedata`). REST + WebSocket. Reliable uptime. Returns OHLCV with configurable intervals (1min to 1month). |
| Oanda v20 API | Runner-up | Excellent forex/gold data, but requires an Oanda brokerage account. Since we are signals-only (no execution), requiring a broker account adds unnecessary friction. If you later add execution, reconsider Oanda. |
| Polygon.io | Not recommended | Strong for US equities/options. Forex/metals coverage exists but is a paid add-on and less mature than Twelve Data. Overkill for a single-instrument system. |
| Alpha Vantage | Not recommended | Free tier is generous but rate-limited (25 req/day premium). Data quality for XAUUSD is inconsistent — known gaps in intraday gold data. No WebSocket support. Reliability issues reported in 2024-2025. |

**Selected:** `twelvedata` Python SDK
**Version:** `>=1.2.5` (latest stable as of early 2025)
**Confidence:** HIGH

```
pip install twelvedata[websocket]
```

**Key config notes:**
- Use WebSocket for live price streaming during market hours (Mon 00:00 - Fri 23:59 UTC, with gaps for weekend close)
- Use REST for historical OHLCV pulls (backtesting data refresh)
- Cache historical data in PostgreSQL to minimize API calls
- XAUUSD symbol: `XAU/USD` in Twelve Data's format

---

### 2.2 Backtesting Engine

| Option | Verdict | Rationale |
|--------|---------|-----------|
| **vectorbt** | **SELECTED** | Vectorized (NumPy-based) backtesting — 100-1000x faster than event-driven alternatives. Perfect for rolling-window strategy evaluation where you need to run hundreds of backtest windows quickly. Native support for parameter optimization, portfolio simulation, and custom indicators. Excellent pandas integration. |
| Backtrader | Not recommended | Event-driven architecture — too slow for rolling-window evaluation across multiple strategies. Development stalled (last meaningful update 2021). Large codebase, steep learning curve, overkill for signals-only system. |
| Backtesting.py | Runner-up | Simpler API, vectorized, but less feature-rich than vectorbt for multi-strategy comparison. No built-in rolling-window support. |
| Zipline/Zipline-Reloaded | Not recommended | Designed for equities with US market calendars. Poor forex/metals support. Heavy Blaze dependency. |

**Selected:** `vectorbt`
**Version:** `>=0.26.2` (community edition, latest stable)
**Confidence:** HIGH

```
pip install vectorbt
```

**Key usage notes:**
- Use `vbt.Portfolio.from_signals()` for strategy evaluation
- Rolling windows: slice your OHLCV DataFrame by date range, run backtest per window, compare Sharpe/win-rate/profit-factor across strategies
- vectorbt PRO exists (paid) with more features, but the open-source version is sufficient for 3 strategies
- **WARNING:** vectorbt depends on NumPy and Numba — pin NumPy to `<2.0` if using vectorbt 0.26.x to avoid compatibility issues

---

### 2.3 Technical Indicators

| Option | Verdict | Rationale |
|--------|---------|-----------|
| **pandas-ta** | **SELECTED** | Pure Python, no C compilation needed (critical for Railway deployment). 130+ indicators. Excellent pandas integration — call `df.ta.sma(length=20)` directly on DataFrames. Actively maintained. Handles all indicators needed for the 3 strategies (EMA, ATR, RSI, Bollinger Bands, volume profile, etc.). |
| TA-Lib (via `ta-lib` Python wrapper) | Not recommended for deployment | Requires C library compilation (`libta-lib`). Painful to install on Railway/Docker. Marginally faster for some indicators but the compilation overhead is not worth it for a system computing indicators on a single instrument. |
| `ta` (Technical Analysis Library) | Runner-up | Simpler API but fewer indicators and less flexible than pandas-ta. |

**Selected:** `pandas-ta`
**Version:** `>=0.3.14b1`
**Confidence:** HIGH

```
pip install pandas-ta
```

**Key usage notes:**
- For Liquidity Sweep strategy: use `df.ta.atr()`, `df.ta.rsi()`, swing high/low detection (custom logic on top of pandas-ta)
- For Trend Continuation: use `df.ta.ema()`, `df.ta.macd()`, `df.ta.adx()`
- For Breakout Expansion: use `df.ta.bbands()`, `df.ta.atr()`, volume analysis
- pandas-ta's `Strategy` class can batch-compute multiple indicators in one call

---

### 2.4 Web Framework (Backend API)

| Option | Verdict | Rationale |
|--------|---------|-----------|
| **FastAPI** | **SELECTED** | Async-native, high-performance, automatic OpenAPI docs, excellent for building the signal dashboard API and webhook endpoints. Type-safe with Pydantic. |

**Selected:** `fastapi` + `uvicorn`
**Version:** `fastapi>=0.115.0`, `uvicorn>=0.32.0`
**Confidence:** HIGH

```
pip install fastapi uvicorn[standard]
```

---

### 2.5 Task Scheduling

| Option | Verdict | Rationale |
|--------|---------|-----------|
| **APScheduler** | **SELECTED** | Lightweight, in-process scheduler. Perfect for Railway where you have a single long-running process. Supports cron-style scheduling (e.g., run strategy evaluation every 4 hours during market hours). Built-in job persistence via SQLAlchemy (backs up to your PostgreSQL). No external broker needed. |
| Celery + Redis | Not recommended | Requires a separate Redis instance (additional Railway service cost + complexity). Overkill for 2-3 scheduled tasks. Celery's complexity is justified for distributed workloads — this system has none. |
| FastAPI BackgroundTasks | Not recommended | Only for fire-and-forget tasks within a request lifecycle. Not suitable for recurring scheduled jobs. No persistence. |
| `rocketry` | Runner-up | Modern Python scheduler but smaller community. APScheduler is more battle-tested. |

**Selected:** `APScheduler`
**Version:** `>=3.10.4` (v3.x — do NOT use v4.x alpha, it's a full rewrite and unstable)
**Confidence:** HIGH

```
pip install APScheduler
```

**Key usage notes:**
- Use `AsyncIOScheduler` (not `BackgroundScheduler`) for FastAPI compatibility
- Integrate with SQLAlchemy job store for persistence across Railway restarts
- Schedule: data refresh every 15min, strategy evaluation every 4h, signal check hourly during high-volume sessions (London/NY overlap)
- Use `misfire_grace_time` to handle Railway cold starts gracefully

---

### 2.6 Database & ORM

| Option | Verdict | Rationale |
|--------|---------|-----------|
| **SQLAlchemy 2.x** | **SELECTED** | Industry standard Python ORM. v2.0+ has native async support via `asyncpg`. Type-safe query building. Excellent Alembic migration tool. Massive ecosystem. |
| Tortoise ORM | Not recommended | Async-first is nice, but smaller community, fewer resources, less mature migration tooling. SQLAlchemy 2.x now has full async support, removing Tortoise's main advantage. |
| SQLModel | Runner-up | Built on SQLAlchemy + Pydantic (by FastAPI creator). Elegant but still maturing. Use SQLAlchemy directly for production — SQLModel can be added as a convenience layer later. |

**Selected:** `SQLAlchemy` + `asyncpg` + `Alembic`
**Version:** `SQLAlchemy>=2.0.36`, `asyncpg>=0.30.0`, `alembic>=1.14.0`
**Confidence:** HIGH

```
pip install sqlalchemy[asyncio] asyncpg alembic
```

**Database: PostgreSQL on Railway**
- Railway provisions PostgreSQL with a connection string — use it directly
- Connection pooling: use SQLAlchemy's built-in async pool (`pool_size=5, max_overflow=10`)
- Store: OHLCV cache, backtest results, signal history, TP/SL tracking outcomes

**Key tables:**
- `ohlcv_data` — cached market data (timestamp, open, high, low, close, volume, interval)
- `backtest_results` — per-strategy performance metrics per rolling window
- `signals` — generated signals (strategy, direction, entry, TP, SL, timestamp, status)
- `signal_outcomes` — tracked results (signal_id, hit_tp, hit_sl, actual_pips, duration)

---

### 2.7 Telegram Bot

| Option | Verdict | Rationale |
|--------|---------|-----------|
| **python-telegram-bot** | **SELECTED** | Most mature Python Telegram library. v20.x is fully async (native `asyncio`). 10k+ GitHub stars, excellent docs, active development. Built-in conversation handlers, inline keyboards, message formatting. |
| aiogram | Runner-up | Also async-native, slightly more "Pythonic" in some ways. But smaller English-language community (originally Russian-focused). python-telegram-bot has broader ecosystem support and more tutorials. |
| Raw HTTP (aiohttp + Telegram Bot API) | Not recommended | Reinventing the wheel. Both libraries above handle rate limiting, retries, and API changes. |

**Selected:** `python-telegram-bot`
**Version:** `>=21.9`
**Confidence:** HIGH

```
pip install python-telegram-bot
```

**Key usage notes:**
- Use `Application.builder().token(TOKEN).build()` pattern
- For signals-only (no user interaction needed beyond subscribing), use `bot.send_message()` directly — no need for full conversation handler setup
- Format signals with MarkdownV2 for clean presentation
- Include inline keyboard buttons for "View on TradingView" deep links
- Rate limit: Telegram allows ~30 messages/sec to different chats, 1 msg/sec to same chat

**Signal message format:**
```
XAUUSD LONG Signal
Strategy: Liquidity Sweep Reversal
Entry: 2,645.50
TP1: 2,665.50 (+200 pips)
TP2: 2,685.50 (+400 pips)
SL: 2,635.50 (-100 pips)
R:R: 1:4
Confidence: HIGH
```

---

### 2.8 TradingView Integration

| Component | Selection | Rationale |
|-----------|-----------|-----------|
| **Charting (Frontend)** | `lightweight-charts` v4.x | TradingView's official open-source charting library. Free, no API key needed. Renders professional candlestick charts in the browser. Can overlay signal markers (entry/TP/SL lines). |
| **Webhook Receiver** | FastAPI endpoint | TradingView alerts can POST to a webhook URL. Use this as an optional secondary signal trigger (Pine Script alerts -> FastAPI -> Telegram). |
| **Pine Script** | Optional | Can write Pine Script indicators that mirror your Python strategies for visual confirmation on TradingView charts. Not required for MVP. |

**Selected:** `lightweight-charts` (npm/CDN for frontend)
**Version:** `>=4.2.0`
**Confidence:** MEDIUM (frontend component — may not be needed for MVP if signals are Telegram-only)

```html
<script src="https://unpkg.com/lightweight-charts/dist/lightweight-charts.standalone.production.js"></script>
```

**Key usage notes:**
- Use for a web dashboard (optional, post-MVP)
- Embed in a simple HTML page served by FastAPI's static files
- Plot OHLCV data + signal markers (entry, TP, SL as horizontal lines)
- Consider `tradingview-widget` embeds as a simpler alternative for read-only charting

---

### 2.9 Additional Libraries

| Library | Purpose | Version | Confidence |
|---------|---------|---------|------------|
| `pydantic` | Data validation, settings management | `>=2.10.0` | HIGH — ships with FastAPI |
| `pydantic-settings` | Environment variable management | `>=2.7.0` | HIGH |
| `httpx` | Async HTTP client (for API calls) | `>=0.28.0` | HIGH |
| `numpy` | Numerical computation (vectorbt dep) | `>=1.26.0, <2.0` | HIGH — pin below 2.0 for vectorbt compat |
| `pandas` | DataFrames for OHLCV data | `>=2.2.0` | HIGH |
| `loguru` | Structured logging | `>=0.7.3` | HIGH — much better than stdlib logging |
| `python-dotenv` | .env file loading | `>=1.0.1` | HIGH |
| `tenacity` | Retry logic for API calls | `>=9.0.0` | HIGH |
| `pytest` + `pytest-asyncio` | Testing | `>=8.3.0` / `>=0.24.0` | HIGH |

---

## 3. Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│                    Railway Deployment                         │
│                                                              │
│  ┌──────────────────────────────────────────────────────┐   │
│  │              FastAPI Application                      │   │
│  │                                                       │   │
│  │  ┌─────────────┐  ┌──────────────┐  ┌────────────┐  │   │
│  │  │ APScheduler │  │ Signal Engine│  │ REST API   │  │   │
│  │  │             │  │              │  │            │  │   │
│  │  │ - Data pull │  │ - 3 Strats   │  │ - Signals  │  │   │
│  │  │ - Backtest  │  │ - Backtest   │  │ - History  │  │   │
│  │  │ - Evaluate  │  │ - Select     │  │ - Health   │  │   │
│  │  └──────┬──────┘  └──────┬───────┘  └────────────┘  │   │
│  │         │                │                            │   │
│  │         ▼                ▼                            │   │
│  │  ┌──────────────────────────────────────────────┐    │   │
│  │  │           SQLAlchemy + asyncpg                │    │   │
│  │  └──────────────────┬───────────────────────────┘    │   │
│  └─────────────────────┼────────────────────────────────┘   │
│                        │                                     │
│                        ▼                                     │
│  ┌─────────────────────────────┐                            │
│  │   PostgreSQL (Railway)      │                            │
│  └─────────────────────────────┘                            │
│                                                              │
└──────────────────────────────────────────────────────────────┘
         │                              │
         ▼                              ▼
┌─────────────────┐          ┌─────────────────────┐
│  Twelve Data    │          │  Telegram Bot API   │
│  (Market Data)  │          │  (Signal Delivery)  │
└─────────────────┘          └─────────────────────┘
```

---

## 4. What NOT to Use (and Why)

| Technology | Why NOT |
|------------|---------|
| **Celery + Redis** | Adds a Redis service on Railway ($5+/mo), operational complexity for message broker, worker processes. You have 2-3 scheduled tasks — APScheduler handles this in-process. |
| **Backtrader** | Event-driven backtesting is 100-1000x slower than vectorbt for rolling windows. Stale development (2021). |
| **TA-Lib** | Requires C library compilation. Breaks on Railway/Docker without custom Dockerfiles. pandas-ta gives you the same indicators in pure Python. |
| **Alpha Vantage** | Unreliable XAUUSD data, harsh rate limits, no WebSocket. |
| **Django** | Too heavy for an API-first signals system. FastAPI is faster, lighter, async-native. |
| **MongoDB** | Structured relational data (signals, outcomes, backtests) fits PostgreSQL perfectly. No need for document store. |
| **Tortoise ORM** | SQLAlchemy 2.x has async support now. Tortoise has weaker migration tooling and smaller community. |
| **APScheduler v4.x** | Major rewrite, still in alpha/beta. Stick with v3.10.x for stability. |
| **Zipline** | Equity-focused, US market calendars baked in, poor forex/metals support. |
| **ccxt** | Designed for crypto exchanges. Does not cover forex/metals brokers or data providers. |

---

## 5. Railway Deployment Specifics

### 5.1 Service Configuration

```toml
# railway.toml
[build]
builder = "nixpacks"

[deploy]
startCommand = "uvicorn app.main:app --host 0.0.0.0 --port $PORT"
healthcheckPath = "/health"
healthcheckTimeout = 300
restartPolicyType = "on_failure"
restartPolicyMaxRetries = 5
```

### 5.2 Key Considerations

- **Long-running process:** Railway supports long-running services (not just serverless). Your FastAPI + APScheduler process runs continuously.
- **Cold starts:** Railway may restart your service during deploys or scaling. Use APScheduler job persistence (SQLAlchemy job store) to survive restarts.
- **Environment variables:** Store `TWELVE_DATA_API_KEY`, `TELEGRAM_BOT_TOKEN`, `DATABASE_URL` in Railway's environment variable UI.
- **PostgreSQL:** Railway provides managed PostgreSQL with automatic backups. Use the `DATABASE_URL` connection string directly.
- **Cost estimate:** Hobby plan ($5/mo) covers the Python service. PostgreSQL plugin is free up to 1GB. Total: ~$5-10/mo for infra + $29/mo for Twelve Data Grow plan = ~$35-40/mo all-in.
- **Logging:** Railway captures stdout/stderr. Use `loguru` with `sys.stderr` sink for structured logs visible in Railway dashboard.
- **No cron jobs:** Railway has a cron service, but it's for short tasks. Use APScheduler in your long-running process instead.

---

## 6. Version Lock — `requirements.txt`

```
# Core Framework
fastapi>=0.115.0
uvicorn[standard]>=0.32.0
pydantic>=2.10.0
pydantic-settings>=2.7.0

# Market Data
twelvedata[websocket]>=1.2.5

# Backtesting & Indicators
vectorbt>=0.26.2
pandas-ta>=0.3.14b1
pandas>=2.2.0
numpy>=1.26.0,<2.0

# Database
sqlalchemy[asyncio]>=2.0.36
asyncpg>=0.30.0
alembic>=1.14.0

# Telegram
python-telegram-bot>=21.9

# Scheduling
APScheduler>=3.10.4,<4.0

# Utilities
httpx>=0.28.0
loguru>=0.7.3
python-dotenv>=1.0.1
tenacity>=9.0.0

# Testing
pytest>=8.3.0
pytest-asyncio>=0.24.0
```

---

## 7. Confidence Summary

| Component | Choice | Confidence | Risk |
|-----------|--------|------------|------|
| Market Data | Twelve Data | HIGH | Low — well-established, good XAUUSD coverage |
| Backtesting | vectorbt | HIGH | Low — vectorized approach is ideal for rolling windows |
| Indicators | pandas-ta | HIGH | Low — pure Python, no compilation issues |
| Web Framework | FastAPI | HIGH | Negligible — industry standard |
| Scheduling | APScheduler 3.x | HIGH | Low — mature, in-process, persistent jobs |
| Database/ORM | SQLAlchemy 2.x + asyncpg | HIGH | Negligible — industry standard |
| Telegram | python-telegram-bot | HIGH | Low — most mature option |
| TradingView | lightweight-charts | MEDIUM | Medium — frontend component, defer to post-MVP |
| Deployment | Railway | HIGH | Low — supports long-running Python services well |

---

## 8. Open Questions for Roadmap

1. **Twelve Data plan tier:** Free tier (800 req/day) may suffice for MVP with aggressive caching. Evaluate if Grow plan is needed based on actual request volume during development.
2. **WebSocket vs polling:** Start with REST polling (every 15min) for simplicity. Add WebSocket streaming later if real-time price action is needed for Liquidity Sweep detection.
3. **TradingView dashboard:** Defer frontend charting to post-MVP. Telegram-only signals are the priority.
4. **Multi-timeframe data:** Strategies may need 15min, 1H, and 4H data simultaneously. Design the OHLCV cache schema to handle multiple intervals from day one.
5. **Signal deduplication:** When rolling-window evaluation shifts, the same signal condition may trigger twice. Need dedup logic keyed on (strategy + direction + price_zone + timestamp_window).

---

*Research generated for the XAUUSD Trade Signal System project. Verify all version numbers against PyPI before locking dependencies.*
