from dotenv import load_dotenv, find_dotenv
import logfire
import os

# Load local development variables (does not impact preview/production)
load_dotenv(find_dotenv(".env"), override=True)

# Logfire configuration
logfire.configure(
    service_name="fastapi",
    environment=os.getenv('RAILWAY_ENVIRONMENT_NAME', 'local'),
)

# --- Logfire Instrumentation ---
# AI/LLM Instrumentation
logfire.instrument_pydantic_ai()  # PydanticAI agent tracing
logfire.instrument_openai()  # Direct OpenAI SDK calls (completions, embeddings, etc.)

# HTTP Client Instrumentation
logfire.instrument_httpx()  # Async HTTP client used throughout the app
logfire.instrument_requests()  # Sync requests library (used by Google APIs, etc.)

# Database Instrumentation
logfire.instrument_asyncpg()  # Low-level asyncpg driver tracing
# SQLAlchemy instrumentation for query-level tracing
from apps.api.src.database.database import (
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

from fastapi import FastAPI, HTTPException, Request, Response
from contextlib import asynccontextmanager
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from strawberry.fastapi import GraphQLRouter
from strawberry.tools import merge_types
import strawberry
from starlette.middleware.sessions import SessionMiddleware
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

# Import from apps.api.src
from apps.api.src.ai.chat_weather.routes import router as chat_weather_router
from apps.api.src.ai.chat_emilio.routes import router as chat_emilio_router
from apps.api.src.ai.multi_agent_chat.routes import router as multi_agent_chat_router
from apps.api.src.open_phone import router as open_phone_router
from apps.api.src.cron import router as cron_router
from apps.api.src.google.common.routes import router as google_router
from apps.api.src.examples.routes import router as examples_router
from apps.api.src.push.routes import router as push_router
from apps.api.src.user.routes import router as user_router
from apps.api.src.contact.routes import router as contact_router
from apps.api.src.scheduler.routes import router as scheduler_router
# from apps.api.src.clickup.routes import router as clickup_router

from apps.api.src.scheduler.service import scheduler
from apps.api.src.zillow_email import service as zillow_email_service
from apps.api.src.clickup import service as clickup_service
# Import all GraphQL schemas
from apps.api.src.examples.schema import Query as ExamplesQuery, Mutation as ExamplesMutation

# from apps.api.src.future_features.schema import Query as FutureQuery, Mutation as FutureMutation
# from apps.api.src.another_feature.schema import Query as AnotherQuery, Mutation as AnotherMutation



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


@logfire.instrument("scheduler-services")
async def start_scheduler_services():
    scheduler.start()
    logfire.info("Scheduler initialized and started successfully.")
    await zillow_email_service.start_service()
    logfire.info("Zillow email service initialized and started successfully.")
    await clickup_service.start_service()
    logfire.info("Clickup service initialized and started successfully.")
    # Example: Add a test job on startup if needed
    # from datetime import datetime, timedelta
    # def startup_test_job():
    #     logfire.info(f"Scheduler startup_test_job executed at {datetime.now()}")
    # add_job(startup_test_job, 'date', job_id='startup_test_job', trigger_args={'run_date': datetime.now() + timedelta(seconds=15)})
    logfire.info("Scheduler services initialized and started successfully.")


# --- Lifespan Event Handler ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup logic
    logfire.info("LIFESPAN: FastAPI index.py startup...")
    try:
        await test_database_connections()
        await start_scheduler_services()
        logfire.info("FastAPI index.py startup completed successfully.")
    except Exception as e:
        logfire.exception(f"Error during scheduler startup: {e}")
        raise
    
    yield # Application runs here
    
    # Shutdown logic
    logfire.info("Application shutdown: Shutting down scheduler...")
    try:
        scheduler.shutdown()
    except Exception as e:
        logfire.exception(f"Error during scheduler shutdown: {e}")

app = FastAPI(docs_url="/api/docs", openapi_url="/api/openapi.json", lifespan=lifespan)

logfire.instrument_fastapi(app)



# --- Middleware Definitions ---

class ErrorHandlingMiddleware(BaseHTTPMiddleware):
    """
    Middleware to catch unhandled exceptions and log them to Logfire.

    Note: logfire.instrument_fastapi(app) already captures most errors, but this
    middleware ensures any exceptions that slip through are properly logged with
    logfire.exception() which triggers Logfire alerts.
    """
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        try:
            response = await call_next(request)
            return response
        except Exception as exc:
            # Log exception to Logfire for alerting
            logfire.exception(
                f"Unhandled exception on {request.method} {request.url.path}",
                path=request.url.path,
                method=request.method,
                client_host=request.client.host if request.client else "unknown",
            )
            # Return a generic 500 response
            return JSONResponse(
                status_code=500,
                content={"error": "Internal Server Error", "detail": "An unexpected error occurred."},
            )


# --- Middleware Registration ---
# Order matters: Middlewares process requests top-to-bottom, responses bottom-to-top.
# Error handling should wrap everything, so it's usually added early (but after essential ones like CORS/Session).

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

# Add Custom Error Handling Middleware
# This should wrap most of the application logic to catch errors effectively.
app.add_middleware(ErrorHandlingMiddleware)


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
app.include_router(open_phone_router, prefix="/api")
app.include_router(cron_router, prefix="/api")
app.include_router(google_router, prefix="/api")
app.include_router(examples_router, prefix="/api")
app.include_router(push_router, prefix="/api")
app.include_router(user_router, prefix="/api")
app.include_router(contact_router, prefix="/api")
app.include_router(scheduler_router, prefix="/api")
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

