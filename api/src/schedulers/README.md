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
| `zillow-email-queue` | Zillow lead emails: process the queue | Wake-up: when a lead email's wait ends, or a retry is due | `zillow_leads/queue.py` (`JOB_INFO`, `QUEUE`) |
| `wake-up-catch-up` | Wake-ups: catch up on waiting work | 2 minutes after startup, then every 15 minutes | `apscheduler_service/wakeups.py` (`CATCH_UP_JOB_INFO`) |
| `escalation-outbox` | Emergency escalations: retry checks and calls | Wake-up: when a check or call is due for another try | `open_phone/escalation_delivery.py` (`RECOVERY_JOB_INFO`, `QUEUE`) |
| `showing-notices` | Showings: send due texts, invites and alerts | Every minute | `showing_bookings/holds.py` (`JOB_INFO`) |
| `showing-details-removal` | Showings: remove prospects' details after 12 months | Daily, 3:30am ET | `showing_bookings/retention.py` (`JOB_INFO`) |
| `hello_world_apscheduler_job` | Test job: hello world (local only) | Once, 30 seconds after startup | `apscheduler_service/service.py` (`HELLO_WORLD_JOB_INFO`) |

The job IDs are stored in the production job store and named by logs, tests and
the page's run and delete calls; the names can change freely, the IDs should not.
Neither should a stored job's function path: while a deploy runs, the old
server shares the job store, and APScheduler deletes a stored job whose function
it cannot import.

### Wake-up jobs

Use a schedule (cron) for work that belongs to a time of day: the ClickUp
texts, the Sernia inbox check. For work that waits in a table until it is due
(a batching wait, a retry, a delayed send), use a **wake-up job**
([`apscheduler_service/wakeups.py`](../apscheduler_service/wakeups.py)), not an
interval job that checks the table every few seconds. Work that should happen
at once belongs in the request's `BackgroundTasks`.

- The table is the record. Each row has its due time; the wake-up job carries no
  data. It is one one-time `date` job per queue, under the queue's job id, that
  runs the queue's own function at the earliest due time.
- It is moved after every enqueue (`wakeups.wake`, once the row is committed),
  at the end of every run (`async with wakeups.rearm(JOB_ID)` around the run's
  body), 30 seconds after each startup, and by the catch-up job (2 minutes after
  startup, then every 15). With nothing waiting there is no job and the page
  shows the queue as idle.
- During a deploy the old and new servers share the job store for a while.
  Each server's timer only sees jobs the other wrote at its own next pass, and
  older code can run a wake-up without setting the next one; the catch-up run 2
  minutes after startup sets every wake-up again once the old server is gone.
  Both servers can also start the same wake-up, and the second to delete it gets
  `JobLookupError`; `ResilientAsyncIOScheduler` (`apscheduler_service/service.py`)
  keeps that from stopping the scheduler.
- `misfire_grace_time=None`: a wake-up due while no server ran (a deploy) runs
  as soon as one does. APScheduler would otherwise drop it after a minute.
- The queue's `next_due_at` must follow its claim's own rules. A time it
  reports as due that the claim will not take (a thread held by another run's
  lease, say) makes the queue wake every few seconds (`RERUN_GAP`) until it can.

To move a queue onto one: give it a `next_due_at`, wrap its run function's
body in `rearm`, call `wake` after its enqueue commits, and register a
`WakeQueue` with `wakeups.register_queue` at startup in place of the interval
job (same job id and function, so the old server can still load it during the
deploy). Add the id to `WAKE_UP_QUEUES` in `test_scheduler_job_info.py`.

### Tracing

Decorate every job's async function with `@scheduled_job`
([`apscheduler_service/tracing.py`](../apscheduler_service/tracing.py)) where
it is defined. Each run then gets its own Logfire trace under an
`apscheduler job: {job_name}` span. `test_scheduler_job_info.py` fails for a
production job that is not decorated; its `NOT_YET_TRACED` lists the ones still
to do.

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
  text, the trigger's `timezone` (a cron trigger's text leaves it out), and
  `wake_up` (true for a wake-up queue). A wake-up queue with nothing waiting has
  no job; it is listed anyway, with trigger `idle` and no next run.
- `GET /api/schedulers/run_job_now/apscheduler/{job_id}` - Trigger a job immediately
  (for a wake-up queue, set its wake-up for now, even when it is idle)
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

