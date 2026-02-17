# Phase 7: Production Hardening - Context

**Gathered:** 2026-02-17
**Status:** Ready for planning

<domain>
## Phase Boundary

Deploy the GoldSignal system to Railway for unattended 24/7 operation. Enforce data retention policies to manage storage growth. Ensure graceful recovery from transient failures (API outages, DB hiccups) without losing signal state or generating duplicates. Add monitoring and alerting for operational visibility.

</domain>

<decisions>
## Implementation Decisions

### Railway deployment setup
- User already has a Railway account and project
- Auto-deploy on push to main branch
- PostgreSQL: Claude's discretion (Railway plugin is simplest)
- Build approach: Claude's discretion (Dockerfile vs Nixpacks based on project structure)
- All environment variables (TWELVE_DATA_API_KEY, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, DATABASE_URL) configured in Railway dashboard

### Failure recovery behavior
- Twelve Data API outage: send Telegram alert after 3+ consecutive failures (not silent)
- Database connection drop: skip the current job cycle, log error, retry on next scheduled tick (no in-cycle retry)
- After Railway restart (deploy/crash): Claude's discretion on whether to catch-up or accept the gap
- Keep-alive strategy: Claude's discretion based on Railway best practices

### Monitoring and alerting
- System alerts via Telegram for: stale data feed (no candles for 2+ expected intervals), consecutive API failures (3+), scheduler jobs not running
- Daily health digest: Claude's discretion
- /status API endpoint: yes, detailed -- shows scheduler jobs, last data fetch, active signals, DB connection status
- Log format: keep human-readable loguru format (not JSON) -- easy to read in Railway logs dashboard

### Claude's Discretion
- Dockerfile vs Nixpacks build approach
- Railway PostgreSQL plugin vs external provider (simplest wins)
- Health check ping vs process-only keep-alive
- Whether to run catch-up cycle after restart or accept the gap
- Whether to include a daily Telegram health digest
- Data retention thresholds and pruning schedule (not discussed -- user skipped this area)

</decisions>

<specifics>
## Specific Ideas

No specific requirements -- open to standard approaches. User wants reliable 24/7 operation with visibility into system health when things go wrong.

</specifics>

<deferred>
## Deferred Ideas

None -- discussion stayed within phase scope.

</deferred>

---

*Phase: 07-production-hardening*
*Context gathered: 2026-02-17*
