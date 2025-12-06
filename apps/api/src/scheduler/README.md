# Scheduler Service

This service manages background tasks using APScheduler.

## Overview

- **Purpose**: To schedule and execute Python functions at specified times or intervals (e.g., cron-like jobs, one-off delayed tasks).
- **Initialization**: The scheduler is automatically initialized and started when the main FastAPI application starts, and shut down when the application stops. This is managed via FastAPI's lifespan events in `api/index.py`.
- **Persistence**: Jobs are persisted in the PostgreSQL database using `SQLAlchemyJobStore`. It utilizes the `sync_engine` defined in `api.src.database.database.py`.
    - The default table name for jobs is `apscheduler_jobs`.
- **Service Functions**:  `api.src.scheduler.service.py` is mostly responsible for initializing the scheduler and showing examples of how to add jobs.

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