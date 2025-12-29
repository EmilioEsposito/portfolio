## Schedulers (APScheduler only, DBOS disabled)

> **DBOS is currently disabled** to avoid $75/month DB keep-alive costs.
> All scheduled jobs have been moved to APScheduler.
> See [Re-enabling DBOS](#re-enabling-dbos) below for instructions if you want to turn it back on.

This backend uses **APScheduler** (`api/src/apscheduler_service/`) for all scheduled jobs:

- DB-backed job store via `SQLAlchemyJobStore` (jobs survive process restarts)
- Supports both cron-based and one-time scheduled jobs
- Jobs can be created dynamically at runtime or registered at startup

---

### Current Scheduled Jobs (APScheduler)

All production scheduled jobs are registered in `api/src/apscheduler_service/service.py`:

| Job ID | Schedule | Description |
|--------|----------|-------------|
| `clickup_peppino_tasks_scheduled` | 8am, 5pm ET | ClickUp task reminders |
| `zillow_email_new_unreplied_scheduled` | 8am, 12pm, 5pm ET | Check for new unreplied Zillow emails |
| `zillow_email_threads_ai_scheduled` | 8am, 5pm ET | AI analysis of Zillow email threads |
| `hello_world_apscheduler_job` | Once at startup | Demo/test job |

---

### API Endpoints

- **Base path**: `/api/schedulers/*` (unified) or `/api/apscheduler/*` (direct)

Endpoints:

- `GET /api/schedulers/get_jobs` - List all scheduled jobs
- `GET /api/schedulers/run_job_now/apscheduler/{job_id}` - Trigger a job immediately
- `DELETE /api/schedulers/delete_job/apscheduler/{job_id}` - Delete a job

---

### Startup behavior

APScheduler initializes in the background during FastAPI startup so the API becomes responsive quickly.
See `api/index.py` for the lifespan startup tasks.

---

## DBOS (Currently Disabled)

DBOS was disabled to avoid $75/month DB keep-alive costs. The code is preserved for future use.

### What DBOS offered

- **Durable workflows**: multi-step processes that can be retried/resumed
- **Human-in-the-loop** patterns (wait for events / approvals)
- **Code-defined scheduled workflows** (cron) via decorators

The DBOS scheduled workflows are still in the codebase (commented out with `# DBOS DISABLED`):
- `api/src/clickup/service.py` - `clickup_peppino_tasks_scheduled`
- `api/src/zillow_email/service.py` - `zillow_*_scheduled` workflows

### Re-enabling DBOS

If you're ready to pay for DBOS or want to use its durable workflow features:

1. **Uncomment imports in `api/index.py`**:
   ```python
   from api.src.dbos_service.routes import router as dbos_router
   from api.src.dbos_service.dbos_config import launch_dbos, shutdown_dbos
   from api.src.dbos_service.dbos_scheduler import capture_scheduled_workflows
   from api.src.zillow_email.service import register_zillow_dbos_jobs
   from api.src.clickup.service import register_clickup_dbos_jobs
   ```

2. **Uncomment `_dbos_startup_sync()` function** in `api/index.py`

3. **Uncomment DBOS startup in lifespan** in `api/index.py`:
   ```python
   with logfire.span("capture_scheduled_workflows"):
       capture_scheduled_workflows()
   app.state.dbos_startup_task = asyncio.create_task(asyncio.to_thread(_dbos_startup_sync))
   ```

4. **Uncomment DBOS shutdown logic** in `api/index.py`

5. **Uncomment DBOS router** in `api/index.py`:
   ```python
   app.include_router(dbos_router, prefix="/api")
   ```

6. **Uncomment DBOS imports in `api/src/schedulers/routes.py`**:
   ```python
   from api.src.dbos_service.routes import get_jobs as dbos_get_jobs
   from api.src.dbos_service.routes import run_job_now as dbos_run_job_now
   ```

7. **Update `get_jobs()` in `api/src/schedulers/routes.py`** to include DBOS jobs

8. **Remove APScheduler versions of the jobs** from `api/src/apscheduler_service/service.py`:
   - Remove `register_clickup_apscheduler_jobs()`
   - Remove `register_zillow_apscheduler_jobs()`
   - Remove their calls in `api/index.py`

9. **Set up DBOS Conductor** (optional but recommended for cloud dashboard):
   - Get a `DBOS_CONDUCTOR_KEY` from https://console.dbos.dev
   - Add to your environment variables

All disabled code is marked with `# DBOS DISABLED` comments for easy searching.

