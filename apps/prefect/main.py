"""
Prefect Hello World - Standalone Prefect App

PREFECT ARCHITECTURE OVERVIEW
=============================

Prefect has 3 main components:

1. FLOWS & TASKS (this file)
   - Your Python code decorated with @flow and @task
   - This is "what to run"

2. ORCHESTRATION SERVER (not configured yet)
   - Stores flow run history, schedules, UI dashboard
   - Options:
     a) Prefect Cloud (cloud.prefect.io) - Managed SaaS, easiest
     b) Self-hosted Prefect Server - Run yourself with PostgreSQL
     c) Ephemeral (current) - Temporary in-memory, no persistence

3. WORKERS (not configured yet)
   - Long-running processes that poll for scheduled work
   - Execute flows when triggered by the server
   - Required for scheduled/deployed flows

CURRENT STATE
=============
When you run `python main.py`:
- Prefect starts a TEMPORARY ephemeral server (in-memory SQLite)
- Flow runs immediately, then server shuts down
- No persistence, no scheduling, no UI

This is fine for development/testing, not production.

TO ENABLE PRODUCTION ORCHESTRATION
==================================

Option A: Prefect Cloud (Recommended for simplicity)
-----------------------------------------------------
1. Sign up at https://cloud.prefect.io
2. Create API key in settings
3. Set environment variables:
   PREFECT_API_URL=https://api.prefect.cloud/api/accounts/<ACCOUNT_ID>/workspaces/<WORKSPACE_ID>
   PREFECT_API_KEY=pnu_xxxxxxxxxxxx
4. Create a deployment (see below)
5. Start a worker: `prefect worker start --pool default-agent-pool`

Option B: Self-hosted Prefect Server
------------------------------------
1. Start server: `prefect server start` (uses SQLite by default)
   For production: configure PostgreSQL via PREFECT_API_DATABASE_CONNECTION_URL
2. Set: PREFECT_API_URL=http://localhost:4200/api
3. Create deployments and start workers

DEPLOYMENTS (Scheduling)
========================
Deployments tell Prefect WHEN and HOW to run flows.

Create with CLI:
  prefect deployment build apps/prefect/main.py:hello_world_flow -n "hello-scheduled" --cron "0 9 * * *"
  prefect deployment apply hello_world_flow-deployment.yaml

Or with Python (see serve() example below).
"""

from prefect import flow, task, serve
from datetime import datetime


@task(log_prints=True)
def say_hello(name: str) -> str:
    """A simple task that says hello."""
    message = f"Hello, {name}!"
    print(message)
    return message


@task(log_prints=True)
def get_current_time() -> str:
    """A task that returns the current time."""
    current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"Current time: {current_time}")
    return current_time


@flow(name="hello-world-flow", log_prints=True)
def hello_world_flow(name: str = "World") -> dict:
    """
    A simple Prefect flow that demonstrates:
    - Task execution
    - Return values
    - Logging

    Args:
        name: The name to greet (default: "World")

    Returns:
        dict with greeting and timestamp
    """
    print(f"Starting hello world flow for: {name}")

    # Execute tasks
    greeting = say_hello(name)
    timestamp = get_current_time()

    result = {
        "greeting": greeting,
        "timestamp": timestamp,
        "status": "success"
    }

    print(f"Flow completed successfully: {result}")
    return result


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "--serve":
        # DEPLOYMENT MODE: Start a long-running process that serves the flow
        # This creates a deployment and runs a worker in one command
        # The flow can then be triggered via API, UI, or schedule
        #
        # Usage: python main.py --serve
        #
        # Note: Without Prefect Cloud/Server configured, this still uses
        # ephemeral mode but keeps the process running to accept triggers.
        print("Starting flow server (Ctrl+C to stop)...")
        print("Flow will be available at: http://localhost:4200")
        serve(
            hello_world_flow.to_deployment(
                name="hello-world-deployment",
                # Uncomment to add a schedule:
                # cron="*/5 * * * *",  # Every 5 minutes
                # interval=300,  # Or: every 300 seconds
            )
        )
    else:
        # IMMEDIATE MODE: Run the flow once and exit
        # Uses ephemeral server (temporary, no persistence)
        result = hello_world_flow(name="Prefect User")
        print(f"\nFinal result: {result}")
