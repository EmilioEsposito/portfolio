# Scheduler Service

This service manages background tasks using APScheduler (v3).

APScheduler runs both registered recurring work and dynamically scheduled jobs.
DBOS is disabled. Startup registration is coordinated by `api/index.py`; the
[unified scheduler guide](../schedulers/README.md) lists the current jobs and
API. The Zillow queue worker runs the dedicated lead agent, independently of
the general agent's scheduled checks.

## Overview

- **Purpose**: To schedule and execute Python functions at specified times or intervals (e.g., cron-like jobs, one-off delayed tasks).
- **Initialization**: The scheduler is automatically initialized and started when the main FastAPI application starts, and shut down when the application stops. This is managed via FastAPI's lifespan events in `api/index.py`.
- **Service Functions**: `api/src/apscheduler_service/service.py` is responsible for initializing the scheduler and showing examples of how to add jobs.

## Job Store: Local vs Railway

| Environment | Job Store | Persistence | Why |
|-------------|-----------|-------------|-----|
| **Local dev** | `MemoryJobStore` | None (lost on restart) | Fast — no DB overhead |
| **Railway** | `SQLAlchemyJobStore` | PostgreSQL (`apscheduler_jobs` table) | Survives deploys/restarts |

### Why MemoryJobStore locally?

APScheduler v3's `SQLAlchemyJobStore` is **synchronous** — every operation (`add_job`, `get_due_jobs`, `update_job`) blocks the asyncio event loop. During startup, APScheduler runs ~20 sequential DB operations. With Neon's remote Postgres, each operation takes ~0.15-0.5s (TCP+TLS overhead), freezing the event loop for several seconds total. This blocks all HTTP requests including health checks.

`MemoryJobStore` eliminates this entirely (startup goes from ~6.5s to ~0.01s). The tradeoff is that one-off scheduled jobs (SMS, email, push) are lost on restart, which is acceptable for local dev.

### Why QueuePool on the sync engine?

The sync engine (used by `SQLAlchemyJobStore`) was originally configured with `NullPool`, which opened a **new TCP+TLS connection to Neon (~0.5s) for every single DB operation**. Switching to `QueuePool(pool_size=2)` lets connections be reused — subsequent operations drop from ~0.5s to ~0.07s (pool checkout). See `api/src/database/database.py` for details.

### Future: APScheduler v4

APScheduler v4 (currently alpha) has native async support via `AsyncScheduler` + `SQLAlchemyDataStore` that accepts an async engine. This would eliminate the event loop blocking entirely and allow using `SQLAlchemyDataStore` everywhere. Track progress: [APScheduler v4 issue #465](https://github.com/agronholm/apscheduler/issues/465).

## Monitoring Jobs

You can monitor scheduled jobs directly in the database (Railway only). For example, to see upcoming jobs:

```sql
SELECT
    ID,
    NEXT_RUN_TIME,
    JOB_STATE
FROM APSCHEDULER_JOBS
ORDER BY NEXT_RUN_TIME DESC;
```