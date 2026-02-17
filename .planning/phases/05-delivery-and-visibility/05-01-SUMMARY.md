---
phase: 05-delivery-and-visibility
plan: 01
subsystem: notifications
tags: [telegram, httpx, tenacity, async, rate-limiting, html-formatting]

# Dependency graph
requires:
  - phase: 04-signal-pipeline
    provides: "SignalPipeline.run() returns persisted Signal ORM objects; jobs.py run_signal_scanner()"
provides:
  - "TelegramNotifier class with format_signal, format_outcome, notify_signal, notify_outcome"
  - "telegram_bot_token and telegram_chat_id config settings"
  - "Signal pipeline fires Telegram alerts after persisting signals"
affects: [06-monitoring-and-outcome-tracking, 07-production-deployment]

# Tech tracking
tech-stack:
  added: []  # httpx and tenacity already installed
  patterns:
    - "Fire-and-forget notification pattern: try/except wrapper that logs but never raises"
    - "asyncio.Lock rate limiter: 1 msg/sec enforcement without external library"
    - "Retry decorator on async HTTP: tenacity @retry with httpx exception types"

key-files:
  created:
    - "app/services/telegram_notifier.py"
  modified:
    - "app/config.py"
    - "app/workers/jobs.py"

key-decisions:
  - "HTML parse mode for all Telegram messages (avoids MarkdownV2 escaping nightmares with gold prices)"
  - "Notification wiring in jobs.py (not signal_pipeline.py) to keep pipeline focused on signal generation"
  - "Strategy name lookup via session.get() in jobs.py rather than passing through pipeline"
  - "notify_outcome() built and tested but NOT wired -- Phase 6 builds outcome detection that calls it"
  - "P&L display uses HTML entity &amp; for ampersand in Telegram HTML mode"

patterns-established:
  - "Fire-and-forget notify pattern: check enabled, try format+send, except log+return"
  - "Optional config with empty string defaults for graceful disabled mode"

# Metrics
duration: 2min
completed: 2026-02-17
---

# Phase 5 Plan 01: Telegram Notification Service Summary

**TelegramNotifier with HTML signal/outcome formatting, tenacity retry (3x exponential), asyncio rate limiter (1 msg/sec), wired into signal scanner job as fire-and-forget**

## Performance

- **Duration:** 2 min
- **Started:** 2026-02-17T22:17:28Z
- **Completed:** 2026-02-17T22:20:09Z
- **Tasks:** 2
- **Files modified:** 3

## Accomplishments
- TelegramNotifier class with format_signal (direction arrow, entry/SL/TP/R:R/confidence/strategy/reasoning) and format_outcome (result emoji, entry/exit/P&L/duration) HTML formatters
- Retry with exponential backoff (3 attempts, 2-10s) via tenacity on HTTPStatusError and ConnectError
- Rate limiter via asyncio.Lock enforcing max 1 message per second to same chat
- Signal scanner job sends notifications for each new signal after pipeline.run() completes
- Disabled mode: system works normally when TELEGRAM_BOT_TOKEN is empty (returns early with debug log)

## Task Commits

Each task was committed atomically:

1. **Task 1: Create TelegramNotifier service** - `bca41bb` (feat)
2. **Task 2: Wire TelegramNotifier into signal pipeline** - `683f1ad` (feat)

## Files Created/Modified
- `app/services/telegram_notifier.py` - TelegramNotifier class with formatting, retry, rate limiting, fire-and-forget wrappers
- `app/config.py` - Added telegram_bot_token and telegram_chat_id optional settings (empty string defaults)
- `app/workers/jobs.py` - Added notification logic after pipeline.run() in run_signal_scanner()

## Decisions Made
- HTML parse mode exclusively -- gold prices like 2650.50 would need escaping in MarkdownV2
- Notification wiring in jobs.py rather than signal_pipeline.py -- keeps the pipeline focused on signal generation and avoids coupling it to delivery channels
- Strategy name lookup via session.get(StrategyModel, sig.strategy_id) in jobs.py for each signal, with dict caching per strategy_id to avoid redundant queries
- notify_outcome() is built and tested but NOT wired into any job -- Phase 6 builds outcome detection (TP/SL hit checker) which will call it
- Used HTML entity `&amp;` for P&L ampersand to be safe in Telegram HTML parse mode

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered
- Pre-existing test failure in `test_signal_pipeline.py::test_pipeline_risk_rejects_all` (mock coroutine not awaited in `_compute_atr`) -- not caused by 05-01 changes, verified by running against prior commit

## User Setup Required

**External services require manual configuration.** Telegram bot credentials must be configured before notifications will send:

| Variable | Source |
|----------|--------|
| `TELEGRAM_BOT_TOKEN` | Telegram @BotFather -> /newbot -> copy token |
| `TELEGRAM_CHAT_ID` | Send message to bot, then GET `https://api.telegram.org/bot<TOKEN>/getUpdates` -> `result.message.chat.id` |

System works normally without these configured (disabled mode).

## Next Phase Readiness
- TelegramNotifier is ready for outcome notifications once Phase 6 builds the outcome detection job
- notify_outcome() is importable and tested, just needs to be called from the outcome checker
- Pre-existing test failure in test_pipeline_risk_rejects_all should be investigated (mock issue, not related to this plan)

---
*Phase: 05-delivery-and-visibility*
*Completed: 2026-02-17*
