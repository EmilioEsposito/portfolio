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

Startup registration is coordinated by `api/index.py`; each feature owns its
job implementation. Registration and execution follow the
[background automation policy](../../../docs/BACKGROUND_AUTOMATION.md).

The Scheduler page (`/team/scheduler`) shows each job's name and summary, and
its full description when opened. They are written beside each registration
(the `JobInfo` constants named below); read them there or on the page.

| Job ID | Name on the page | Schedule | Code |
|--------|------------------|----------|------|
| `clickup_peppino_tasks_scheduled` | ClickUp tasks: text the due list | 8am, 5pm ET | `clickup/service.py` (`TASK_REMINDER_JOB_INFO`) |
| `sernia_scheduled_checks` | Sernia AI: scheduled inbox check | Days and hours set in Settings, ET; production only | `sernia_ai/triggers/scheduled_triggers.py` (`JOB_INFO`) |
| `zillow-email-queue` | Zillow lead emails: process the queue | Every 30 seconds | `zillow_leads/queue.py` (`JOB_INFO`) |
| `escalation-outbox` | Emergency escalations: retry checks and calls | Every 15 seconds | `open_phone/escalation_delivery.py` (`RECOVERY_JOB_INFO`) |
| `showing-notices` | Showings: send due texts, invites and alerts | Every minute | `showing_bookings/holds.py` (`JOB_INFO`) |
| `showing-details-removal` | Showings: remove prospects' details after 12 months | Daily, 3:30am ET | `showing_bookings/retention.py` (`JOB_INFO`) |
| `hello_world_apscheduler_job` | Test job: hello world (local only) | Once, 30 seconds after startup | `apscheduler_service/service.py` (`HELLO_WORLD_JOB_INFO`) |

The job IDs are stored in the production job store and named by logs, tests and
the page's run and delete calls; the names can change freely, the IDs should not.

### Adding a job

1. Write its `JobInfo` (`api/src/schedulers/job_info.py`) beside its registration:
   - `name`: what a person would call it, not the function name.
   - `summary`: one sentence, shown on the collapsed card.
   - `description`: what each run does; what it touches (texts, emails, pushes,
     outside services, deleted data); how often and why; what stops if it is
     paused or removed. Plain words, short paragraphs separated by a blank line
     (`"\n\n"`), and no person's name, number or email: this repository is public.
2. Register it with `upsert_job(scheduler, info=JOB_INFO, id=..., func=..., ...)`.
   `upsert_job` records the description under the id and uses `info.name` as the
   job's name; do not pass `name=` as well.
3. Call the registration from `_apscheduler_startup_async` in `api/index.py`.
   `test_scheduler_job_info.py` runs that registration and fails for any job
   without a `JobInfo`; add the new ID and trigger to its `PRODUCTION_JOBS`.

A job added without a `JobInfo` (the agent's one-off scheduled texts and
emails) still lists, under its scheduler name, with no description.

---

The retired Zillow scheduled assessor and its SQL were removed. The active
Zillow queue calls `zillow_leads/handle.py`; it does not create phone
contacts or calendar events. See the [lead-agent guide](../zillow_leads/README.md).

### API Endpoints

- **Base path**: `/api/schedulers/*` (unified) or `/api/apscheduler/*` (direct)

Endpoints:

- `GET /api/schedulers/get_jobs` - List all scheduled jobs, each with its `name`,
  `summary` and `description` (null without a `JobInfo`), APScheduler's `trigger`
  text, and the trigger's `timezone` (a cron trigger's text leaves it out)
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

### Re-enabling DBOS

If you're ready to pay for DBOS or want to use its durable workflow features:

1. **Uncomment imports in `api/index.py`**:
   ```python
   from api.src.dbos_service.routes import router as dbos_router
   from api.src.dbos_service.dbos_config import launch_dbos, shutdown_dbos
   from api.src.dbos_service.dbos_scheduler import capture_scheduled_workflows
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
   - Remove their calls in `api/index.py`

9. **Set up DBOS Conductor** (optional but recommended for cloud dashboard):
   - Get a `DBOS_CONDUCTOR_KEY` from https://console.dbos.dev
   - Add to your environment variables

All disabled code is marked with `# DBOS DISABLED` comments for easy searching.

