# Scheduler Service

This service manages background tasks using APScheduler. 

All static jobs have been migrated to DBOS scheduled workflows instead, but this service is kept around for **dynamic jobs** that need to be scheduled at runtime (like by an AI agent). There are no dynamic jobs yet.

See also: [`api/src/schedulers/README.md`](../schedulers/README.md) for the overall “dual schedulers” approach (DBOS + APScheduler + unified API).

## Overview

- **Purpose**: To schedule and execute Python functions at specified times or intervals (e.g., cron-like jobs, one-off delayed tasks).
- **Initialization**: The scheduler is automatically initialized and started when the main FastAPI application starts, and shut down when the application stops. This is managed via FastAPI's lifespan events in `api/index.py`.
- **Persistence**: Jobs are persisted in the PostgreSQL database using `SQLAlchemyJobStore`. It utilizes the `sync_engine` defined in `api.src.database.database.py`.
    - The default table name for jobs is `apscheduler_jobs`.
- **Service Functions**: `api/src/apscheduler_service/service.py` is responsible for initializing the scheduler and showing examples of how to add jobs.

## Monitoring Jobs

You can monitor scheduled jobs directly in the database. For example, to see upcoming jobs:

```sql
SELECT 
    ID,
    NEXT_RUN_TIME,
    JOB_STATE
FROM APSCHEDULER_JOBS 
ORDER BY NEXT_RUN_TIME DESC;
```