## Dual schedulers (DBOS + APScheduler)

This backend intentionally supports **two ways to run scheduled/background work**:

- **DBOS** (`api/src/dbos_service/`): durable workflows and scheduled workflows.
- **APScheduler** (`api/src/apscheduler_service/`): a DB-backed job scheduler for **dynamic** jobs created at runtime.

This is not redundant—each tool solves a different class of problems.

---

### When to use which

#### DBOS (recommended for durable background work)

Use DBOS when you want:

- **Durable workflows**: multi-step processes that can be retried/resumed.
- **Human-in-the-loop** patterns (wait for events / approvals).
- **Code-defined scheduled workflows** (cron) that are part of the application’s codebase.

Important: **DBOS is more than a scheduler**—the scheduler is just one capability of the workflow system.

DBOS scheduled workflows are **declared in code** (decorators). Because of that:

- They show up as “jobs” for observability and manual triggering.
- They **cannot be deleted via API** (removing them is a code change).

#### APScheduler (best for dynamic, runtime-created jobs)

Use APScheduler when you want:

- Jobs that are **created dynamically at runtime** (ex: per-tenant cron schedules, agent-created reminders).
- DB persistence via `SQLAlchemyJobStore` (jobs can survive process restarts).

Important: APScheduler jobs **can persist even if the code that originally created them is removed** (because the job store is in the database). So we expose **delete endpoints** for APScheduler jobs.

---

### Unified API for the frontend

To make it obvious in the UI which jobs belong to which system, we provide a thin wrapper router:

- **Unified**: `api/src/schedulers/routes.py`
- **Base path**: `/api/schedulers/*`

This aggregates the underlying routers without hiding them:

- DBOS routes: `/api/dbos/*`
- APScheduler routes: `/api/apscheduler/*`

Endpoints:

- `GET /api/schedulers/get_jobs`
  - Returns a combined list of jobs from both systems.
  - Each job includes a `service` field: `"dbos"` or `"apscheduler"`.
- `GET /api/schedulers/run_job_now/{service}/{job_id}`
  - Dispatches to the correct underlying scheduler.
- `DELETE /api/schedulers/delete_job/apscheduler/{job_id}`
  - Supported for APScheduler only.
  - DBOS deletion is intentionally rejected.

---

### Startup behavior (non-blocking)

Both DBOS and APScheduler may take time to start. FastAPI startup is structured so that:

- the API becomes responsive quickly
- scheduler backends initialize in the background

See `api/index.py` for the lifespan startup tasks.

