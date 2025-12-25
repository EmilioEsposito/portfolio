from dotenv import load_dotenv, find_dotenv
import asyncio
import json
import logfire
import os

import logfire
from api.src.utils.logfire_config import ensure_logfire_configured

ensure_logfire_configured(mode="prod", service_name="fastapi")
logfire.info("FastAPI index.py starting...")

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

# Import from api.src
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
from api.src.dbos_service.routes import router as dbos_router
# from api.src.clickup.routes import router as clickup_router
from api.src.dbos_service.dbos_config import launch_dbos, shutdown_dbos
from api.src.dbos_service.dbos_scheduler import capture_scheduled_workflows
from api.src.dbos_service.examples.hello_dbos import hello_workflow
from api.src.apscheduler_service.service import register_hello_apscheduler_jobs, get_scheduler
from api.src.schedulers.routes import router as schedulers_router

# Import DBOS scheduled workflows to register them with DBOS
from api.src.zillow_email.service import register_zillow_dbos_jobs
from api.src.clickup.service import register_clickup_dbos_jobs
# Import all GraphQL schemas
from api.src.examples.schema import Query as ExamplesQuery, Mutation as ExamplesMutation

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
def _dbos_startup_sync() -> None:
    """
    Start DBOS without blocking FastAPI startup.

    NOTE: This runs in a background thread via asyncio.to_thread(), because DBOS
    startup is expected to take a while and is synchronous.
    """
    try:
        with logfire.span("dbos_startup"):
            with logfire.span("launch_dbos"):
                launch_dbos()
            with logfire.span("register_zillow_dbos_jobs"):
                register_zillow_dbos_jobs()  # just make sure the service module is imported
            with logfire.span("register_clickup_dbos_jobs"):
                register_clickup_dbos_jobs()  # just make sure the service module is imported

        # Keep existing behavior, but run it after DBOS is up and off the main startup path.
        hello_workflow(5)
        logfire.info("DBOS background startup completed successfully.")
    except Exception as e:
        # Don't take down the API if DBOS fails to launch; log loudly instead.
        logfire.exception(f"DBOS background startup failed: {e}")


async def _apscheduler_startup_async() -> None:
    """
    Start APScheduler without blocking FastAPI startup.

    NOTE: APScheduler is an AsyncIO scheduler, so we keep this on the main event loop
    (do NOT run it in a background thread).
    """
    try:
        with logfire.span("apscheduler_startup"):
            apscheduler = get_scheduler()
            if not apscheduler.running:
                apscheduler.start()
            register_hello_apscheduler_jobs()
        logfire.info("APScheduler background startup completed successfully.")
    except Exception as e:
        logfire.exception(f"APScheduler background startup failed: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup logic
    logfire.info("LIFESPAN: FastAPI index.py startup...")
    try:
        await test_database_connections()

        # Start APScheduler in the background so FastAPI can begin serving immediately.
        app.state.apscheduler_startup_task = asyncio.create_task(_apscheduler_startup_async())

        # IMPORTANT: Capture scheduled workflow info BEFORE launch_dbos().
        # Keep this synchronous so the /dbos endpoints can respond immediately.
        with logfire.span("capture_scheduled_workflows"):
            capture_scheduled_workflows()

        # Start DBOS in the background so FastAPI can begin serving immediately.
        # Store task so we can introspect it later if needed.
        app.state.dbos_startup_task = asyncio.create_task(asyncio.to_thread(_dbos_startup_sync))

        logfire.info("LIFESPAN: FastAPI index.py startup completed successfully.")
    except Exception as e:
        logfire.exception(f"Error during startup: {e}")
        raise

    yield # Application runs here

    # Shutdown logic
    logfire.info("Application shutdown...")

    # Cancel background startup tasks if still running
    for task_name in ("dbos_startup_task", "apscheduler_startup_task"):
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
        scheduler = get_scheduler()
        if scheduler.running:
            scheduler.shutdown(wait=False)  # Don't wait for jobs to complete
    except Exception as e:
        logfire.warn(f"APScheduler shutdown error: {e}")

    # Shutdown DBOS in a thread with a hard timeout to avoid blocking hot reload.
    # DBOS workflows are durable, so pending workflows will resume on restart.
    logfire.info("Shutting down DBOS...")
    dbos_shutdown_timed_out = False
    try:
        await asyncio.wait_for(
            asyncio.to_thread(shutdown_dbos, workflow_completion_timeout_sec=1),
            timeout=3.0  # Hard timeout - don't block hot reload
        )
    except asyncio.TimeoutError:
        dbos_shutdown_timed_out = True
        logfire.warn("DBOS shutdown timed out - will force exit (workflows recover on restart)")

    logfire.info("Application shutdown completed successfully.")

    # DBOS spawns non-daemon threads that block process exit even after destroy() times out.
    # Force exit to allow hot reload to work. This is safe because:
    # 1. DBOS workflows are durable and will recover from the database on restart
    # 2. We've already completed our graceful shutdown logic above
    if dbos_shutdown_timed_out:
        import os
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
app.include_router(dbos_router, prefix="/api")
app.include_router(schedulers_router, prefix="/api")
# app.include_router(clickup_router, prefix="/api")

@app.get("/api/hello")
async def hello_fast_api():
    logfire.info("Hello from FastAPI")
    return {"message": "Hello from FastAPI"}


@app.get("/api/health")
async def health_check():
    return {"status": "healthy"}

@app.get("/api/error")
async def error_500_check():
    logfire.error("Raising 500 error for testing")

    # raise a 500 error
    raise HTTPException(status_code=500, detail="Test error message")