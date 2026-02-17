# Phase 5: Delivery and Visibility - Research

**Researched:** 2026-02-17
**Domain:** Telegram Bot API messaging, TradingView Lightweight Charts, FastAPI HTML serving, async retry/rate-limiting
**Confidence:** HIGH

## Summary

Phase 5 adds two delivery channels: (1) a Telegram bot that sends formatted signal alerts and outcome notifications, and (2) a browser-accessible web page displaying a live XAUUSD candlestick chart with signal markers and SL/TP overlays. Both channels are triggered by existing pipeline events -- signal creation and outcome detection.

The Telegram component is intentionally simple: direct HTTP calls to the Telegram Bot API via `httpx` (already a project dependency), with `tenacity` (already a dependency) providing retry logic and a lightweight `asyncio.Lock`-based rate limiter ensuring max 1 msg/sec compliance. No Telegram bot framework (python-telegram-bot, aiogram) is needed -- the project only sends outbound messages; it does not receive commands or handle updates.

The chart component uses TradingView's Lightweight Charts v5.1 loaded via CDN `<script>` tag in a single HTML page served by FastAPI. Chart data (candles + signals) is served through two new REST endpoints. No npm/node toolchain is needed -- the chart is a self-contained HTML page with inline JavaScript that fetches data from the FastAPI backend.

**Primary recommendation:** Use direct `httpx` POST to Telegram Bot API (no framework), `tenacity` for retry, `asyncio`-based rate limiter, and a single-page HTML chart served via `FastAPI.HTMLResponse` with TradingView Lightweight Charts v5.1 loaded from unpkg CDN.

## Standard Stack

### Core (new dependencies)
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| jinja2 | >=3.1.0 | HTML template rendering for chart page | FastAPI's recommended template engine; cleaner than inline HTML strings |

### Core (already installed -- no new external dependencies)
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| httpx | >=0.28.0 | Async HTTP client for Telegram Bot API calls | Already in requirements.txt; async-native, used for DXY calls |
| tenacity | >=9.0.0 | Retry with exponential backoff for Telegram delivery | Already in requirements.txt; proven retry library |
| FastAPI | >=0.115.0 | REST endpoints for chart data + HTML page serving | Already the app framework |
| SQLAlchemy | >=2.0.36 | Query signals/outcomes/candles for chart data | Already established ORM |
| pydantic | >=2.10.0 | Response schemas for chart API endpoints | Already used throughout |
| loguru | >=0.7.3 | Structured logging for Telegram delivery | Already used project-wide |

### Frontend (CDN -- no install needed)
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| lightweight-charts | 5.1.0 | TradingView's official financial charting library | 35kB bundle, open source, Apache 2.0 license, performant HTML5 canvas |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| Direct httpx to Telegram API | python-telegram-bot (22.0) | Full framework is overkill; we only send messages, never receive. httpx is already a dependency. Avoids ~15 transitive dependencies. |
| Direct httpx to Telegram API | aiogram (3.x) | Same reasoning -- aiogram is for building interactive bots with command handlers, routers, middleware. We just POST to sendMessage. |
| asyncio.Lock rate limiter | aiolimiter library | Adding a dependency for a 10-line implementation is unnecessary. A simple `asyncio.Lock` + `asyncio.sleep` handles max 1 msg/sec. |
| Jinja2 template | Inline HTMLResponse string | Jinja2 is cleaner for a full HTML page with embedded JS; separates concerns. Template can be maintained independently. FastAPI docs recommend it. |
| Jinja2 template | React/Vue SPA | Massive overkill for a single chart page. No build toolchain needed. The chart is read-only with no user interaction beyond panning/zooming. |
| TradingView Lightweight Charts | Plotly/Bokeh | Lightweight Charts is purpose-built for financial charts, 35kB vs 3MB+ for Plotly. TradingView is the industry standard. |
| CDN script tag | npm + bundler | No Node.js toolchain exists in this project. CDN is simpler, zero build step, and perfectly fine for a single-page chart. |

**Installation:**
```bash
pip install jinja2>=3.1.0
# Update requirements.txt to add: jinja2>=3.1.0
# lightweight-charts loaded via CDN <script> tag -- no pip install
```

## Architecture Patterns

### Recommended Project Structure
```
app/
  services/
    telegram_notifier.py   # Telegram message formatting + delivery
    outcome_tracker.py     # Monitors active signals for TP/SL hits (or extend signal_pipeline)
  api/
    chart.py               # REST endpoints: GET /chart/candles, GET /chart/signals, GET /chart
  templates/
    chart.html             # Jinja2 template: Lightweight Charts page with JS
  # Existing files unchanged:
  services/signal_pipeline.py  # Hook: call telegram_notifier after persisting signals
  workers/jobs.py              # Hook: call telegram_notifier on outcome detection
  config.py                    # Add: telegram_bot_token, telegram_chat_id
```

### Pattern 1: Notification Service (Fire-and-Forget with Retry)
**What:** A `TelegramNotifier` class that formats messages and sends them via Telegram Bot API with retry and rate limiting. Called by the signal pipeline after persisting signals.
**When to use:** When notifications must not block or crash the pipeline on failure.
**Example:**
```python
# Source: Telegram Bot API docs + tenacity docs
import asyncio
from loguru import logger
import httpx
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

class TelegramNotifier:
    """Sends formatted trade signals via Telegram Bot API."""

    TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"

    def __init__(self, bot_token: str, chat_id: str) -> None:
        self.bot_token = bot_token
        self.chat_id = chat_id
        self._rate_lock = asyncio.Lock()
        self._last_send: float = 0.0

    async def _rate_limit(self) -> None:
        """Enforce max 1 message per second to same chat (TELE-05)."""
        async with self._rate_lock:
            now = asyncio.get_event_loop().time()
            elapsed = now - self._last_send
            if elapsed < 1.0:
                await asyncio.sleep(1.0 - elapsed)
            self._last_send = asyncio.get_event_loop().time()

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type((httpx.HTTPStatusError, httpx.ConnectError)),
    )
    async def _send_message(self, text: str) -> dict:
        """POST to Telegram sendMessage with retry (TELE-04)."""
        await self._rate_limit()
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                self.TELEGRAM_API.format(token=self.bot_token),
                json={
                    "chat_id": self.chat_id,
                    "text": text,
                    "parse_mode": "HTML",
                },
            )
            response.raise_for_status()
            return response.json()

    def format_signal(self, signal) -> str:
        """Format signal as HTML message (TELE-01)."""
        arrow = "\u2B06" if signal.direction == "BUY" else "\u2B07"
        return (
            f"{arrow} <b>XAUUSD {signal.direction}</b>\n\n"
            f"<b>Entry:</b> {signal.entry_price}\n"
            f"<b>Stop Loss:</b> {signal.stop_loss}\n"
            f"<b>TP1:</b> {signal.take_profit_1}\n"
            f"<b>TP2:</b> {signal.take_profit_2}\n"
            f"<b>R:R:</b> {signal.risk_reward}\n"
            f"<b>Confidence:</b> {signal.confidence}%\n"
            f"<b>Strategy:</b> {signal.strategy_name}\n\n"
            f"<i>{signal.reasoning}</i>"
        )

    def format_outcome(self, signal, outcome) -> str:
        """Format outcome as HTML message (TELE-02)."""
        emoji = {"tp1_hit": "\u2705", "tp2_hit": "\u2705\u2705", "sl_hit": "\u274C", "expired": "\u23F0"}
        return (
            f"{emoji.get(outcome.result, '')} <b>XAUUSD {signal.direction} - {outcome.result.upper()}</b>\n\n"
            f"<b>Entry:</b> {signal.entry_price}\n"
            f"<b>Exit:</b> {outcome.exit_price}\n"
            f"<b>P&L:</b> {outcome.pnl_pips} pips\n"
            f"<b>Duration:</b> {outcome.duration_minutes} min"
        )

    async def notify_signal(self, signal) -> None:
        """Send signal alert, log failure without raising."""
        try:
            text = self.format_signal(signal)
            await self._send_message(text)
            logger.info("Telegram signal notification sent for signal_id={}", signal.id)
        except Exception:
            logger.exception("Telegram signal notification failed for signal_id={}", signal.id)

    async def notify_outcome(self, signal, outcome) -> None:
        """Send outcome alert, log failure without raising."""
        try:
            text = self.format_outcome(signal, outcome)
            await self._send_message(text)
            logger.info("Telegram outcome notification sent for signal_id={}", signal.id)
        except Exception:
            logger.exception("Telegram outcome notification failed for signal_id={}", signal.id)
```

### Pattern 2: Pipeline Integration Hook
**What:** After `SignalPipeline.run()` persists signals, it calls `TelegramNotifier.notify_signal()` for each. The notifier is injected as a dependency, making it mockable in tests.
**When to use:** When a post-persist side effect should not break the main pipeline flow.
**Example:**
```python
# In signal_pipeline.py, after commit:
for signal in persisted:
    await self.notifier.notify_signal(signal)
```

### Pattern 3: Chart Data API + Static HTML
**What:** FastAPI REST endpoints serve candle data and signal data as JSON. A separate endpoint serves an HTML page that loads Lightweight Charts from CDN and fetches data from these endpoints.
**When to use:** When the frontend is simple enough that a single HTML page with inline JS suffices.
**Example:**
```python
# Source: FastAPI official docs + TradingView Lightweight Charts docs
from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

router = APIRouter(prefix="/chart", tags=["chart"])
templates = Jinja2Templates(directory="app/templates")

@router.get("/candles")
async def get_chart_candles(
    limit: int = 500,
    session: AsyncSession = Depends(get_session),
):
    """Return H1 XAUUSD candles formatted for Lightweight Charts."""
    # Query candles, return as [{time: unix_ts, open, high, low, close}, ...]
    ...

@router.get("/signals")
async def get_chart_signals(
    session: AsyncSession = Depends(get_session),
):
    """Return signals with outcomes for chart markers and price lines."""
    # Query signals + left join outcomes, return markers and price line data
    ...

@router.get("/", response_class=HTMLResponse)
async def chart_page(request: Request):
    """Serve the chart HTML page."""
    return templates.TemplateResponse(request=request, name="chart.html")
```

### Pattern 4: Lightweight Charts v5 Candlestick with Markers and Price Lines
**What:** A single HTML page that loads Lightweight Charts v5 from CDN, fetches candle + signal data from the FastAPI backend, and renders a candlestick chart with entry markers and SL/TP horizontal lines.
**When to use:** For the chart page (TV-01 through TV-05).
**Example:**
```html
<!-- Source: TradingView Lightweight Charts v5 official docs -->
<!DOCTYPE html>
<html>
<head>
    <title>GoldSignal - XAUUSD Chart</title>
    <script src="https://unpkg.com/lightweight-charts@5.1.0/dist/lightweight-charts.standalone.production.js"></script>
</head>
<body>
    <div id="chart" style="width:100%;height:600px;"></div>
    <script>
        // Create chart
        const chart = LightweightCharts.createChart(document.getElementById('chart'), {
            layout: { textColor: '#DDD', background: { type: 'solid', color: '#1E1E1E' } },
            grid: { vertLines: { color: '#2B2B2B' }, horzLines: { color: '#2B2B2B' } },
        });

        // Add candlestick series (v5 API)
        const series = chart.addSeries(LightweightCharts.CandlestickSeries, {
            upColor: '#26a69a', downColor: '#ef5350',
            borderVisible: false,
            wickUpColor: '#26a69a', wickDownColor: '#ef5350',
        });

        // Fetch and set candle data
        fetch('/chart/candles')
            .then(r => r.json())
            .then(data => {
                series.setData(data);
                chart.timeScale().fitContent();
            });

        // Fetch signals and add markers + price lines
        fetch('/chart/signals')
            .then(r => r.json())
            .then(signals => {
                const markers = signals.map(s => ({
                    time: s.time,
                    position: s.direction === 'BUY' ? 'belowBar' : 'aboveBar',
                    color: s.outcome_color,  // green/red/gray
                    shape: s.direction === 'BUY' ? 'arrowUp' : 'arrowDown',
                    text: s.direction,
                }));
                LightweightCharts.createSeriesMarkers(series, markers);

                // Add SL/TP price lines for active signals
                signals.filter(s => s.status === 'active').forEach(s => {
                    series.createPriceLine({ price: s.entry_price, color: '#3179F5', lineWidth: 1, lineStyle: 2, title: 'Entry' });
                    series.createPriceLine({ price: s.stop_loss, color: '#ef5350', lineWidth: 1, lineStyle: 1, title: 'SL' });
                    series.createPriceLine({ price: s.take_profit_1, color: '#26a69a', lineWidth: 1, lineStyle: 1, title: 'TP1' });
                    series.createPriceLine({ price: s.take_profit_2, color: '#26a69a', lineWidth: 1, lineStyle: 1, title: 'TP2' });
                });
            });
    </script>
</body>
</html>
```

### Anti-Patterns to Avoid
- **MarkdownV2 for Telegram:** Gold prices like `2650.50` require escaping dots in MarkdownV2 (`2650\.50`). HTML parse mode avoids this entirely. Use `parse_mode: "HTML"` always.
- **Blocking the pipeline on notification failure:** Telegram delivery must be fire-and-forget (log errors, never raise to pipeline). A failed notification must not prevent signal persistence.
- **Creating httpx.AsyncClient per request:** Create one client per notifier instance or use a context manager efficiently. Avoid connection overhead on every message.
- **Using python-telegram-bot/aiogram for send-only:** These are full bot frameworks with update handlers, middleware, conversation state. We only send outbound messages. Direct httpx is simpler, has fewer dependencies, and is already in requirements.txt.
- **npm/webpack build for charts:** The project has no Node.js toolchain. CDN script tag is the correct approach for a single chart page.
- **Heavy SPA framework for one chart page:** React/Vue/Angular would be massive overkill. Vanilla JS + Lightweight Charts CDN is the right level of complexity.

## Don't Hand-Roll

Problems that look simple but have existing solutions:

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Retry with exponential backoff | Custom retry loop with sleep | tenacity `@retry` decorator | Edge cases: jitter, max attempts, exception filtering, logging. Already a dependency. |
| Telegram message formatting | Custom string concatenation with escape logic | HTML parse mode (`parse_mode: "HTML"`) | MarkdownV2 requires escaping 18 special characters including `.` which appears in every gold price. HTML just works. |
| Financial candlestick chart | Custom canvas/SVG rendering | TradingView Lightweight Charts v5.1 | Professional-grade chart with pan, zoom, crosshair, responsive -- 35kB. Years of edge-case handling. |
| Rate limiting | Complex token bucket library | Simple asyncio.Lock + time check | For a single-chat bot sending at most a few messages per hour, a full token bucket library is unnecessary. |

**Key insight:** The Telegram Bot API is simple enough that a framework adds more complexity than it removes. The chart library does the heavy lifting on the frontend. The backend's job is just serving JSON data.

## Common Pitfalls

### Pitfall 1: MarkdownV2 Escaping with Gold Prices
**What goes wrong:** Gold prices like `2650.50` contain dots that must be escaped in MarkdownV2 as `2650\.50`. Missing an escape character causes the entire message to fail silently or with a cryptic 400 error.
**Why it happens:** MarkdownV2 requires escaping: `_`, `*`, `[`, `]`, `(`, `)`, `~`, `` ` ``, `>`, `#`, `+`, `-`, `=`, `|`, `{`, `}`, `.`, `!`.
**How to avoid:** Use `parse_mode: "HTML"` exclusively (already decided in TELE-03). HTML only requires escaping `<`, `>`, `&`, `"`.
**Warning signs:** 400 Bad Request from Telegram API with "Can't parse entities" error message.

### Pitfall 2: Telegram 429 Rate Limit
**What goes wrong:** Sending multiple messages rapidly (e.g., 5 signals fire at once) triggers Telegram's rate limit (max ~1 msg/sec per chat), returning 429 Too Many Requests.
**Why it happens:** Telegram enforces per-chat rate limits. In a single chat, the limit is approximately 1 message per second. The 429 response includes a `retry_after` field.
**How to avoid:** Implement per-chat rate limiting (asyncio.Lock + 1s minimum gap). Handle 429 by sleeping for the `retry_after` duration.
**Warning signs:** 429 status codes in logs, messages arriving out of order or with delays.

### Pitfall 3: Lightweight Charts v5 API Changes from v4
**What goes wrong:** Using v4 API methods (`addCandlestickSeries()`, `series.setMarkers()`) with v5 library fails silently or throws errors.
**Why it happens:** v5 unified series creation to `chart.addSeries(CandlestickSeries, options)` and extracted markers to `createSeriesMarkers(series, markers)`.
**How to avoid:** Use v5 API exclusively: `chart.addSeries(LightweightCharts.CandlestickSeries, ...)` and `LightweightCharts.createSeriesMarkers(series, [...])`. For standalone/CDN build, all types are under the `LightweightCharts` global namespace.
**Warning signs:** `TypeError: chart.addCandlestickSeries is not a function` or `series.setMarkers is not a function`.

### Pitfall 4: Lightweight Charts Time Format
**What goes wrong:** Lightweight Charts expects time as either UTC Unix timestamp (seconds) or `{ year, month, day }` object. Passing ISO strings or millisecond timestamps produces no data or broken x-axis.
**Why it happens:** The library has strict time format requirements.
**How to avoid:** Convert candle timestamps to Unix seconds (`int(timestamp.timestamp())`) in the FastAPI endpoint before sending to frontend.
**Warning signs:** Empty chart, garbled x-axis labels, or `Invalid time` console errors.

### Pitfall 5: Pipeline Crash from Notification Failure
**What goes wrong:** An unhandled Telegram API error (network timeout, invalid token, API down) propagates up and crashes the signal pipeline, preventing signal persistence.
**Why it happens:** The notification call is placed in the pipeline flow without proper isolation.
**How to avoid:** Wrap all notification calls in try/except at the caller level. The notifier itself should catch exceptions, but the pipeline should have a safety net too. Notifications are fire-and-forget: log failures, never raise.
**Warning signs:** Signals generated but not persisted to DB, scheduler job marked as failed.

### Pitfall 6: Telegram Bot Token/Chat ID Not Configured
**What goes wrong:** App starts up fine but Telegram notifications silently fail because env vars are missing or empty.
**Why it happens:** New env vars (`TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`) are required but not in .env template.
**How to avoid:** Make settings optional with defaults that disable Telegram. Log a clear warning at startup if credentials are missing. Allow the system to function without Telegram configured.
**Warning signs:** No Telegram messages arrive, no errors in logs (if silently skipped without logging).

## Code Examples

Verified patterns from official sources:

### Telegram Bot API sendMessage (Direct httpx)
```python
# Source: https://core.telegram.org/bots/api#sendmessage
async def send_telegram_message(
    bot_token: str, chat_id: str, text: str
) -> dict:
    """Send HTML-formatted message via Telegram Bot API."""
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.post(
            f"https://api.telegram.org/bot{bot_token}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": text,
                "parse_mode": "HTML",
            },
        )
        response.raise_for_status()
        return response.json()
```

### Tenacity Retry for Async Function
```python
# Source: https://tenacity.readthedocs.io/
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
import httpx

@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception_type((httpx.HTTPStatusError, httpx.ConnectError)),
)
async def send_with_retry(client: httpx.AsyncClient, url: str, payload: dict) -> dict:
    response = await client.post(url, json=payload)
    response.raise_for_status()
    return response.json()
```

### Rate Limiter (asyncio-based, 1 msg/sec)
```python
# Source: Custom pattern using asyncio primitives
import asyncio

class SimpleRateLimiter:
    """Ensures minimum interval between calls."""

    def __init__(self, min_interval: float = 1.0) -> None:
        self._lock = asyncio.Lock()
        self._last_call: float = 0.0
        self._min_interval = min_interval

    async def acquire(self) -> None:
        async with self._lock:
            loop = asyncio.get_event_loop()
            now = loop.time()
            wait_time = self._min_interval - (now - self._last_call)
            if wait_time > 0:
                await asyncio.sleep(wait_time)
            self._last_call = asyncio.get_event_loop().time()
```

### Lightweight Charts v5 Candlestick + Markers + Price Lines (Standalone CDN)
```javascript
// Source: https://tradingview.github.io/lightweight-charts/docs
// Source: https://tradingview.github.io/lightweight-charts/docs/migrations/from-v4-to-v5

// Create chart (standalone build uses LightweightCharts global)
const chart = LightweightCharts.createChart(container, {
    width: container.clientWidth,
    height: 600,
    layout: { textColor: '#DDD', background: { type: 'solid', color: '#1E1E1E' } },
});

// Add candlestick series (v5 API: addSeries with type)
const series = chart.addSeries(LightweightCharts.CandlestickSeries, {
    upColor: '#26a69a',
    downColor: '#ef5350',
    borderVisible: false,
    wickUpColor: '#26a69a',
    wickDownColor: '#ef5350',
});

// Set OHLC data (time must be Unix seconds or {year, month, day})
series.setData([
    { time: 1706745600, open: 2645.50, high: 2647.00, low: 2644.00, close: 2646.00 },
    // ...
]);

// Add markers (v5: createSeriesMarkers, NOT series.setMarkers)
LightweightCharts.createSeriesMarkers(series, [
    { time: 1706745600, position: 'belowBar', color: '#26a69a', shape: 'arrowUp', text: 'BUY' },
]);

// Add horizontal price lines for SL/TP
series.createPriceLine({
    price: 2645.00,
    color: '#ef5350',
    lineWidth: 1,
    lineStyle: LightweightCharts.LineStyle.Dotted,
    axisLabelVisible: true,
    title: 'SL',
});
```

### FastAPI Jinja2 Template Endpoint
```python
# Source: https://fastapi.tiangolo.com/advanced/templates/
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

router = APIRouter(prefix="/chart", tags=["chart"])
templates = Jinja2Templates(directory="app/templates")

@router.get("/", response_class=HTMLResponse)
async def chart_page(request: Request):
    return templates.TemplateResponse(request=request, name="chart.html")
```

### Config Extension for Telegram Settings
```python
# Source: Existing app/config.py pattern
class Settings(BaseSettings):
    # ... existing fields ...

    # Telegram (optional -- system works without it)
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| `chart.addCandlestickSeries()` | `chart.addSeries(CandlestickSeries, opts)` | Lightweight Charts v5 (Oct 2024) | Must import/reference series type separately |
| `series.setMarkers([...])` | `createSeriesMarkers(series, [...])` | Lightweight Charts v5 (Oct 2024) | Markers are now a separate primitive, enables tree-shaking |
| MarkdownV2 for Telegram formatting | HTML parse mode | Telegram Bot API (long-standing) | HTML avoids the 18-character escaping nightmare |
| python-telegram-bot for all use cases | Direct httpx for send-only bots | Community trend 2024+ | Frameworks are for interactive bots; send-only is simpler with raw HTTP |

**Deprecated/outdated:**
- Lightweight Charts v4 API (`addCandlestickSeries`, `setMarkers`): Replaced in v5 (Oct 2024)
- `lightweight-charts.standalone.production.js` (v3.x): Use v5.1.0 from unpkg CDN

## Open Questions

Things that could not be fully resolved:

1. **Outcome tracking trigger mechanism**
   - What we know: Outcomes (TP1 hit, TP2 hit, SL hit, expired) need to be detected so TELE-02 outcome notifications can fire. The `Outcome` model and `expire_stale_signals` mechanism exist. Signal expiry is handled in the pipeline.
   - What's unclear: Whether outcome tracking (comparing live price to signal SL/TP) should run as a separate scheduled job or be integrated into the existing candle refresh/signal scanner flow. The phase description says "outcome detected" but doesn't specify the detection mechanism.
   - Recommendation: Add an `outcome_checker` job that runs on the M15 candle schedule (every 15 minutes). It queries active signals, compares latest candle high/low against SL/TP levels, creates Outcome records, updates signal status, and fires Telegram notifications. This is a new job in the scheduler, separate from the signal pipeline.

2. **Lightweight Charts v5 standalone build and createSeriesMarkers**
   - What we know: The standalone CDN build exposes `LightweightCharts` as a global. In v5, markers use `createSeriesMarkers()`. The migration guide confirms this function exists.
   - What's unclear: Whether `LightweightCharts.createSeriesMarkers` is exported in the standalone production build (vs. ESM import). All search results suggest it is available on the global namespace, but this should be verified during implementation.
   - Recommendation: Test with a simple HTML file loading the CDN script before building the full chart page. Fallback: use v4 API if v5 standalone build has issues (unlikely).

3. **Chart page auto-refresh**
   - What we know: The requirement says "live XAUUSD candlestick chart" (TV-01). Candles update every hour (H1 timeframe).
   - What's unclear: Whether "live" means auto-refreshing or just showing the latest data on page load. True real-time would require WebSocket; periodic refresh is simpler.
   - Recommendation: Implement periodic `fetch` polling (every 60 seconds) to update candle data and signal markers. This is sufficient for H1 candles and avoids WebSocket complexity. The chart will update within a minute of new data arriving.

## Sources

### Primary (HIGH confidence)
- [Telegram Bot API Official Documentation](https://core.telegram.org/bots/api) - sendMessage endpoint, parse_mode HTML, rate limits
- [Telegram Bot FAQ](https://core.telegram.org/bots/faq) - Rate limits: max 1 msg/sec per chat, 30 msgs/sec globally
- [TradingView Lightweight Charts Official Docs](https://tradingview.github.io/lightweight-charts/docs) - v5.1 API, createChart, addSeries, CandlestickSeries
- [Lightweight Charts v4 to v5 Migration Guide](https://tradingview.github.io/lightweight-charts/docs/migrations/from-v4-to-v5) - Breaking changes: addSeries, createSeriesMarkers
- [Lightweight Charts Series Markers Tutorial](https://tradingview.github.io/lightweight-charts/tutorials/how_to/series-markers) - createSeriesMarkers API
- [Lightweight Charts Price Line Tutorial](https://tradingview.github.io/lightweight-charts/tutorials/how_to/price-line) - createPriceLine API
- [FastAPI Templates Documentation](https://fastapi.tiangolo.com/advanced/templates/) - Jinja2Templates setup
- [Tenacity Documentation](https://tenacity.readthedocs.io/) - @retry decorator, async support, wait_exponential
- [TradingView Lightweight Charts GitHub Releases](https://github.com/tradingview/lightweight-charts/releases) - v5.1.0 (Dec 2024)

### Secondary (MEDIUM confidence)
- [Telegram HTML Formatting Guide](https://www.misterchatter.com/docs/telegram-html-formatting-guide-supported-tags/) - Supported HTML tags verified against official docs
- [httpx Official Documentation](https://www.python-httpx.org/) - AsyncClient API

### Tertiary (LOW confidence)
- WebSearch results on Telegram rate limits: "30 messages per second per bot token" -- verified against official FAQ
- WebSearch results on lightweight-charts standalone build: `LightweightCharts.createSeriesMarkers` availability in global namespace -- should be verified during implementation

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH - All libraries either already installed or well-documented with official sources
- Architecture: HIGH - Patterns derived from official docs (FastAPI templates, Telegram API, Lightweight Charts) and codebase analysis
- Pitfalls: HIGH - MarkdownV2 escaping and v5 migration are well-documented breaking changes; rate limits confirmed from official Telegram FAQ

**Research date:** 2026-02-17
**Valid until:** 2026-03-17 (30 days -- stable domain, no fast-moving dependencies)
