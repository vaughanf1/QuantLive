# Phase 4: Signal Pipeline - Context

**Gathered:** 2026-02-17
**Status:** Ready for planning

<domain>
## Phase Boundary

The system automatically selects the best-performing strategy, generates validated trade signals with risk management, and accounts for gold-specific market behavior. This phase wires the full pipeline: strategy ranking -> signal generation -> validation filters -> risk management -> gold intelligence. Telegram delivery, outcome tracking, and production deployment are separate phases.

</domain>

<decisions>
## Implementation Decisions

### Strategy Scoring
- Win rate is the primary weight, profit factor close behind — consistency matters most, but big-win potential is important too
- Claude fine-tunes exact metric weights within this spirit (win rate slightly ahead, profit factor close behind, Sharpe and drawdown as secondary)
- Minimum 50 trades in rolling window before trusting a strategy's metrics (higher bar than the 30 in requirements)
- Volatility regime detection: Claude's discretion on regime-strategy mapping approach
- Multi-timeframe confluence: Claude designs the approach

### Signal Validation Rules
- Minimum R:R ratio raised to 1:2 (stricter than the 1:1.5 in requirements)
- Minimum confidence threshold set to 65% (slightly above the 60% requirement)
- Dedup window duration: Claude's discretion
- Signal expiry logic: Claude's discretion
- Directional bias detection (SIG-07): as specified in requirements

### Risk Management
- Per-trade risk: 1% of account — conservative, prop-firm safe
- Maximum concurrent active signals: 2
- Daily loss limit: 2% drawdown — tight capital protection, then suppress signals for the day
- Position sizing volatility adjustment: Claude's discretion (ATR-scaled or alternative)

### Session Filtering
- No session-based signal suppression — allow signals in all sessions including Asian
- Let strategy quality and backtest metrics speak for themselves rather than blocking by session
- London/NY overlap boost: Claude's discretion
- DXY correlation monitoring: Claude designs the integration approach
- Session-specific volatility profiles: Claude assesses whether they add value

### Claude's Discretion
- Exact metric weight percentages (within win-rate-first, profit-factor-close-second spirit)
- Volatility regime classification thresholds and strategy mapping
- Multi-timeframe confluence scoring design
- Dedup window duration
- Signal expiry times (intraday vs swing differentiation)
- Position sizing volatility adjustment model
- DXY divergence handling approach
- Session volatility profile usefulness assessment
- London/NY overlap confidence adjustment

</decisions>

<specifics>
## Specific Ideas

- User is trading on a prop firm account — capital protection is critical (1% risk, 2% daily limit)
- Consistency (win rate) is the top priority, but profitable trades matter too — not a pure scalping mentality
- Higher quality bar than requirements specify: R:R 1:2 (not 1:1.5), confidence 65% (not 60%), 50 trades minimum (not 30)
- No session blocking — user wants to capture opportunities in all sessions

</specifics>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope

</deferred>

---

*Phase: 04-signal-pipeline*
*Context gathered: 2026-02-17*
