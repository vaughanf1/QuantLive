# Architecture Research: Automated XAUUSD Trade Signal System

> **Research dimension:** Architecture
> **Date:** 2026-02-17
> **Status:** Complete
> **Scope:** Production-grade automated trade signal system for XAUUSD (Gold)
> **Stack:** Python / FastAPI / PostgreSQL (Railway) / Telegram / TradingView

---

## 1. System Overview

The system is a **signal-generation platform** — it does not execute trades. It ingests market data, runs strategy logic, selects the best-performing strategy, emits trade signals, delivers them to the user, tracks outcomes, and feeds performance data back to improve future selection. This is a closed-loop signal intelligence system.

### High-Level Architecture Diagram (Text)

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                          EXTERNAL DATA SOURCES                              │
│   ┌──────────────┐   ┌──────────────────┐   ┌────────────────────────┐     │
│   │  Broker API  │   │  TradingView     │   │  Alternative Data     │     │
│   │  (OANDA/etc) │   │  (Webhooks/Feed) │   │  (COT, Sentiment)     │     │
│   └──────┬───────┘   └────────┬─────────┘   └───────────┬────────────┘     │
└──────────┼────────────────────┼──────────────────────────┼──────────────────┘
           │                    │                          │
           ▼                    ▼                          ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                        1. DATA INGESTION LAYER                              │
│   ┌────────────────────────────────────────────────────────────────────┐    │
│   │  CandleIngestor  ─── normalizes, validates, stores multi-TF data  │    │
│   │  Timeframes: M1, M5, M15, H1, H4, D1                             │    │
│   └────────────────────────────────┬───────────────────────────────────┘    │
└────────────────────────────────────┼────────────────────────────────────────┘
                                     │
                                     ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                          2. POSTGRESQL DATABASE                             │
│   ┌──────────┐ ┌──────────┐ ┌───────────┐ ┌─────────┐ ┌───────────────┐   │
│   │ candles   │ │ signals  │ │ outcomes  │ │ backtest│ │ strategy_perf │   │
│   │          │ │          │ │           │ │ results │ │               │   │
│   └──────────┘ └──────────┘ └───────────┘ └─────────┘ └───────────────┘   │
└────────────────────────────────────┬────────────────────────────────────────┘
                                     │
           ┌─────────────────────────┼─────────────────────────┐
           │                         │                         │
           ▼                         ▼                         ▼
┌────────────────────┐  ┌──────────────────────┐  ┌────────────────────────┐
│ 3. STRATEGY ENGINE │  │ 4. BACKTESTING ENGINE│  │ 9. FEEDBACK LOOP       │
│                    │  │                      │  │                        │
│ - LiquiditySweep   │  │ - Rolling window     │  │ - Performance decay    │
│ - TrendContinuatn  │  │ - Win rate, PF, etc  │  │   detection            │
│ - BreakoutExpansn  │  │ - Walk-forward       │  │ - Stat recalculation   │
│                    │  │                      │  │ - Strategy re-ranking  │
└────────┬───────────┘  └──────────┬───────────┘  └────────────┬───────────┘
         │                         │                           │
         │              ┌──────────▼───────────┐               │
         └──────────────▶ 5. STRATEGY SELECTOR ◀───────────────┘
                        │                      │
                        │ Picks best strategy  │
                        │ based on backtest    │
                        │ metrics + regime     │
                        └──────────┬───────────┘
                                   │
                                   ▼
                        ┌──────────────────────┐
                        │ 6. SIGNAL GENERATOR  │
                        │                      │
                        │ Entry, SL, TP1, TP2  │
                        │ R:R, Confidence       │
                        │ Reasoning text        │
                        └──────────┬───────────┘
                                   │
                    ┌──────────────┼──────────────┐
                    │              │              │
                    ▼              ▼              ▼
         ┌──────────────┐ ┌──────────────┐ ┌──────────────────┐
         │ 7. TELEGRAM  │ │ 8. TRADING   │ │ 10. OUTCOME      │
         │   NOTIFIER   │ │    VIEW UI   │ │    TRACKER        │
         │              │ │              │ │                    │
         │ Formatted    │ │ Charts +     │ │ Monitors price    │
         │ alerts       │ │ overlays     │ │ vs SL/TP levels   │
         └──────────────┘ └──────────────┘ └────────┬──────────┘
                                                     │
                                                     │ Outcome recorded
                                                     ▼
                                              (back to Database →
                                               Feedback Loop)
```

---

## 2. Component Definitions and Boundaries

### Component 1: Data Ingestion Layer

**Responsibility:** Fetch, normalize, validate, and store XAUUSD candle data across multiple timeframes.

**Boundaries:**
- **Owns:** Raw data fetching, data normalization, gap detection, timeframe alignment
- **Does NOT own:** Strategy logic, signal generation, or any downstream decision-making
- **Inputs:** External API responses (REST or WebSocket)
- **Outputs:** Clean, validated candle records written to PostgreSQL `candles` table

**Key Design Decisions:**

| Question | Recommendation | Rationale |
|----------|---------------|-----------|
| Poll vs Event-driven? | **Hybrid.** WebSocket for M1/M5 real-time feed; scheduled poll (APScheduler) for H1/H4/D1 on candle close | WebSocket gives low-latency for intraday strategies; higher timeframes only need data on candle close. Avoids unnecessary polling overhead. |
| How often to poll? | On candle close for each timeframe (every 1m, 5m, 15m, 1h, 4h, daily) | Strategies act on completed candles, not partial. Polling mid-candle wastes resources and can produce false signals. |
| Data source? | Primary: broker API (OANDA v20, or similar). Fallback: TradingView webhook or free API (e.g., Twelve Data) | Broker API is authoritative for price. Free APIs may have lag or gaps. |
| Multi-TF sync? | Align all timeframes to the same clock. Higher TF candles derived from lower TF data OR fetched independently with timestamp alignment | Prevents drift between timeframes that could cause conflicting signals. |

**Internal Structure:**
```
data_ingestion/
├── __init__.py
├── ingestor.py          # Main orchestrator
├── sources/
│   ├── __init__.py
│   ├── base.py          # Abstract DataSource
│   ├── oanda.py         # OANDA v20 implementation
│   └── tradingview.py   # TradingView webhook receiver
├── normalizer.py        # OHLCV normalization, validation
├── gap_detector.py      # Detect missing candles, fill or flag
└── scheduler.py         # APScheduler jobs per timeframe
```

---

### Component 2: PostgreSQL Database

**Responsibility:** Persistent storage for all system state — candles, signals, outcomes, backtest results, strategy performance metrics.

**Boundaries:**
- **Owns:** Schema, migrations, indices, query optimization
- **Does NOT own:** Business logic (that lives in application layer)
- **Accessed by:** All other components via SQLAlchemy ORM / async sessions

**Schema Design:**

```sql
-- Core market data
CREATE TABLE candles (
    id              BIGSERIAL PRIMARY KEY,
    symbol          VARCHAR(10) NOT NULL DEFAULT 'XAUUSD',
    timeframe       VARCHAR(5) NOT NULL,          -- M1, M5, M15, H1, H4, D1
    timestamp       TIMESTAMPTZ NOT NULL,
    open            DECIMAL(10,5) NOT NULL,
    high            DECIMAL(10,5) NOT NULL,
    low             DECIMAL(10,5) NOT NULL,
    close           DECIMAL(10,5) NOT NULL,
    volume          DECIMAL(15,2),
    UNIQUE(symbol, timeframe, timestamp)
);
CREATE INDEX idx_candles_lookup ON candles(symbol, timeframe, timestamp DESC);

-- Strategy definitions
CREATE TABLE strategies (
    id              SERIAL PRIMARY KEY,
    name            VARCHAR(100) NOT NULL UNIQUE,  -- e.g., 'liquidity_sweep_reversal'
    version         VARCHAR(20) NOT NULL,
    description     TEXT,
    parameters      JSONB NOT NULL DEFAULT '{}',   -- strategy config params
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Backtest results (rolling window)
CREATE TABLE backtest_results (
    id              SERIAL PRIMARY KEY,
    strategy_id     INTEGER NOT NULL REFERENCES strategies(id),
    window_start    TIMESTAMPTZ NOT NULL,
    window_end      TIMESTAMPTZ NOT NULL,
    total_trades    INTEGER NOT NULL,
    win_rate        DECIMAL(5,4),                  -- 0.0000 to 1.0000
    profit_factor   DECIMAL(8,4),
    sharpe_ratio    DECIMAL(8,4),
    max_drawdown    DECIMAL(8,4),
    expectancy      DECIMAL(10,2),                 -- in pips or dollars
    avg_rr          DECIMAL(6,4),                  -- average R:R achieved
    calculated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    metadata        JSONB DEFAULT '{}'
);
CREATE INDEX idx_backtest_strategy ON backtest_results(strategy_id, calculated_at DESC);

-- Trade signals
CREATE TABLE signals (
    id              SERIAL PRIMARY KEY,
    strategy_id     INTEGER NOT NULL REFERENCES strategies(id),
    symbol          VARCHAR(10) NOT NULL DEFAULT 'XAUUSD',
    direction       VARCHAR(5) NOT NULL,           -- 'LONG' or 'SHORT'
    entry_price     DECIMAL(10,5) NOT NULL,
    stop_loss       DECIMAL(10,5) NOT NULL,
    take_profit_1   DECIMAL(10,5) NOT NULL,
    take_profit_2   DECIMAL(10,5),
    risk_reward     DECIMAL(6,4) NOT NULL,
    confidence      DECIMAL(5,4) NOT NULL,         -- 0.0 to 1.0
    reasoning       TEXT NOT NULL,
    timeframe       VARCHAR(5) NOT NULL,
    status          VARCHAR(20) NOT NULL DEFAULT 'ACTIVE',  -- ACTIVE, TP1_HIT, TP2_HIT, SL_HIT, EXPIRED, CANCELLED
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at      TIMESTAMPTZ,
    metadata        JSONB DEFAULT '{}'
);
CREATE INDEX idx_signals_active ON signals(status, created_at DESC);

-- Outcome tracking
CREATE TABLE outcomes (
    id              SERIAL PRIMARY KEY,
    signal_id       INTEGER NOT NULL REFERENCES signals(id) UNIQUE,
    result          VARCHAR(20) NOT NULL,           -- TP1_HIT, TP2_HIT, SL_HIT, EXPIRED, BREAKEVEN
    exit_price      DECIMAL(10,5),
    pnl_pips        DECIMAL(10,2),
    pnl_rr          DECIMAL(6,4),                  -- in R multiples
    duration_mins   INTEGER,                        -- how long signal was active
    tp1_hit_at      TIMESTAMPTZ,
    tp2_hit_at      TIMESTAMPTZ,
    sl_hit_at       TIMESTAMPTZ,
    resolved_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    metadata        JSONB DEFAULT '{}'
);
CREATE INDEX idx_outcomes_result ON outcomes(result, resolved_at DESC);

-- Strategy performance (aggregated, updated by feedback loop)
CREATE TABLE strategy_performance (
    id              SERIAL PRIMARY KEY,
    strategy_id     INTEGER NOT NULL REFERENCES strategies(id),
    period          VARCHAR(10) NOT NULL,           -- 'live_7d', 'live_30d', 'backtest_30d', 'backtest_60d'
    total_signals   INTEGER NOT NULL DEFAULT 0,
    win_rate        DECIMAL(5,4),
    profit_factor   DECIMAL(8,4),
    sharpe_ratio    DECIMAL(8,4),
    max_drawdown    DECIMAL(8,4),
    expectancy      DECIMAL(10,2),
    is_degrading    BOOLEAN NOT NULL DEFAULT FALSE,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(strategy_id, period)
);
```

**Key Schema Decisions:**
- JSONB `parameters` and `metadata` columns provide extensibility without schema migrations for every strategy tweak
- `candles` table uses a composite unique index on (symbol, timeframe, timestamp) for upsert safety
- `strategy_performance` table separates live vs backtest periods so the selector can weight them differently
- Signal `status` is mutable — updated by the outcome tracker as prices move

---

### Component 3: Strategy Engine

**Responsibility:** Implement the three rule-based trading strategies. Each strategy receives candle data and outputs a candidate signal (or no signal).

**Boundaries:**
- **Owns:** Strategy logic, entry/exit rules, indicator calculations
- **Does NOT own:** Data fetching, signal delivery, outcome tracking
- **Inputs:** Multi-timeframe candle DataFrames from the database
- **Outputs:** `CandidateSignal` objects (or `None` if no setup detected)

**Strategy Class Design (Extensibility Pattern):**

```python
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional
import pandas as pd

@dataclass
class CandidateSignal:
    direction: str          # LONG or SHORT
    entry_price: float
    stop_loss: float
    take_profit_1: float
    take_profit_2: Optional[float]
    risk_reward: float
    confidence: float       # 0.0 to 1.0
    reasoning: str
    timeframe: str
    strategy_name: str
    metadata: dict = None

class BaseStrategy(ABC):
    """All strategies inherit from this base class."""

    name: str
    version: str
    required_timeframes: list[str]  # e.g., ['M15', 'H1', 'H4']
    min_candles: int                # minimum history needed

    @abstractmethod
    def analyze(self, candles: dict[str, pd.DataFrame]) -> Optional[CandidateSignal]:
        """
        Receives a dict of {timeframe: DataFrame} with OHLCV data.
        Returns a CandidateSignal if a valid setup is detected, else None.
        """
        pass

    @abstractmethod
    def get_parameters(self) -> dict:
        """Return current strategy parameters for storage/audit."""
        pass

class LiquiditySweepReversal(BaseStrategy):
    name = "liquidity_sweep_reversal"
    version = "1.0.0"
    required_timeframes = ["M15", "H1", "H4"]
    min_candles = 100
    # ... implements analyze() looking for sweep of highs/lows + reversal candle

class TrendContinuation(BaseStrategy):
    name = "trend_continuation"
    version = "1.0.0"
    required_timeframes = ["M15", "H1", "H4", "D1"]
    min_candles = 200
    # ... implements analyze() looking for pullback to structure in established trend

class BreakoutExpansion(BaseStrategy):
    name = "breakout_expansion"
    version = "1.0.0"
    required_timeframes = ["M15", "H1"]
    min_candles = 50
    # ... implements analyze() looking for range breakout + volume expansion
```

**Why this pattern:**
- **Abstract base class** enforces a contract — every strategy must implement `analyze()` and `get_parameters()`
- **`required_timeframes`** lets the scanning loop know what data to fetch per strategy
- **`CandidateSignal` dataclass** standardizes output so the selector and signal generator are decoupled from strategy internals
- **Adding a new strategy** = create a new class inheriting `BaseStrategy`, register it in a strategy registry — zero changes to downstream components

**File Structure:**
```
strategies/
├── __init__.py
├── base.py                      # BaseStrategy + CandidateSignal
├── registry.py                  # Strategy registry (discover + instantiate)
├── liquidity_sweep_reversal.py
├── trend_continuation.py
├── breakout_expansion.py
└── indicators/
    ├── __init__.py
    ├── structure.py             # Support/resistance, swing highs/lows
    ├── momentum.py              # RSI, MACD, etc.
    ├── volume.py                # Volume profile, OBV
    └── atr.py                   # ATR for SL/TP calculation
```

---

### Component 4: Backtesting Engine

**Responsibility:** Run each strategy against historical candle data over a rolling window (30-60 days). Calculate performance metrics. Provide walk-forward validation.

**Boundaries:**
- **Owns:** Historical simulation, metric calculation, walk-forward logic
- **Does NOT own:** Live signal generation (but shares the same strategy classes)
- **Inputs:** Strategy instances + historical candle data from DB
- **Outputs:** `BacktestResult` records written to `backtest_results` table

**Key Design Decisions:**

| Question | Recommendation | Rationale |
|----------|---------------|-----------|
| How to integrate with live execution? | **Same strategy classes.** Backtester calls `strategy.analyze()` on historical data windows, exactly as the live scanner does. | Eliminates divergence between backtest and live behavior — the single biggest source of bugs in trading systems. |
| Rolling window? | 30-day primary window, 60-day secondary. Re-run backtests daily (or on each new candle close for the relevant timeframe) | Keeps metrics current. Avoids over-fitting to distant past data. |
| Walk-forward? | Yes. Train on 80% of window, test on 20%. Slide forward daily. | Detects overfitting. If in-sample performance far exceeds out-of-sample, strategy is suspect. |
| Execution model? | Run as a **background task** (APScheduler or Celery), not in the request path | Backtests are computationally expensive. Must not block API responses. |

**Metrics Calculated:**
- **Win Rate** — % of signals that hit TP1 or TP2
- **Profit Factor** — gross profit / gross loss
- **Sharpe Ratio** — risk-adjusted return (annualized)
- **Max Drawdown** — worst peak-to-trough in equity curve
- **Expectancy** — (win% x avg win) - (loss% x avg loss), in pips
- **Average R:R Achieved** — actual reward vs risk

---

### Component 5: Strategy Selector

**Responsibility:** Algorithmically choose the best-performing strategy for the current market conditions based on backtest metrics and live performance.

**Boundaries:**
- **Owns:** Ranking logic, weighting of metrics, regime detection
- **Does NOT own:** Running strategies or backtests
- **Inputs:** `backtest_results` + `strategy_performance` from DB
- **Outputs:** Selected strategy ID + confidence score

**Selection Algorithm (Recommended):**

```
Score = (0.30 × win_rate_normalized) +
        (0.25 × profit_factor_normalized) +
        (0.20 × sharpe_normalized) +
        (0.15 × expectancy_normalized) +
        (0.10 × (1 - max_drawdown_normalized))

# Penalties:
- If is_degrading == True: score *= 0.5
- If total_trades < 10: score *= 0.3  (insufficient sample)
- If live win_rate < backtest win_rate * 0.7: score *= 0.6  (live underperformance)
```

**Regime Awareness (Future Enhancement):**
- Low volatility (ATR < threshold): favor TrendContinuation
- High volatility: favor BreakoutExpansion
- Range-bound: favor LiquiditySweepReversal

---

### Component 6: Signal Generator

**Responsibility:** When a strategy produces a `CandidateSignal` and it passes through the selector, the signal generator enriches it, persists it, and triggers downstream notifications.

**Boundaries:**
- **Owns:** Signal enrichment (R:R validation, confidence calibration, reasoning generation), signal persistence, deduplication
- **Does NOT own:** Strategy logic, delivery channels
- **Inputs:** `CandidateSignal` from selected strategy
- **Outputs:** Persisted `Signal` record + event dispatch to Telegram + TradingView

**Key Rules:**
- **Deduplication:** Do not emit a new signal if an active signal exists for the same direction within a configurable window (e.g., 4 hours)
- **Minimum R:R filter:** Reject signals with R:R < 1.5 (configurable)
- **Confidence threshold:** Reject signals with confidence < 0.6 (configurable)
- **Expiry:** Each signal gets an `expires_at` timestamp (e.g., +8 hours for intraday, +48 hours for swing)

---

### Component 7: Telegram Notifier

**Responsibility:** Format and deliver signal alerts to a Telegram channel/group.

**Boundaries:**
- **Owns:** Message formatting, Telegram Bot API interaction, delivery confirmation
- **Does NOT own:** Signal logic, outcome tracking
- **Inputs:** Signal record from signal generator
- **Outputs:** Formatted Telegram message

**Message Format:**
```
🟢 LONG XAUUSD @ 2,345.50

Strategy: Liquidity Sweep Reversal
Timeframe: H1
Confidence: 82%

📍 Entry: 2,345.50
🛑 Stop Loss: 2,338.00 (-75 pips)
🎯 TP1: 2,360.00 (+145 pips)
🎯 TP2: 2,375.00 (+295 pips)
📊 R:R: 1:1.93 / 1:3.93

📝 Reasoning: H4 liquidity sweep below 2,340 support
followed by bullish engulfing on H1. RSI divergence
confirms reversal. H4 trend remains bullish.

⏰ Valid until: 2026-02-18 02:00 UTC

Win Rate (30d): 67% | Profit Factor: 1.85
```

**Implementation:** Use `python-telegram-bot` library with async support. Fire-and-forget with retry (3 attempts, exponential backoff). Log delivery status.

---

### Component 8: TradingView Integration

**Responsibility:** Provide visual chart interface with signal overlays for review and analysis.

**Boundaries:**
- **Owns:** Chart rendering, signal overlay visualization, widget configuration
- **Does NOT own:** Signal logic, data storage
- **Inputs:** Signal records + candle data from DB
- **Outputs:** Web UI with interactive charts

**Architecture Decision: Client-Side TradingView Lightweight Charts**

| Option | Recommendation |
|--------|---------------|
| TradingView Embed Widget | Not suitable — limited customization, no programmatic overlay control |
| TradingView Advanced Charts (licensed) | Expensive, complex — overkill for signal visualization |
| **TradingView Lightweight Charts (open source)** | **Recommended.** Free, fast, full API for drawing overlays (markers, lines, shapes) |
| Server-side rendering (e.g., mplfinance) | Poor UX — no interactivity, slow |

**Implementation approach:**
- FastAPI serves a simple HTML/JS page
- Page uses TradingView Lightweight Charts library (CDN or bundled)
- JS fetches candle data + signals via FastAPI REST endpoints
- Signals rendered as chart markers (entry) + horizontal lines (SL, TP1, TP2)
- Color-coded by outcome (green = TP hit, red = SL hit, gray = active/pending)

**File Structure:**
```
frontend/
├── templates/
│   └── chart.html          # Jinja2 template with Lightweight Charts
├── static/
│   ├── js/
│   │   ├── chart.js        # Chart initialization + signal overlay logic
│   │   └── api.js          # Fetch candles and signals from backend
│   └── css/
│       └── style.css
```

---

### Component 9: Feedback Loop

**Responsibility:** Detect strategy performance degradation, recalculate rolling statistics, and trigger re-ranking of strategies.

**Boundaries:**
- **Owns:** Degradation detection, stat recalculation, alerting on performance issues
- **Does NOT own:** Strategy logic or outcome tracking
- **Inputs:** New outcome records from outcome tracker
- **Outputs:** Updated `strategy_performance` records, `is_degrading` flags

**Degradation Detection Rules:**
- Win rate drops >15% vs 30-day rolling average
- Profit factor drops below 1.0 (i.e., losing money)
- 3+ consecutive losing signals
- Max drawdown exceeds historical 95th percentile

**Actions on Degradation:**
1. Flag strategy as `is_degrading = True`
2. Strategy selector automatically penalizes (score * 0.5)
3. Send Telegram alert: "Strategy X showing degradation — auto-deprioritized"
4. After 7 days, if metrics recover, clear the flag automatically

---

### Component 10: Outcome Tracker

**Responsibility:** Monitor live price against active signal levels (SL, TP1, TP2, expiry) and record outcomes.

**Boundaries:**
- **Owns:** Price monitoring, outcome detection, signal status updates
- **Does NOT own:** Signal generation, strategy logic
- **Inputs:** Active signals from DB + live price data
- **Outputs:** Outcome records, updated signal statuses

**Design Decision: Polling vs WebSocket**

| Approach | Recommendation |
|----------|---------------|
| **Polling (recommended for v1)** | Poll current price every 15-30 seconds. Check all active signals against current price. Simple, reliable, easy to debug. |
| WebSocket | Lower latency but more complex. Not needed for signal-level tracking (we need "was TP hit?" not "exact tick of hit"). |
| Price alert callbacks | Depends on broker API support. Can supplement polling. |

**Why polling wins for v1:** Signal outcomes are not latency-sensitive. Whether TP1 is detected 15 seconds after it was hit vs instantly makes no practical difference. Polling is simpler to implement, test, and debug. Upgrade to WebSocket in v2 if needed.

---

## 3. Data Flow

### 3.1 Primary Signal Generation Flow

```
1. Scheduler triggers candle fetch (on candle close)
       │
       ▼
2. Data Ingestion fetches new candles from broker API
       │
       ▼
3. New candles stored in PostgreSQL `candles` table
       │
       ▼
4. Scanner loop iterates over active strategies:
       │
       ├── For each strategy:
       │       │
       │       ▼
       │   4a. Fetch required timeframe candles from DB
       │       │
       │       ▼
       │   4b. Call strategy.analyze(candles) → CandidateSignal or None
       │
       ▼
5. Collect all CandidateSignals (0 to N)
       │
       ▼
6. Strategy Selector ranks candidates using backtest metrics
       │
       ▼
7. Best candidate (if confidence + R:R pass thresholds) → Signal Generator
       │
       ▼
8. Signal Generator:
       ├── Validates (dedup, R:R, confidence checks)
       ├── Persists to `signals` table
       └── Dispatches events:
               ├── → Telegram Notifier (sends alert)
               └── → TradingView UI (available via API)
```

### 3.2 Outcome Tracking Flow

```
1. Outcome Tracker polls current price every 15-30 seconds
       │
       ▼
2. Loads all ACTIVE signals from DB
       │
       ▼
3. For each active signal, checks:
       ├── Price crossed TP2? → result = TP2_HIT
       ├── Price crossed TP1? → result = TP1_HIT, update status, keep tracking for TP2
       ├── Price crossed SL? → result = SL_HIT
       └── Signal expired? → result = EXPIRED
       │
       ▼
4. If outcome detected:
       ├── Write to `outcomes` table
       ├── Update `signals.status`
       └── Trigger Feedback Loop
```

### 3.3 Feedback Loop Flow

```
1. New outcome recorded
       │
       ▼
2. Feedback Loop recalculates rolling metrics for that strategy:
       ├── Live 7-day performance
       ├── Live 30-day performance
       │
       ▼
3. Updates `strategy_performance` table
       │
       ▼
4. Checks degradation rules:
       ├── If degrading → set is_degrading = True, alert via Telegram
       └── If recovering → clear is_degrading flag
       │
       ▼
5. Strategy Selector uses updated metrics on next signal generation cycle
```

### 3.4 Backtesting Flow (Background)

```
1. Daily scheduled job (e.g., 00:05 UTC)
       │
       ▼
2. For each active strategy:
       │
       ├── Fetch 30-day + 60-day candle windows from DB
       │
       ├── Run strategy.analyze() on each historical candle
       │   (simulating what signals would have been generated)
       │
       ├── Calculate metrics: win rate, PF, Sharpe, drawdown, expectancy
       │
       └── Write to `backtest_results` table
       │
       ▼
3. Strategy Selector uses fresh backtest results on next cycle
```

---

## 4. FastAPI Application Structure

**Recommended pattern: API layer + background workers in one process (for v1)**

For a v1 system with moderate load, running background tasks via APScheduler within the FastAPI process is simpler than deploying separate worker processes. Graduate to Celery + Redis when/if the system needs horizontal scaling.

```
app/
├── main.py                      # FastAPI app factory, startup/shutdown events
├── config.py                    # Settings via pydantic-settings (env vars)
├── database.py                  # SQLAlchemy async engine + session factory
├── models/                      # SQLAlchemy ORM models
│   ├── __init__.py
│   ├── candle.py
│   ├── strategy.py
│   ├── backtest_result.py
│   ├── signal.py
│   ├── outcome.py
│   └── strategy_performance.py
├── schemas/                     # Pydantic request/response schemas
│   ├── __init__.py
│   ├── candle.py
│   ├── signal.py
│   ├── outcome.py
│   └── backtest.py
├── api/                         # FastAPI routers (REST endpoints)
│   ├── __init__.py
│   ├── candles.py               # GET /candles/{timeframe}
│   ├── signals.py               # GET /signals, GET /signals/{id}
│   ├── outcomes.py              # GET /outcomes
│   ├── strategies.py            # GET /strategies, GET /strategies/{id}/performance
│   ├── backtest.py              # GET /backtest/results, POST /backtest/run
│   └── health.py                # GET /health
├── services/                    # Business logic layer
│   ├── __init__.py
│   ├── scanner.py               # Main scanning loop orchestrator
│   ├── signal_generator.py
│   ├── strategy_selector.py
│   ├── outcome_tracker.py
│   ├── feedback_loop.py
│   └── telegram_notifier.py
├── strategies/                  # Strategy implementations (Component 3)
│   ├── __init__.py
│   ├── base.py
│   ├── registry.py
│   ├── liquidity_sweep_reversal.py
│   ├── trend_continuation.py
│   ├── breakout_expansion.py
│   └── indicators/
│       ├── __init__.py
│       ├── structure.py
│       ├── momentum.py
│       ├── volume.py
│       └── atr.py
├── backtesting/                 # Backtesting engine (Component 4)
│   ├── __init__.py
│   ├── engine.py
│   ├── metrics.py
│   └── walk_forward.py
├── data_ingestion/              # Data ingestion (Component 1)
│   ├── __init__.py
│   ├── ingestor.py
│   ├── sources/
│   │   ├── base.py
│   │   ├── oanda.py
│   │   └── tradingview.py
│   ├── normalizer.py
│   └── gap_detector.py
├── workers/                     # Background task scheduling
│   ├── __init__.py
│   ├── scheduler.py             # APScheduler setup
│   ├── jobs.py                  # Job definitions
│   └── tasks.py                 # Individual task functions
├── frontend/                    # TradingView chart UI
│   ├── templates/
│   │   └── chart.html
│   └── static/
│       ├── js/
│       └── css/
└── alembic/                     # Database migrations
    ├── versions/
    └── env.py
```

**FastAPI Startup Events:**

```python
# main.py
from fastapi import FastAPI
from contextlib import asynccontextmanager
from app.workers.scheduler import start_scheduler, stop_scheduler

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    await start_scheduler()  # Starts APScheduler with all jobs
    yield
    # Shutdown
    await stop_scheduler()

app = FastAPI(title="XAUUSD Signal System", lifespan=lifespan)
```

**Background Jobs (APScheduler):**

| Job | Trigger | Interval | Description |
|-----|---------|----------|-------------|
| `ingest_candles_m1` | Interval | 60s | Fetch M1 candles |
| `ingest_candles_m5` | Interval | 300s | Fetch M5 candles |
| `ingest_candles_m15` | Interval | 900s | Fetch M15 candles |
| `ingest_candles_h1` | Interval | 3600s | Fetch H1 candles |
| `ingest_candles_h4` | Interval | 14400s | Fetch H4 candles |
| `ingest_candles_d1` | Cron | 00:01 UTC | Fetch D1 candles |
| `run_scanner` | Interval | 60s | Run strategy analysis on latest candles |
| `track_outcomes` | Interval | 15s | Check active signals vs current price |
| `run_backtests` | Cron | 00:05 UTC daily | Re-run backtests for all strategies |
| `update_performance` | Interval | 300s | Recalculate strategy performance metrics |

---

## 5. Scanning Loop Design

The scanner is the heart of the system — the main loop that checks for new trade setups.

**Recommended approach: Event-triggered with interval fallback**

```python
# services/scanner.py

class Scanner:
    def __init__(self, strategy_registry, selector, signal_generator):
        self.registry = strategy_registry
        self.selector = selector
        self.generator = signal_generator

    async def run(self):
        """Called by scheduler every 60 seconds (or on new candle event)."""

        # 1. Check if any new candles have arrived since last scan
        new_candles = await self.check_new_candles()
        if not new_candles:
            return  # No new data, skip this cycle

        # 2. Run each active strategy
        candidates = []
        for strategy in self.registry.get_active_strategies():
            candles = await self.fetch_candles(strategy.required_timeframes)
            candidate = strategy.analyze(candles)
            if candidate:
                candidates.append(candidate)

        # 3. If any candidates, select the best
        if not candidates:
            return

        best = self.selector.select(candidates)
        if best is None:
            return  # All candidates below threshold

        # 4. Generate and dispatch signal
        await self.generator.generate(best)
```

**Key design principle:** The scanner runs frequently (every 60s) but is a **no-op** if no new candle data has arrived. This prevents duplicate signals while ensuring responsiveness.

---

## 6. Multi-Timeframe Data Management

**Challenge:** Strategies need synchronized data across multiple timeframes (e.g., M15 + H1 + H4). Higher timeframes update less frequently, so a strategy checking H4 data might be looking at a candle that closed hours ago.

**Solution:**

1. **Store all timeframes in the same `candles` table**, differentiated by the `timeframe` column
2. **Each timeframe has its own ingestion schedule** aligned to candle close times
3. **Strategies declare their `required_timeframes`** — the scanner fetches the latest N candles for each
4. **No cross-timeframe derivation in v1** — fetch each timeframe independently from the broker API (simpler, avoids aggregation bugs)
5. **Staleness check:** Before running a strategy, verify that the most recent candle for each required timeframe is not older than expected (e.g., latest H1 candle should be <70 minutes old during market hours)

```python
async def fetch_candles(self, timeframes: list[str]) -> dict[str, pd.DataFrame]:
    result = {}
    for tf in timeframes:
        candles = await self.db.get_candles(
            symbol="XAUUSD",
            timeframe=tf,
            limit=200  # enough history for any strategy
        )
        if self.is_stale(candles, tf):
            logger.warning(f"Stale data for {tf}, skipping scan")
            return None
        result[tf] = candles
    return result
```

---

## 7. Technology Stack Summary

| Layer | Technology | Rationale |
|-------|-----------|-----------|
| Runtime | Python 3.11+ | Ecosystem (pandas, numpy, ta-lib), team familiarity |
| Web Framework | FastAPI | Async, fast, auto-docs, Pydantic integration |
| Database | PostgreSQL (Railway) | ACID, JSONB, mature, Railway provides managed hosting |
| ORM | SQLAlchemy 2.0 (async) | Industry standard, excellent migration support (Alembic) |
| Migrations | Alembic | SQLAlchemy's companion, production-proven |
| Background Jobs | APScheduler | In-process, simple for v1. Upgrade to Celery if needed |
| Data Processing | pandas + numpy | Fast OHLCV manipulation, indicator calculation |
| Technical Indicators | pandas-ta or ta-lib | Comprehensive indicator libraries |
| Telegram | python-telegram-bot (v20+) | Async, well-maintained, full API coverage |
| Charts | TradingView Lightweight Charts | Free, fast, interactive, programmatic overlays |
| HTTP Client | httpx (async) | Modern async HTTP, connection pooling |
| Config | pydantic-settings | Type-safe env var parsing, validation |
| Logging | structlog | Structured JSON logging, great for production debugging |
| Testing | pytest + pytest-asyncio | Standard Python testing with async support |

---

## 8. Suggested Build Order

The build order is determined by dependency analysis — each phase builds on what was delivered in previous phases.

### Phase 1: Foundation (Week 1-2)
**Components:** Database + Data Ingestion + FastAPI skeleton

**Why first:** Everything else depends on having candle data in the database and a running API server.

**Deliverables:**
- PostgreSQL schema created via Alembic migrations
- FastAPI app with health check endpoint
- Data ingestion for at least one timeframe (H1) from one source
- Basic candle storage and retrieval
- `GET /candles/{timeframe}` endpoint working

**Exit criteria:** Can fetch H1 XAUUSD candles, store them, and retrieve them via API.

---

### Phase 2: Strategy Engine (Week 3-4)
**Components:** BaseStrategy + one strategy implementation + indicators

**Why second:** Need the strategy abstraction in place before backtesting or signal generation.

**Deliverables:**
- `BaseStrategy` abstract class and `CandidateSignal` dataclass
- Strategy registry with auto-discovery
- First strategy implemented (recommend LiquiditySweepReversal — most distinctive)
- Indicator helper functions (swing highs/lows, ATR, support/resistance)
- Multi-timeframe data fetching (all timeframes ingesting)

**Exit criteria:** Can call `strategy.analyze(candles)` and get a `CandidateSignal` back on historical data.

---

### Phase 3: Backtesting Engine (Week 5-6)
**Components:** Backtesting engine + metrics calculation

**Why third:** Need backtesting before the strategy selector can rank strategies.

**Deliverables:**
- Backtest engine that replays historical data through strategy.analyze()
- Metric calculations (win rate, PF, Sharpe, drawdown, expectancy)
- Walk-forward validation
- Results persisted to `backtest_results` table
- Background job to run daily backtests
- `GET /backtest/results` endpoint

**Exit criteria:** Can run a 30-day backtest for a strategy and see metrics.

---

### Phase 4: Signal Pipeline (Week 7-8)
**Components:** Strategy Selector + Signal Generator + Scanner Loop

**Why fourth:** Now we have strategies and backtest metrics — can wire up the full signal pipeline.

**Deliverables:**
- Strategy selector with scoring algorithm
- Signal generator with dedup, R:R filter, confidence threshold
- Scanner loop running on schedule (APScheduler)
- Remaining two strategies implemented
- `GET /signals` endpoint
- Signal persistence

**Exit criteria:** System automatically generates trade signals from live data.

---

### Phase 5: Notifications + Delivery (Week 9)
**Components:** Telegram Notifier + TradingView Chart UI

**Why fifth:** Signals are flowing — now make them visible to the user.

**Deliverables:**
- Telegram bot sending formatted signal alerts
- TradingView Lightweight Charts page with candle display
- Signal overlay on charts (entry, SL, TP markers)
- Basic frontend accessible via browser

**Exit criteria:** Receive a Telegram alert for a new signal and can view it on the chart.

---

### Phase 6: Outcome Tracking + Feedback (Week 10-11)
**Components:** Outcome Tracker + Feedback Loop

**Why sixth:** System is generating and delivering signals. Now close the loop.

**Deliverables:**
- Outcome tracker polling price and checking active signals
- Outcome persistence + signal status updates
- Telegram notifications for outcomes ("TP1 hit! +145 pips")
- Feedback loop recalculating strategy performance
- Degradation detection + auto-deprioritization
- Chart UI updated to show outcome colors (green/red)

**Exit criteria:** Full closed loop — signal generated → outcome detected → metrics updated → strategy ranking adjusted.

---

### Phase 7: Hardening + Polish (Week 12)
**Components:** Error handling, monitoring, performance, testing

**Deliverables:**
- Comprehensive error handling and retry logic
- Structured logging throughout
- API rate limiting
- Database connection pooling optimization
- Unit tests for strategies and metrics
- Integration tests for signal pipeline
- Performance profiling of scanner loop
- Documentation

**Exit criteria:** System runs reliably 24/5 (market hours) without manual intervention.

---

## 9. Key Architectural Risks and Mitigations

| Risk | Impact | Mitigation |
|------|--------|-----------|
| Data source API downtime | No new candles → no signals | Implement fallback data source. Cache last known price. Alert on staleness. |
| Strategy overfitting | Backtest looks great, live performance poor | Walk-forward validation. Compare live vs backtest metrics. Auto-degradation detection. |
| Database bottleneck (candle volume) | M1 candles = ~500K rows/year | Partition by month. Archive old data. Index on (symbol, timeframe, timestamp). |
| Single process failure | Entire system goes down | Railway auto-restart. Health check endpoint. Consider 2-process deployment in v2 (API + worker). |
| Telegram API rate limits | Delayed notifications | Batch messages if multiple signals. Respect rate limits (30 msg/sec). Queue with retry. |
| Signal quality in ranging markets | Low win rate → user trust erosion | Regime detection in selector. Minimum confidence threshold. Conservative position sizing in signal reasoning. |
| Backtest/live divergence | False confidence in strategies | Same strategy code for both. No look-ahead bias in backtester (use only data available at signal time). |

---

## 10. Deployment Architecture (Railway)

```
Railway Project
├── Service: xauusd-signal-api
│   ├── FastAPI application (API + background workers)
│   ├── Dockerfile (Python 3.11, uvicorn)
│   ├── PORT: 8000
│   └── Environment variables:
│       ├── DATABASE_URL (auto-injected by Railway)
│       ├── TELEGRAM_BOT_TOKEN
│       ├── TELEGRAM_CHAT_ID
│       ├── BROKER_API_KEY
│       ├── BROKER_ACCOUNT_ID
│       └── LOG_LEVEL
│
├── Database: PostgreSQL
│   ├── Managed by Railway
│   ├── Auto-backups enabled
│   └── Connected to service via internal networking
│
└── (Future) Service: xauusd-worker
    └── Separate process for heavy backtesting (if needed)
```

---

## Quality Gate Checklist

- [x] **Components clearly defined with boundaries** — 10 components defined, each with explicit "owns" / "does not own" boundaries, inputs, and outputs
- [x] **Data flow direction explicit** — 4 data flows documented (signal generation, outcome tracking, feedback loop, backtesting) with step-by-step progression
- [x] **Build order implications noted** — 7-phase build order with dependency rationale, deliverables, and exit criteria for each phase

---

*Research completed: 2026-02-17*
*Downstream consumer: Roadmap phase structure (.planning/roadmap/)*
