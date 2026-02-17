# Phase 7: Production Hardening - Research

**Researched:** 2026-02-17
**Domain:** Railway deployment, data retention, failure recovery, operational monitoring
**Confidence:** HIGH

## Summary

This phase deploys the GoldSignal FastAPI application to Railway as a long-running service with PostgreSQL, implements data retention policies for candle data, adds failure recovery with Telegram alerting, and creates a detailed /status endpoint for operational visibility.

The project is a standard Python 3.12 / FastAPI / SQLAlchemy async / APScheduler application with `requirements.txt` dependency management. Railway's Railpack builder (successor to Nixpacks) auto-detects this project structure and builds it without a Dockerfile. However, a Dockerfile provides more control over image size, system dependencies, and reproducibility -- which matters for a 24/7 production trading system. The recommendation is to use a Dockerfile for deterministic builds.

Railway provides a managed PostgreSQL plugin that exposes a `DATABASE_URL` environment variable. The critical gotcha is that Railway's `DATABASE_URL` uses the `postgresql://` scheme, but this project requires `postgresql+asyncpg://` for SQLAlchemy async. The `DATABASE_URL` must be manually constructed in Railway's dashboard using the reference variable syntax with the correct scheme prefix.

**Primary recommendation:** Use a Dockerfile with multi-stage build for deterministic deploys, Railway PostgreSQL plugin for simplicity, `railway.json` for config-as-code, and simple batch DELETE for data retention (partitioning is overkill for ~4K candles/month).

## Standard Stack

The established libraries/tools for this domain:

### Core (Already in Project)
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| FastAPI | >=0.115.0 | Web framework + lifespan management | Already in project |
| uvicorn | >=0.32.0 | ASGI server | Already in project, Railway expects it |
| SQLAlchemy | >=2.0.36 | Async ORM with PostgreSQL | Already in project |
| asyncpg | >=0.30.0 | Async PostgreSQL driver | Already in project |
| APScheduler | >=3.10.4,<4.0 | Background job scheduling | Already in project, MemoryJobStore |
| Alembic | >=1.14.0 | Database migrations | Already in project |
| loguru | >=0.7.3 | Human-readable logging | Already in project |
| tenacity | >=9.0.0 | Retry logic for API calls | Already in project |
| httpx | >=0.28.0 | Async HTTP client (Telegram) | Already in project |
| pydantic-settings | >=2.7.0 | Environment-based config | Already in project |

### Supporting (No New Dependencies Required)
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| Railway PostgreSQL plugin | N/A (managed) | Database hosting | Simplest option, zero-config |
| Railway Railpack / Dockerfile | N/A | Build system | Railpack auto-detects, Dockerfile for control |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| Dockerfile | Railpack (auto-detect) | Railpack is simpler but less deterministic; Dockerfile gives full control over Python version, system deps, image size |
| Railway PostgreSQL | External (Supabase, Neon) | External adds latency; Railway plugin is same-network, zero config |
| Simple batch DELETE | pg_partman partitioning | Partitioning is overkill for ~4K rows/month; adds complexity for no benefit at this scale |

**Installation:**
No new pip dependencies required. All operational tooling uses existing libraries.

## Architecture Patterns

### Railway Deployment Structure
```
project-root/
  Dockerfile              # Multi-stage build for production
  railway.json            # Config-as-code: start command, healthcheck, restart policy, pre-deploy
  .python-version         # Pin Python 3.12 for Railpack fallback
  requirements.txt        # Already exists
  alembic/                # Already exists
  alembic.ini             # Already exists
  app/
    main.py               # Already exists (FastAPI + lifespan)
    config.py             # Already exists (pydantic-settings)
    database.py           # Already exists (async engine)
    workers/
      scheduler.py        # Already exists (APScheduler jobs)
      jobs.py             # Already exists (job functions) -- ADD data retention job
    services/
      telegram_notifier.py  # Already exists -- ADD system alert methods
      data_retention.py     # NEW: retention policy enforcement
    api/
      health.py           # Already exists -- EXTEND to /status with detailed info
```

### Pattern 1: railway.json Config-as-Code
**What:** Define deployment configuration in a `railway.json` file committed to the repo, so Railway reads it automatically.
**When to use:** Always -- ensures deployment config is version-controlled.
**Example:**
```json
{
  "$schema": "https://railway.com/railway.schema.json",
  "build": {
    "builder": "DOCKERFILE",
    "dockerfilePath": "Dockerfile"
  },
  "deploy": {
    "startCommand": "uvicorn app.main:app --host 0.0.0.0 --port $PORT",
    "healthcheckPath": "/health",
    "healthcheckTimeout": 120,
    "restartPolicyType": "ALWAYS",
    "restartPolicyMaxRetries": 10,
    "preDeployCommand": "alembic upgrade head"
  }
}
```
Source: https://docs.railway.com/reference/config-as-code

### Pattern 2: Multi-Stage Dockerfile
**What:** Use a multi-stage Dockerfile to produce a small, reproducible image.
**When to use:** For production deployments where image size and build reproducibility matter.
**Example:**
```dockerfile
# Stage 1: Build dependencies
FROM python:3.12-slim AS builder

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir --target=/deps -r requirements.txt

# Stage 2: Production image
FROM python:3.12-slim

WORKDIR /app

# Copy installed dependencies
COPY --from=builder /deps /usr/local/lib/python3.12/site-packages

# Copy application code
COPY alembic/ alembic/
COPY alembic.ini .
COPY app/ app/

# Railway injects PORT env var
EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```
Note: The `startCommand` in railway.json overrides CMD, and uses `$PORT` which Railway injects. The Dockerfile CMD is a fallback for local testing.

### Pattern 3: DATABASE_URL Construction for asyncpg
**What:** Railway's PostgreSQL plugin provides `DATABASE_URL` with `postgresql://` scheme. SQLAlchemy async requires `postgresql+asyncpg://`. Must construct the correct URL.
**When to use:** Always when connecting from a Railway app service to Railway PostgreSQL.
**How to configure:**
In Railway dashboard, on the app service, set variable:
```
DATABASE_URL=postgresql+asyncpg://${{Postgres.PGUSER}}:${{Postgres.PGPASSWORD}}@${{Postgres.PGHOST}}:${{Postgres.PGPORT}}/${{Postgres.PGDATABASE}}
```
This uses Railway's reference variable syntax to pull values from the Postgres service and construct the asyncpg-compatible URL. The app's existing `config.py` reads `DATABASE_URL` directly -- no code changes needed.

**Alternative approach (code-side):** Modify `config.py` to replace the URL scheme:
```python
@property
def async_database_url(self) -> str:
    return self.database_url.replace("postgresql://", "postgresql+asyncpg://", 1)
```
The dashboard approach is cleaner because it avoids conditional logic in application code.

### Pattern 4: APScheduler MemoryJobStore Recovery
**What:** The project uses MemoryJobStore -- all jobs are lost on restart. Jobs are re-registered in `register_jobs()` called from the FastAPI lifespan. This is correct.
**When to use:** Always with MemoryJobStore.
**Key behavior:** After a Railway restart/redeploy, the scheduler starts fresh. `misfire_grace_time=300` means if a job was due within the last 5 minutes, it will still fire. `coalesce=True` means multiple missed runs collapse into one execution. This is already configured correctly in `scheduler.py`.

### Pattern 5: Batch DELETE for Data Retention
**What:** Scheduled job that deletes old candle data based on timeframe-specific age thresholds.
**When to use:** For modest data volumes (under 100K rows/month).
**Example:**
```python
from datetime import datetime, timedelta, timezone
from sqlalchemy import delete
from app.models.candle import Candle

RETENTION_POLICY = {
    "M15": timedelta(days=90),
    "H1": timedelta(days=365),
    # H4 and D1: kept indefinitely (not in this dict)
}

async def prune_candles(session):
    now = datetime.now(timezone.utc)
    total_deleted = 0
    for timeframe, max_age in RETENTION_POLICY.items():
        cutoff = now - max_age
        stmt = (
            delete(Candle)
            .where(Candle.timeframe == timeframe)
            .where(Candle.timestamp < cutoff)
        )
        result = await session.execute(stmt)
        total_deleted += result.rowcount
    await session.commit()
    return total_deleted
```

### Anti-Patterns to Avoid
- **Running Alembic migrations from application startup code:** Use Railway's `preDeployCommand` instead. The pre-deploy runs in a separate container before the app starts, preventing race conditions in multi-replica scenarios and ensuring the app only starts with a migrated database.
- **Using `postgresql://` URL scheme with asyncpg:** Will cause `ValueError: invalid URL scheme`. Always use `postgresql+asyncpg://`.
- **Hardcoding PORT:** Railway injects `PORT` as an environment variable. Always use `$PORT` in the start command.
- **Running uvicorn with multiple workers alongside APScheduler:** Multiple uvicorn workers would each start their own APScheduler instance, causing duplicate job execution. Use `--workers 1` (the default). Railway's single-process model is correct for this app.
- **Storing APScheduler jobs in the database for "persistence":** With MemoryJobStore + `register_jobs()` on startup, jobs are always recreated from code. A persistent store adds complexity without benefit since job definitions live in code, not in data.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Deployment configuration | Custom shell scripts or Procfile | `railway.json` config-as-code | Railway reads it natively, version-controlled, supports healthcheck/restart/pre-deploy |
| Database migrations on deploy | Manual SSH or startup code | `preDeployCommand: "alembic upgrade head"` | Runs before app starts, blocks deploy on failure, separate container |
| Process restart after crash | Custom supervisor/watchdog | Railway `restartPolicyType: "ALWAYS"` | Platform-native, handles backoff, no extra code |
| Health check ping service | Custom cron ping or external service | Railway's built-in `healthcheckPath` | Used at deploy time to verify readiness |
| DATABASE_URL scheme conversion | Code-side string replacement | Railway dashboard variable with `postgresql+asyncpg://` prefix | Keeps application code environment-agnostic |

**Key insight:** Railway provides platform primitives for deployment, restart, health checks, and pre-deploy commands. The application code should focus on business logic and operational alerting, not infrastructure management.

## Common Pitfalls

### Pitfall 1: Railway DATABASE_URL Scheme Mismatch
**What goes wrong:** Railway PostgreSQL provides `DATABASE_URL` with `postgresql://` scheme. SQLAlchemy async + asyncpg requires `postgresql+asyncpg://`. App fails to start with `ValueError: invalid URL scheme`.
**Why it happens:** Railway follows the standard `postgresql://` convention. asyncpg is Python-specific.
**How to avoid:** Construct DATABASE_URL in Railway dashboard with explicit `postgresql+asyncpg://` prefix using reference variables: `postgresql+asyncpg://${{Postgres.PGUSER}}:${{Postgres.PGPASSWORD}}@${{Postgres.PGHOST}}:${{Postgres.PGPORT}}/${{Postgres.PGDATABASE}}`
**Warning signs:** App crashes immediately on startup with connection error.

### Pitfall 2: Multiple Uvicorn Workers + APScheduler Duplication
**What goes wrong:** Running `uvicorn --workers N` (N > 1) causes each worker to execute the lifespan, starting N scheduler instances, running all jobs N times.
**Why it happens:** APScheduler uses MemoryJobStore (no cross-process coordination). Each process gets its own scheduler.
**How to avoid:** Always run with 1 worker (uvicorn default). The app is I/O-bound (API calls, DB queries), so async concurrency within a single process is sufficient.
**Warning signs:** Duplicate signals, duplicate Telegram notifications, API rate limit exhaustion.

### Pitfall 3: Pre-Deploy Command Assuming Filesystem Persistence
**What goes wrong:** Pre-deploy command writes files expecting the app to read them. But pre-deploy runs in a separate container.
**Why it happens:** Railway's `preDeployCommand` executes in an isolated container. Filesystem changes are discarded.
**How to avoid:** Only use pre-deploy for stateless operations like `alembic upgrade head` that write to external stores (database).
**Warning signs:** Files created in pre-deploy are missing when app starts.

### Pitfall 4: Healthcheck Endpoint With Heavy Queries
**What goes wrong:** Making the `/health` endpoint run expensive database queries. Railway calls it at deploy time, and external monitors may call it frequently.
**Why it happens:** Temptation to add comprehensive checks to the health endpoint.
**How to avoid:** Keep `/health` lightweight (SELECT 1). Put detailed diagnostics in `/status` which is only called on-demand.
**Warning signs:** Slow deploy healthcheck verification, database connection pool exhaustion.

### Pitfall 5: Not Handling Railway's Private vs Public Networking
**What goes wrong:** App uses public DATABASE_URL from within Railway's network, causing ECONNREFUSED or high latency.
**Why it happens:** Recent Railway change: `DATABASE_URL` now defaults to the private network URL. Older docs may reference different behavior.
**How to avoid:** Use the private network connection (Railway's default `DATABASE_URL` now points to private). Verify the app connects successfully in Railway logs.
**Warning signs:** Connection timeout, ECONNREFUSED errors in logs.

### Pitfall 6: Alembic env.py Using Wrong DATABASE_URL for Pre-Deploy
**What goes wrong:** Alembic's `env.py` reads from `get_settings()` which reads `.env` file. In Railway pre-deploy, there is no `.env` file -- only environment variables.
**Why it happens:** `pydantic-settings` reads env vars first, then falls back to `.env`. But `.env` is in `.gitignore` so it won't be in the Docker image.
**How to avoid:** This actually works correctly. `pydantic-settings` reads OS environment variables (which Railway injects) with higher priority than `.env`. Since `.env` is gitignored and not in the image, `DATABASE_URL` from Railway's environment will be used. No changes needed.
**Warning signs:** None -- this is a non-issue but worth verifying during testing.

### Pitfall 7: Data Retention Job Deleting Currently-Referenced Data
**What goes wrong:** Retention job deletes M15 candles that an active signal or in-progress backtest still references.
**Why it happens:** Candles are referenced indirectly (by querying for candles matching a timeframe/symbol/time range), not by foreign key.
**How to avoid:** Set retention thresholds conservatively (90 days for M15 is safe since backtests use 30/60-day windows on H1 data, not M15). Run retention job during off-peak hours (e.g., 03:00 UTC).
**Warning signs:** Backtests or signal scans fail with insufficient candle data.

## Code Examples

### railway.json -- Complete Config
```json
{
  "$schema": "https://railway.com/railway.schema.json",
  "build": {
    "builder": "DOCKERFILE",
    "dockerfilePath": "Dockerfile"
  },
  "deploy": {
    "startCommand": "uvicorn app.main:app --host 0.0.0.0 --port $PORT",
    "healthcheckPath": "/health",
    "healthcheckTimeout": 120,
    "restartPolicyType": "ALWAYS",
    "restartPolicyMaxRetries": 10,
    "preDeployCommand": "alembic upgrade head"
  }
}
```
Source: https://docs.railway.com/reference/config-as-code

### Dockerfile -- Production Multi-Stage
```dockerfile
FROM python:3.12-slim AS builder

WORKDIR /build
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

FROM python:3.12-slim

WORKDIR /app

# Copy Python packages from builder
COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Copy application
COPY alembic.ini .
COPY alembic/ alembic/
COPY app/ app/

# Railway injects PORT; default 8000 for local
ENV PORT=8000
EXPOSE $PORT

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

### Data Retention Service
```python
"""Data retention policy enforcement for candle data."""

from datetime import datetime, timedelta, timezone

from loguru import logger
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.candle import Candle

# Retention policy: timeframe -> max age
# H4 and D1 are kept indefinitely (not in this dict)
RETENTION_POLICY: dict[str, timedelta] = {
    "M15": timedelta(days=90),
    "H1": timedelta(days=365),
}


async def enforce_retention(session: AsyncSession) -> dict[str, int]:
    """Delete candle rows older than their timeframe's retention threshold.

    Returns dict mapping timeframe -> number of rows deleted.
    """
    now = datetime.now(timezone.utc)
    results: dict[str, int] = {}

    for timeframe, max_age in RETENTION_POLICY.items():
        cutoff = now - max_age
        stmt = (
            delete(Candle)
            .where(Candle.timeframe == timeframe)
            .where(Candle.timestamp < cutoff)
        )
        result = await session.execute(stmt)
        deleted = result.rowcount
        results[timeframe] = deleted

        if deleted > 0:
            logger.info(
                "Data retention: pruned {count} {tf} candles older than {cutoff}",
                count=deleted,
                tf=timeframe,
                cutoff=cutoff.isoformat(),
            )

    await session.commit()
    return results
```

### System Alert Methods for TelegramNotifier
```python
# Add to existing TelegramNotifier class

async def notify_system_alert(self, title: str, message: str) -> None:
    """Send a system-level alert. Never raises."""
    if not self.enabled:
        return
    try:
        text = f"\u26a0\ufe0f <b>{title}</b>\n\n{message}"
        await self._send_message(text)
        logger.info("Telegram system alert sent: {}", title)
    except Exception:
        logger.exception("Telegram system alert failed: {}", title)
```

### Consecutive Failure Tracking (for API Outage Alerting)
```python
"""Track consecutive failures for alerting thresholds."""

from collections import defaultdict
from datetime import datetime, timezone


class FailureTracker:
    """Tracks consecutive failures per job and triggers alerts at threshold."""

    def __init__(self, threshold: int = 3):
        self.threshold = threshold
        self._counts: dict[str, int] = defaultdict(int)
        self._alerted: dict[str, bool] = defaultdict(bool)

    def record_failure(self, job_id: str) -> bool:
        """Record a failure. Returns True if threshold just crossed."""
        self._counts[job_id] += 1
        if self._counts[job_id] >= self.threshold and not self._alerted[job_id]:
            self._alerted[job_id] = True
            return True  # Trigger alert
        return False

    def record_success(self, job_id: str) -> bool:
        """Record a success. Returns True if recovering from alert state."""
        was_alerted = self._alerted.get(job_id, False)
        self._counts[job_id] = 0
        self._alerted[job_id] = False
        return was_alerted  # True = recovery alert

    def get_count(self, job_id: str) -> int:
        return self._counts.get(job_id, 0)
```

### Enhanced /status Endpoint
```python
from datetime import UTC, datetime
from app.workers.scheduler import scheduler

@router.get("/status")
async def status(session: AsyncSession = Depends(get_session)):
    """Detailed operational status for monitoring."""
    # Database check
    db_ok = True
    try:
        await session.execute(text("SELECT 1"))
    except Exception:
        db_ok = False

    # Scheduler jobs status
    jobs = []
    for job in scheduler.get_jobs():
        jobs.append({
            "id": job.id,
            "name": job.name,
            "next_run": job.next_run_time.isoformat() if job.next_run_time else None,
        })

    return {
        "status": "ok" if db_ok else "degraded",
        "timestamp": datetime.now(UTC).isoformat(),
        "database": "connected" if db_ok else "disconnected",
        "scheduler": {
            "running": scheduler.running,
            "job_count": len(jobs),
            "jobs": jobs,
        },
    }
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Nixpacks (auto-builder) | Railpack (Railway's new builder) | March 2026 | Railpack is now default for new services; smaller images, faster builds. Nixpacks still works but is deprecated. |
| `DATABASE_URL` = public URL | `DATABASE_URL` = private URL | ~2025 | Railway changed naming: DATABASE_URL is now private network. Apps within Railway should use this (lower latency, no egress charges). |
| Procfile for start command | railway.json / railway.toml | 2024+ | Config-as-code is the recommended approach. Procfile still works but railway.json provides healthcheck, restart policy, and pre-deploy in one file. |

**Deprecated/outdated:**
- Nixpacks: Replaced by Railpack as default builder. Still functional but Railway recommends Railpack for new services.
- Procfile: Still supported but `railway.json` is more capable (includes healthcheck, restart policy).

## Discretion Decisions (Research-Backed Recommendations)

### Dockerfile vs Railpack
**Recommendation: Dockerfile.**
Railpack would auto-detect the project (has `requirements.txt` + `app/main.py`) and install dependencies. However, Railpack defaults to Python 3.13.2 and auto-detects the start command based on framework. A Dockerfile gives explicit control over Python version (3.12, matching development), image size (multi-stage slim), and system dependencies. For a 24/7 trading system, determinism matters more than convenience.

### Railway PostgreSQL Plugin vs External
**Recommendation: Railway PostgreSQL plugin.**
Zero configuration, same private network (low latency), no egress charges, automatic environment variable injection. The data volume is modest (<50K rows/year). No need for advanced features from external providers.

### Health Check Ping vs Process-Only Keep-Alive
**Recommendation: Health check ping (`/health` endpoint).**
Railway's `healthcheckPath` is only called at deploy time, not continuously. For ongoing monitoring, the app should remain alive because it is a long-running FastAPI process serving HTTP (uvicorn keeps the event loop alive). The `restartPolicyType: "ALWAYS"` handles crash recovery. No external keep-alive ping needed.

### Catch-Up After Restart vs Accept Gap
**Recommendation: Accept the gap.**
APScheduler with `misfire_grace_time=300` and `coalesce=True` already handles the common case: if the restart takes under 5 minutes, missed jobs fire once on recovery. For longer outages, the CronTrigger jobs will run at their next scheduled time and fetch the latest data. The candle ingestor already fetches recent history (not just the latest candle), so gaps fill naturally. Building a custom catch-up mechanism adds complexity with minimal benefit.

### Daily Telegram Health Digest
**Recommendation: Yes, include it.**
A daily summary at 06:00 UTC (before London session) provides confidence the system is running. Content: last successful data fetch per timeframe, count of active signals, database row counts, scheduler status. Inexpensive to implement (one scheduled job, one Telegram message). Provides passive assurance without requiring active checking.

### Data Retention Thresholds and Pruning Schedule
**Recommendation:**
- M15 candles: prune after 90 days (~8,640 rows max in table)
- H1 candles: prune after 365 days (~8,640 rows max)
- H4 candles: keep indefinitely (~2,160 rows/year -- negligible)
- D1 candles: keep indefinitely (~365 rows/year -- negligible)
- Backtest results: prune after 180 days (they regenerate daily)
- Signals + Outcomes: keep indefinitely (low volume, valuable history)
- Schedule: Run retention job daily at 03:00 UTC (off-peak, after daily backtests at 02:00 UTC)

## Open Questions

1. **Railway plan tier and `restartPolicyType: "ALWAYS"`**
   - What we know: Free/trial plans cannot use `ALWAYS` restart policy and cap `ON_FAILURE` at 10 restarts. Paid plans have unlimited restarts with all policies.
   - What's unclear: Whether the user's Railway account is on a paid plan.
   - Recommendation: Use `ALWAYS` in railway.json. If the user is on a free plan, fall back to `ON_FAILURE` with `restartPolicyMaxRetries: 10`. Document this in deployment instructions.

2. **Railway PORT environment variable behavior**
   - What we know: Railway injects a `PORT` env variable. Uvicorn must bind to this port.
   - What's unclear: The exact port number Railway assigns (typically 3000 or 8080).
   - Recommendation: Always use `$PORT` in the start command, never hardcode. The Dockerfile CMD can use a default (8000) for local testing.

3. **Alembic pre-deploy with asyncpg URL scheme**
   - What we know: Alembic's `env.py` uses `get_settings().database_url` which returns the `postgresql+asyncpg://` URL. Alembic's offline/online migration modes handle async URLs via `async_engine_from_config`.
   - What's unclear: Whether `alembic upgrade head` works correctly with the async URL in Railway's pre-deploy container (which has no running event loop by default).
   - Recommendation: The existing `env.py` calls `asyncio.run(run_async_migrations())` which creates its own event loop. This should work in the pre-deploy container. Verify during first deployment.

## Sources

### Primary (HIGH confidence)
- Railway Config-as-Code Reference: https://docs.railway.com/reference/config-as-code -- Full schema for railway.json
- Railway Dockerfile Guide: https://docs.railway.com/builds/dockerfiles -- Dockerfile detection, build args
- Railway PostgreSQL Docs: https://docs.railway.com/databases/postgresql -- Plugin setup, variables, networking
- Railway Healthchecks: https://docs.railway.com/guides/healthchecks-and-restarts -- Deploy-time only, timeout config
- Railway Restart Policy: https://docs.railway.com/deployments/restart-policy -- ALWAYS/ON_FAILURE/NEVER, plan limits
- Railway Pre-Deploy Command: https://docs.railway.com/guides/pre-deploy-command -- Separate container, stateless
- Railpack Python Docs: https://railpack.com/languages/python/ -- Detection, version selection, start command
- APScheduler 3.x User Guide: https://apscheduler.readthedocs.io/en/3.x/userguide.html -- MemoryJobStore, misfire handling, coalesce
- Railway FastAPI Guide: https://docs.railway.com/guides/fastapi -- Deployment template, Hypercorn/Uvicorn setup

### Secondary (MEDIUM confidence)
- Railway Variables Reference: https://docs.railway.com/reference/variables -- Reference variable syntax `${{Service.VAR}}`
- Railway Private Networking: https://docs.railway.com/reference/private-networking -- Internal DNS, Wireguard tunnels
- PostgreSQL time-based retention strategies: https://blog.sequinstream.com/time-based-retention-strategies-in-postgres/ -- DELETE vs partitioning tradeoffs
- Railway Railpack vs Nixpacks blog: https://blog.railway.com/p/introducing-railpack -- Railpack as Nixpacks successor

### Tertiary (LOW confidence)
- Railway community station discussions on DATABASE_URL scheme issues -- confirms asyncpg scheme mismatch is a common gotcha
- WebSearch results on Railway restart backoff behavior -- backoff strategy exists but exact timing not documented officially

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH - All libraries already in project, Railway docs are authoritative
- Architecture (Railway deployment): HIGH - Official docs verified for config-as-code, healthcheck, restart, pre-deploy
- Architecture (data retention): HIGH - Simple DELETE approach is well-understood; data volume analysis confirms partitioning unnecessary
- Pitfalls: HIGH - DATABASE_URL scheme mismatch, multi-worker duplication, and pre-deploy isolation verified via multiple sources
- Discretion decisions: MEDIUM - Recommendations are well-reasoned but some (Dockerfile vs Railpack, catch-up vs gap) involve preference tradeoffs

**Research date:** 2026-02-17
**Valid until:** 2026-03-17 (30 days -- Railway platform is stable, no major breaking changes expected)
