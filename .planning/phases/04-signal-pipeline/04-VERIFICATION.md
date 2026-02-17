---
phase: 04-signal-pipeline
verified: 2026-02-17T18:30:00Z
status: gaps_found
score: 3/5 must-haves verified
gaps:
  - truth: "Generated signals include all required fields with ATR-based SL/TP distances, and the generator can load strategies at runtime"
    status: failed
    reason: "signal_generator.py imports two non-existent strategy modules (fvg_reentry, ema_momentum) on lines 70-71. This causes an ImportError at runtime whenever generate() is called, blocking all signal generation."
    artifacts:
      - path: "app/services/signal_generator.py"
        issue: "Lines 70-71 import app.strategies.fvg_reentry and app.strategies.ema_momentum which do not exist in app/strategies/"
    missing:
      - "Remove or replace the two non-existent imports on lines 70-71 of signal_generator.py"
      - "The existing three strategies (liquidity_sweep, trend_continuation, breakout_expansion) should be imported instead, matching the pattern used in jobs.py"

  - truth: "Risk management enforces per-trade risk limits, maximum concurrent signals, daily loss limits, and volatility-adjusted position sizing"
    status: partial
    reason: "Per-trade risk, concurrent limit, and daily loss limit are fully implemented. However, volatility-adjusted position sizing (RISK-06) uses hardcoded ATR values of 1.0/1.0 in risk_manager.check() — the SignalPipeline never passes real ATR candle data to the check() call, so the ATR adjustment factor always evaluates to 1.0x, nullifying the volatility scaling."
    artifacts:
      - path: "app/services/risk_manager.py"
        issue: "Lines 137-140: calculate_position_size() is called with current_atr=1.0, baseline_atr=1.0 hardcoded defaults. The NOTE comment on lines 134-136 confirms this is a known incomplete state."
      - path: "app/services/signal_pipeline.py"
        issue: "The run() method calls risk_manager.check(session, validated) without computing or passing ATR values, so real volatility adjustment never occurs."
    missing:
      - "Compute current ATR from H1 candle data inside SignalPipeline.run() before the risk check step"
      - "Compute baseline ATR (e.g., mean ATR over 30 days) for normalization"
      - "Pass current_atr and baseline_atr to risk_manager.check() or calculate_position_size() directly"
---

# Phase 4: Signal Pipeline Verification Report

**Phase Goal:** The system automatically selects the best-performing strategy, generates validated trade signals with risk management, and accounts for gold-specific market behavior
**Verified:** 2026-02-17T18:30:00Z
**Status:** gaps_found
**Re-verification:** No — initial verification

## Goal Achievement

### Observable Truths

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | Strategy selector ranks strategies using weighted backtest metrics and current volatility regime, and selects the highest-scoring strategy for signal generation | VERIFIED | `strategy_selector.py` (542 lines): composite scoring with 5 weighted metrics (win rate 0.30, profit factor 0.25, Sharpe 0.15, expectancy 0.15, drawdown 0.15), ATR percentile regime detection (25th/75th thresholds on 720 H1 candles), regime modifiers (-10% on breakout_expansion in HIGH vol, trend_continuation in LOW vol), degradation detection and deprioritization, minimum 50-trade enforcement, H4 EMA-50/200 confluence check |
| 2 | Generated signals include all required fields (direction, entry, SL, TP1, TP2, R:R, confidence, strategy name, reasoning) with ATR-based SL/TP distances | FAILED | Signal model and CandidateSignal both contain all required fields. Strategies use ATR for SL/TP (verified in liquidity_sweep.py). BUT: `signal_generator.py` lines 70-71 import `app.strategies.fvg_reentry` and `app.strategies.ema_momentum` — neither file exists in `app/strategies/`. This causes ImportError at runtime when generate() is called. |
| 3 | Signals below minimum R:R (1:2) or minimum confidence (65%) are automatically rejected, and duplicate signals for the same direction within the dedup window are suppressed | VERIFIED | `signal_generator.py`: MIN_RR=2.0, MIN_CONFIDENCE=65.0, DEDUP_WINDOW_HOURS=4. Validation pipeline applies these filters in order with continue on failure. Dedup queries Signal table for active same-symbol/direction signals within 4h window. Directional bias detection (75% threshold over 20 signals) annotates but does not reject. |
| 4 | Risk management enforces per-trade risk limits, maximum concurrent signals, daily loss limits, and volatility-adjusted position sizing | PARTIAL | Per-trade 1%, MAX_CONCURRENT_SIGNALS=2, DAILY_LOSS_LIMIT_PCT=2% all implemented and wired. Drawdown tracking via get_drawdown_metrics() implemented. BUT: calculate_position_size() is called with hardcoded current_atr=1.0, baseline_atr=1.0 — the pipeline never computes or passes real ATR values, so ATR factor always = 1.0x (RISK-06 effectively non-functional). |
| 5 | System identifies current gold trading session (Asian/London/NY/overlap) and enriches signals with session metadata and overlap confidence boost | VERIFIED | `gold_intelligence.py` (349 lines): GoldIntelligence.get_session_info() uses existing session_filter.get_active_sessions() helper (Asian 23:00-08:00, London 07:00-16:00, NY 12:00-21:00, overlap 12:00-16:00 UTC). enrich() applies +5 confidence boost during overlap, attaches session label. DXY rolling Pearson correlation (30-period) with full graceful degradation. All wired into SignalPipeline.run() steps 7-8. |

**Score:** 3/5 truths verified (Truths 1, 3, 5 pass; Truth 2 fails on broken imports; Truth 4 partial on ATR wiring gap)

---

### Required Artifacts

| Artifact | Expected | Exists | Lines | Stubs | Wired | Status |
|----------|----------|--------|-------|-------|-------|--------|
| `app/services/strategy_selector.py` | Composite scoring, regime detection, degradation | YES | 542 | None | YES — imported by signal_pipeline.py | VERIFIED |
| `app/services/signal_generator.py` | R:R/confidence validation, dedup, expiry | YES | 328 | None | YES — imported by signal_pipeline.py | VERIFIED (broken import in generate()) |
| `app/services/risk_manager.py` | 1% risk, 2 concurrent cap, 2% daily loss, ATR sizing | YES | 355 | None (ATR defaults documented) | YES — imported by signal_pipeline.py | PARTIAL (ATR defaults unwired) |
| `app/services/gold_intelligence.py` | Session ID, overlap boost, DXY correlation | YES | 349 | None | YES — imported by signal_pipeline.py | VERIFIED |
| `app/services/signal_pipeline.py` | Orchestrator wiring all 4 services | YES | 202 | None | YES — called by jobs.run_signal_scanner() | VERIFIED |
| `app/workers/jobs.py` (run_signal_scanner) | Hourly scanner with stale data guard | YES | 306 | None | YES — registered in scheduler.py | VERIFIED |
| `app/workers/scheduler.py` (signal scanner job) | :02 every hour cron registration | YES | — | None | YES — imports run_signal_scanner | VERIFIED |
| `tests/test_signal_pipeline.py` | 7 integration tests | YES | 253 | None | N/A | VERIFIED (7 tests confirmed) |
| `app/strategies/fvg_reentry.py` | Strategy module referenced by signal_generator | NO | — | — | — | MISSING |
| `app/strategies/ema_momentum.py` | Strategy module referenced by signal_generator | NO | — | — | — | MISSING |

---

### Key Link Verification

| From | To | Via | Status | Details |
|------|----|-----|--------|---------|
| `signal_pipeline.py` | `strategy_selector.py` | `selector.select_best(session)` | WIRED | Line 75 in signal_pipeline.py |
| `signal_pipeline.py` | `signal_generator.py` | `generator.generate()`, `generator.validate()`, `generator.expire_stale_signals()` | WIRED | Lines 71, 86, 94 |
| `signal_pipeline.py` | `risk_manager.py` | `risk_manager.check(session, validated)` | WIRED (partial) | Line 100 — called but without real ATR values |
| `signal_pipeline.py` | `gold_intelligence.py` | `gold_intel.get_dxy_correlation()`, `gold_intel.enrich()` | WIRED | Lines 144, 147 |
| `signal_pipeline.py` | `strategy_selector.py` H4 | `selector.check_h4_confluence(session, direction)` | WIRED | Lines 123-135 (H4 boost applied) |
| `jobs.py` | `signal_pipeline.py` | `run_signal_scanner()` instantiates and calls `pipeline.run(session)` | WIRED | Lines 292-298 |
| `scheduler.py` | `jobs.run_signal_scanner` | `CronTrigger(minute=2)` | WIRED | Lines 89-96 (6th registered job, hourly at :02) |
| `signal_generator.generate()` | `app.strategies.fvg_reentry` | `import app.strategies.fvg_reentry` line 70 | NOT WIRED | File does not exist — ImportError at runtime |
| `signal_generator.generate()` | `app.strategies.ema_momentum` | `import app.strategies.ema_momentum` line 71 | NOT WIRED | File does not exist — ImportError at runtime |
| `risk_manager.check()` | Real ATR values | ATR computed from candle data in pipeline | NOT WIRED | Pipeline calls check() with current_atr=1.0, baseline_atr=1.0 hardcoded |

---

### Requirements Coverage

| Requirement | Status | Blocking Issue |
|-------------|--------|----------------|
| SEL-01: Weighted metric scoring | SATISFIED | Implemented in strategy_selector._compute_scores() with METRIC_WEIGHTS |
| SEL-02: ATR volatility regime detection | SATISFIED | _detect_volatility_regime() using ATR percentile on 720 H1 candles |
| SEL-03: Regime factors into selection | SATISFIED | _apply_regime_modifier() adjusts composite scores |
| SEL-04: Multi-timeframe confluence | SATISFIED | check_h4_confluence() checks EMA-50 vs EMA-200 on H4 candles |
| SEL-05: Degradation detection | SATISFIED | _check_degradation() flags win rate drop >15% and profit factor <1.0 |
| SEL-06: Auto-deprioritize degraded | SATISFIED | Degraded strategies sorted after non-degraded in select_best() |
| SEL-07: Minimum sample size | SATISFIED (stricter) | MIN_TRADES=50 enforced (requirement says 30+; implementation uses 50) |
| SIG-01: Required signal fields | SATISFIED | Signal model + CandidateSignal contain all required fields |
| SIG-02: ATR-based SL/TP | SATISFIED | Strategies (liquidity_sweep verified) use ATR multipliers for SL/TP |
| SIG-03: Minimum R:R filter | SATISFIED (stricter) | MIN_RR=2.0 (requirement: 1.5; success criteria: 2.0; code uses 2.0) |
| SIG-04: Minimum confidence threshold | SATISFIED (stricter) | MIN_CONFIDENCE=65.0 (requirement: 60%; success criteria: 65%; code uses 65%) |
| SIG-05: Signal deduplication | SATISFIED | 4h window dedup by symbol + direction + active status |
| SIG-06: Signal expiry | SATISFIED | compute_expiry() with timeframe-specific hours (M15=4h, H1=8h, H4=24h, D1=48h) |
| SIG-07: Directional bias detection | SATISFIED | _check_directional_bias() over 20 recent signals, 75% threshold |
| SIG-08: Scanner no-ops on stale data | SATISFIED | Module-level _last_scanned_ts guard in run_signal_scanner() |
| RISK-01: Per-trade risk limit | SATISFIED | RISK_PER_TRADE=0.01 (1%) of account_balance |
| RISK-02: Max concurrent signals | SATISFIED | MAX_CONCURRENT_SIGNALS=2 enforced in _check_concurrent_limit() |
| RISK-03: Position sizing calculator | SATISFIED | calculate_position_size() implemented with SL distance and risk% |
| RISK-04: Daily loss limit | SATISFIED | DAILY_LOSS_LIMIT_PCT=2% enforced via DB query on outcomes |
| RISK-05: Drawdown monitoring | SATISFIED | get_drawdown_metrics() computes running and max drawdown from outcomes |
| RISK-06: Volatility-adjusted position sizing | BLOCKED | calculate_position_size() has correct ATR formula but called with 1.0/1.0 defaults — pipeline does not pass real ATR values |
| GOLD-01: Session identification | SATISFIED | GoldIntelligence.get_session_info() identifies Asian/London/NY/overlap |
| GOLD-02: Session-based signal filtering | SATISFIED (no suppression by design) | Per CONTEXT.md: all sessions allowed; overlap gets +5 boost instead |
| GOLD-03: Session volatility profiles | SATISFIED | get_session_volatility_profile() provides qualitative descriptions |
| GOLD-04: DXY correlation monitoring | SATISFIED | get_dxy_correlation() with 30-period rolling Pearson correlation and graceful degradation |

---

### Anti-Patterns Found

| File | Line | Pattern | Severity | Impact |
|------|------|---------|----------|--------|
| `app/services/signal_generator.py` | 70-71 | Non-existent module imports: `app.strategies.fvg_reentry`, `app.strategies.ema_momentum` | BLOCKER | ImportError at runtime in generate() — blocks all signal generation |
| `app/services/risk_manager.py` | 134-140 | Hardcoded ATR defaults 1.0/1.0 with NOTE comment acknowledging they should come from caller | WARNING | RISK-06 volatility-adjusted position sizing effectively disabled |

---

### Gaps Summary

**Gap 1 (Blocker): Non-existent strategy imports in signal_generator.py**

`signal_generator.generate()` performs lazy imports to trigger strategy registration. At lines 70-71, it imports `app.strategies.fvg_reentry` and `app.strategies.ema_momentum`. Neither file exists — only `liquidity_sweep.py`, `trend_continuation.py`, and `breakout_expansion.py` exist in `app/strategies/`. When `generate()` is called at runtime, it will raise `ModuleNotFoundError`, preventing any signal from being generated. The fix is to replace lines 70-71 with the three existing strategies (matching the import pattern already established in `jobs.run_daily_backtests()`).

**Gap 2 (Warning): Volatility-adjusted position sizing not wired to real ATR**

The `calculate_position_size()` implementation in `risk_manager.py` is correct — it computes `atr_factor = baseline_atr / current_atr` clamped to [0.5, 1.5]. However, `risk_manager.check()` always calls it with `current_atr=1.0, baseline_atr=1.0`, producing an `atr_factor` of exactly 1.0 always. The `SignalPipeline.run()` method does not compute ATR from candle data before invoking the risk check. RISK-06 (volatility-adjusted position sizing) is implemented but not connected to actual market data. A comment in the code acknowledges this: "SignalPipeline will provide real ATR when integrated."

The system will run the signal pipeline hourly and perform strategy selection, validation, session enrichment, and risk limit enforcement — but no signal will ever be generated due to Gap 1's ImportError.

---

_Verified: 2026-02-17T18:30:00Z_
_Verifier: Claude (gsd-verifier)_
