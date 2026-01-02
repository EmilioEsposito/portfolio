from dotenv import load_dotenv, find_dotenv
import asyncio
import json
import logfire
import os
import time

_startup_time = time.perf_counter()  # Capture immediately for startup timing

import logfire
from api.src.utils.logfire_config import ensure_logfire_configured

ensure_logfire_configured(mode="prod", service_name="fastapi")
logfire.info("STARTUP0: FastAPI index.py starting...")

# --- Logfire Instrumentation ---
# AI/LLM Instrumentation
logfire.instrument_pydantic_ai()  # PydanticAI agent tracing
logfire.instrument_openai()  # Direct OpenAI SDK calls (completions, embeddings, etc.)

# HTTP Client Instrumentation
logfire.instrument_httpx()  # Async HTTP client used throughout the app
logfire.instrument_requests()  # Sync requests library (used by Google APIs, etc.)

# Database Instrumentation
# asyncpg instrumentation is noisy with DBOS; rely on SQLAlchemy instead
# logfire.instrument_asyncpg()  # Low-level asyncpg driver tracing
# SQLAlchemy instrumentation for query-level tracing (app engines only)
with logfire.span("Database"):
    from api.src.database.database import (
        engine as async_engine,
        sync_engine,
        test_database_connections
    )
    engines_to_instrument = [async_engine]
    if sync_engine is not None:
        engines_to_instrument.append(sync_engine)

    logfire.instrument_sqlalchemy(engines=engines_to_instrument)

# Validation Instrumentation
# logfire.instrument_pydantic()  # Pydantic model validation tracing. Commented out because it's very verbose.

logfire.info("Logfire configured with comprehensive instrumentation")

from fastapi import FastAPI, HTTPException, Request
from contextlib import asynccontextmanager
from fastapi.middleware.cors import CORSMiddleware
from strawberry.fastapi import GraphQLRouter
from strawberry.tools import merge_types
import strawberry
from starlette.middleware.sessions import SessionMiddleware
logfire.info("STARTUP1")

# Import from api.src
with logfire.span("Creating AI Agent Routes"):
    from api.src.ai.chat_weather.routes import router as chat_weather_router
    from api.src.ai.chat_emilio.routes import router as chat_emilio_router
    from api.src.ai.multi_agent_chat.routes import router as multi_agent_chat_router
    from api.src.ai.hitl_agents.routes import router as hitl_agents_router

from api.src.open_phone import router as open_phone_router
from api.src.cron import router as cron_router
from api.src.google.common.routes import router as google_router
from api.src.examples.routes import router as examples_router
from api.src.push.routes import router as push_router
from api.src.user.routes import router as user_router
from api.src.contact.routes import router as contact_router
from api.src.apscheduler_service.routes import router as apscheduler_router
# DBOS DISABLED: $75/month DB keep-alive costs too high for hobby project.
# See api/src/schedulers/README.md for re-enabling instructions.
# from api.src.dbos_service.routes import router as dbos_router
# from api.src.dbos_service.dbos_config import launch_dbos, shutdown_dbos
# from api.src.dbos_service.dbos_scheduler import capture_scheduled_workflows
# from api.src.zillow_email.service import register_zillow_dbos_jobs
# from api.src.clickup.service import register_clickup_dbos_jobs

from api.src.apscheduler_service.service import register_hello_apscheduler_jobs, get_scheduler
from api.src.clickup.service import register_clickup_apscheduler_jobs
from api.src.zillow_email.service import register_zillow_apscheduler_jobs
from api.src.schedulers.routes import router as schedulers_router
from api.src.docuform.routes import router as docuform_router

# Import all GraphQL schemas
from api.src.examples.schema import Query as ExamplesQuery, Mutation as ExamplesMutation
from pathlib import Path
import threading

# from api.src.future_features.schema import Query as FutureQuery, Mutation as FutureMutation
# from api.src.another_feature.schema import Query as AnotherQuery, Mutation as AnotherMutation


# Verify critical environment variables
required_env_vars = {
    "SESSION_SECRET_KEY": (
        "Required for secure session handling. "
        "Generate unique values for each environmen!:\n"
    ),
    "GOOGLE_OAUTH_CLIENT_ID": (
        "Required for Google OAuth. Set up in Google Cloud Console.\n"
    ),
    "GOOGLE_OAUTH_CLIENT_SECRET": (
        "Required for Google OAuth. Set up in Google Cloud Console.\n"
    ),
    "GOOGLE_OAUTH_REDIRECT_URI": (
        "Required for Google OAuth. Must match the URIs configured in Google Cloud Console.\n"
    ),
    "OPEN_PHONE_WEBHOOK_SECRET": (
        "Required for OpenPhone webhook. Set up in OpenPhone dashboard.\n"
    ),
}

missing_vars = []
for var, description in required_env_vars.items():
    if not os.getenv(var):
        missing_vars.append(f"- {var}:\n{description}\n")

if missing_vars:
    raise ValueError(
        "Missing required environment variables:\n\n"
        + "\n".join(missing_vars)
    )

# --- Lifespan Event Handler ---

# DBOS DISABLED: $75/month DB keep-alive costs too high for hobby project.
# See api/src/schedulers/README.md for re-enabling instructions.
# def _dbos_startup_sync() -> None:
#     """
#     Start DBOS without blocking FastAPI startup.
#
#     NOTE: This runs in a background thread via asyncio.to_thread(), because DBOS
#     startup is expected to take a while and is synchronous.
#     """
#     try:
#         with logfire.span("dbos_startup"):
#             with logfire.span("launch_dbos"):
#                 launch_dbos()
#             with logfire.span("register_zillow_dbos_jobs"):
#                 register_zillow_dbos_jobs()  # just make sure the service module is imported
#             with logfire.span("register_clickup_dbos_jobs"):
#                 register_clickup_dbos_jobs()  # just make sure the service module is imported
#
#         # Keep existing behavior, but run it after DBOS is up and off the main startup path.
#         logfire.info("DBOS background startup completed successfully.")
#     except Exception as e:
#         # Don't take down the API if DBOS fails to launch; log loudly instead.
#         logfire.exception(f"DBOS background startup failed: {e}")


async def _apscheduler_startup_async() -> None:
    """
    Start APScheduler without blocking FastAPI startup.

    The SQLAlchemy job store and job registration do sync I/O, so we run those in
    a thread pool. However, AsyncIOScheduler.start() must be called on the main
    event loop since it registers asyncio callbacks.
    """
    try:
        with logfire.span("apscheduler_startup"):
                        # Initialize scheduler in thread pool (SQLAlchemy jobstore may block on DB)
            apscheduler = await asyncio.to_thread(get_scheduler)

            # start() must be on main event loop for AsyncIOScheduler
            if not apscheduler.running:
                apscheduler.start()
            # Register jobs in thread pool (add_job writes to the DB via sync engine)
            await asyncio.to_thread(register_hello_apscheduler_jobs)
            await asyncio.to_thread(register_clickup_apscheduler_jobs)
            await asyncio.to_thread(register_zillow_apscheduler_jobs)

        logfire.info("APScheduler startup completed successfully.")
    except asyncio.CancelledError:
        # Expected during hot reload - don't let debugger pause here
        logfire.debug("APScheduler startup cancelled (hot reload)")
        return  # Exit cleanly without re-raising
    except Exception as e:
        logfire.exception(f"APScheduler startup failed: {e}")


def _local_heartbeat():
    """
    Write a timestamp heartbeat to disk.
    Used only in local / non-Railway environments to detect debugger pauses.
    """
    HEARTBEAT_PATH = Path("/tmp/fastapi_heartbeat")

    def _run():
        while True:
            try:
                HEARTBEAT_PATH.write_text(str(time.time()))
            except Exception:
                pass
            time.sleep(0.5)

    threading.Thread(
        target=_run,
        name="fastapi-heartbeat",
        daemon=True,
    ).start()

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup logic
    with logfire.span("LIFESPAN: FastAPI index.py"):
        try:

            # Enable heartbeat only when NOT running on Railway
            if not os.getenv("RAILWAY_ENVIRONMENT_NAME"):
                _local_heartbeat()
            
            # if we are deploying on Railway, we want to block deployment if the DB connection test fails.
            if os.getenv("RAILWAY_ENVIRONMENT_NAME"):
                await test_database_connections()

            # Start APScheduler in the background so FastAPI can begin serving immediately.
            app.state.apscheduler_startup_task = asyncio.create_task(_apscheduler_startup_async())

            # DBOS DISABLED: $75/month DB keep-alive costs too high for hobby project.
            # See api/src/schedulers/README.md for re-enabling instructions.
            # # IMPORTANT: Capture scheduled workflow info BEFORE launch_dbos().
            # # Keep this synchronous so the /dbos endpoints can respond immediately.
            # with logfire.span("capture_scheduled_workflows"):
            #     capture_scheduled_workflows()
            #
            # # Start DBOS in the background so FastAPI can begin serving immediately.
            # # Store task so we can introspect it later if needed.
            # app.state.dbos_startup_task = asyncio.create_task(asyncio.to_thread(_dbos_startup_sync))

            logfire.info("LIFESPAN: FastAPI index.py startup completed successfully.")
        except Exception as e:
            logfire.exception(f"Error during startup: {e}")
            raise

    yield # Application runs here


    # Shutdown logic
    logfire.info("Application shutdown...")

    # Cancel background startup tasks if still running
    for task_name in ("apscheduler_startup_task",):  # DBOS disabled
        task = getattr(app.state, task_name, None)
        if task and not task.done():
            task.cancel()
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=0.5)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass

    # Shutdown APScheduler
    logfire.info("Shutting down APScheduler...")
    try:
        # TODO: cleanup threads that are blocking the process from exiting
        scheduler = get_scheduler()
        if scheduler.running:
            scheduler.shutdown(wait=False)  # Don't wait for jobs to complete
    except Exception as e:
        logfire.warn(f"APScheduler shutdown error: {e}")

    # DBOS DISABLED: Shutdown code preserved for re-enabling.
    # # Shutdown DBOS in a thread with a hard timeout to avoid blocking hot reload.
    # # DBOS workflows are durable, so pending workflows will resume on restart.
    # logfire.info("Shutting down DBOS...")
    # dbos_shutdown_timed_out = False
    # try:
    #     await asyncio.wait_for(
    #         asyncio.to_thread(shutdown_dbos, workflow_completion_timeout_sec=1),
    #         timeout=3.0  # Hard timeout - don't block hot reload
    #     )
    # except asyncio.TimeoutError:
    #     dbos_shutdown_timed_out = True
    #     logfire.warn("DBOS shutdown timed out - will force exit (workflows recover on restart)")

    logfire.info("Application shutdown completed successfully.")

    # Force exit in local dev to avoid Hypercorn shutdown timeout.
    # APScheduler threads can block clean shutdown; this is safe for local dev.
    if not os.getenv("RAILWAY_ENVIRONMENT_NAME"):
        os._exit(0)

app = FastAPI(docs_url="/api/docs", openapi_url="/api/openapi.json", lifespan=lifespan)

logfire.instrument_fastapi(app)

# --- Middleware Registration ---

is_hosted = len(os.getenv("RAILWAY_ENVIRONMENT_NAME","")) > 0

# Add session middleware - MUST be added before CORS middleware
app.add_middleware(
    SessionMiddleware,
    secret_key=os.getenv("SESSION_SECRET_KEY"),  # Will raise error if not set
    same_site="lax",  # Required for OAuth redirects
    # TODO: Set secure=True for production based on env var? Needs testing.
    # secure=os.getenv("RAILWAY_ENVIRONMENT_NAME") == "production", # Enable for production HTTPS only
    https_only=is_hosted,
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], # TODO: Restrict in production? ["https://eesposito.com", etc] ?
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- GraphQL Setup ---

# Merge GraphQL types
Query = merge_types("Query", (ExamplesQuery,))
Mutation = merge_types("Mutation", (ExamplesMutation,))

# Create combined schema for GraphQL
schema = strawberry.Schema(query=Query, mutation=Mutation)

# GraphQL router
graphql_router = GraphQLRouter(schema, path="/graphql")

# Include all routers
app.include_router(graphql_router, prefix="/api")
app.include_router(chat_weather_router, prefix="/api")
app.include_router(chat_emilio_router, prefix="/api")
app.include_router(multi_agent_chat_router, prefix="/api")
app.include_router(hitl_agents_router, prefix="/api")
app.include_router(open_phone_router, prefix="/api")
app.include_router(cron_router, prefix="/api")
app.include_router(google_router, prefix="/api")
app.include_router(examples_router, prefix="/api")
app.include_router(push_router, prefix="/api")
app.include_router(user_router, prefix="/api")
app.include_router(contact_router, prefix="/api")
app.include_router(apscheduler_router, prefix="/api")
# DBOS DISABLED: See api/src/schedulers/README.md for re-enabling instructions.
# app.include_router(dbos_router, prefix="/api")
app.include_router(schedulers_router, prefix="/api")
app.include_router(docuform_router, prefix="/api")
# app.include_router(clickup_router, prefix="/api")

@app.get("/api/hello")
async def hello_fast_api():
    logfire.info("Hello from FastAPI")
    return {"message": "Hello from FastAPI"}


_startup_logged = False

@app.get("/api/health")
async def health_check():
    global _startup_logged
    if not _startup_logged:
        _startup_logged = True
        elapsed = time.perf_counter() - _startup_time
        logfire.info(f"🚀 STARTUP COMPLETE: First request served after {elapsed:.2f}s")
    return {"status": "healthy"}

@app.get("/api/error")
async def error_500_check():
    logfire.error("Raising 500 error for testing")

    # raise a 500 error
    raise HTTPException(status_code=500, detail="Test error message")