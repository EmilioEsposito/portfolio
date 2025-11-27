import logging
import sys # Added for sys.stdout
from dotenv import load_dotenv, find_dotenv
import json # Added import
import logfire

# --- Forceful Logging Reconfiguration ---
# Remove all handlers associated with the root logger object.
# This ensures that our basicConfig call is the one that sets up the primary console handler.
for handler in logging.root.handlers[:]:
    logging.root.removeHandler(handler)
    handler.close() # Important to close handlers

LOG_FORMAT = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
DATE_FORMAT = '%Y-%m-%d %H:%M:%S'  # Controls the format of the timestamp in logs

logging.basicConfig(
    level=logging.INFO,
    format=LOG_FORMAT,
    datefmt=DATE_FORMAT,  # datefmt specifies the format of the %(asctime)s timestamp; previously, the default format was used
    stream=sys.stdout  # Explicitly set the stream
)

# --- SQLAlchemy Logging Configuration ---
sqlalchemy_logger = logging.getLogger("sqlalchemy")
sqlalchemy_logger.setLevel(logging.WARNING)
for handler in sqlalchemy_logger.handlers[:]:
    sqlalchemy_logger.removeHandler(handler)
    handler.close()
if not sqlalchemy_logger.hasHandlers():
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT))
    sqlalchemy_logger.addHandler(handler)
# --- End SQLAlchemy Logging Configuration ---

# Load local development variables (does not impact preview/production)
load_dotenv(find_dotenv(".env.development.local"), override=True)


from fastapi import FastAPI, HTTPException, Request, Response
from contextlib import asynccontextmanager
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from strawberry.fastapi import GraphQLRouter
from strawberry.tools import merge_types
import strawberry
import os
from starlette.middleware.sessions import SessionMiddleware
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import StreamingResponse
import traceback

# Import from api.src
from api.src.ai.chat_weather.routes import router as chat_weather_router
from api.src.ai.chat_emilio.routes import router as chat_emilio_router
from api.src.ai.multi_agent_chat.routes import router as multi_agent_chat_router
from api.src.open_phone import router as open_phone_router
from api.src.cron import router as cron_router
from api.src.google.common.routes import router as google_router
from api.src.examples.routes import router as examples_router
from api.src.push.routes import router as push_router
from api.src.user.routes import router as user_router
from api.src.contact.routes import router as contact_router
from api.src.scheduler.routes import router as scheduler_router
# from api.src.clickup.routes import router as clickup_router
from api.src.google.gmail.service import send_email
from api.src.google.common.service_account_auth import get_delegated_credentials

from api.src.scheduler.service import scheduler
from api.src.zillow_email import service as zillow_email_service
from api.src.clickup import service as clickup_service
# Import all GraphQL schemas
from api.src.examples.schema import Query as ExamplesQuery, Mutation as ExamplesMutation

# from api.src.future_features.schema import Query as FutureQuery, Mutation as FutureMutation
# from api.src.another_feature.schema import Query as AnotherQuery, Mutation as AnotherMutation


# Define a logger for this module
logger = logging.getLogger(__name__)

# Logfire configuration
logfire.configure(environment=os.getenv('RAILWAY_ENVIRONMENT_NAME', 'local'))  
logfire.instrument_pydantic_ai()  
logfire.instrument_httpx()
logfire.instrument_asyncpg()
logger.info("Logfire configured and instrumented")

# Test log message immediately after reconfiguration
logger.info("EMILIO: FastAPI index.py loaded and Logfire configured")

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
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup logic
    logger.info("LIFESPAN: FastAPI index.py startup...")
    try:
        scheduler.start()
        logger.info("Scheduler initialized and started successfully.")
        await zillow_email_service.start_service()
        await clickup_service.start_service()
        # Example: Add a test job on startup if needed
        # from datetime import datetime, timedelta
        # def startup_test_job():
        #     logger.info(f"Scheduler startup_test_job executed at {datetime.now()}")
        # add_job(startup_test_job, 'date', job_id='startup_test_job', trigger_args={'run_date': datetime.now() + timedelta(seconds=15)})
        logger.info("Zillow email service initialized and started successfully.")
        logger.info("FastAPI index.py startup completed successfully.")
    except Exception as e:
        logger.error(f"Error during scheduler startup: {e}", exc_info=True)
        raise
    
    yield # Application runs here
    
    # Shutdown logic
    logger.info("Application shutdown: Shutting down scheduler...")
    try:
        scheduler.shutdown()
    except Exception as e:
        logger.error(f"Error during scheduler shutdown: {e}", exc_info=True)

app = FastAPI(docs_url="/api/docs", openapi_url="/api/openapi.json", lifespan=lifespan)

logfire.instrument_fastapi(app)

# --- Error Notification ---
async def send_error_notification(request: Request, exc: Exception) -> None:
    """
    Sends an email notification for 500 errors with detailed information.

    Args:
        request: The FastAPI request object
        exc: The exception that occurred (can be a generic one for handled 500s)
    """
    # Avoid sending notifications for expected errors during development/testing if needed
    # Example: if isinstance(exc, ExpectedTestException): return

    error_details = {
        "error": str(exc),
        # Provide traceback only if it's available (i.e., for uncaught exceptions)
        "traceback": traceback.format_exc() if exc.__traceback__ else "N/A (Handled 500 Response)",
        "path": request.url.path,
        "method": request.method,
        "headers": dict(request.headers),
        "client_host": request.client.host if request.client else "unknown",
    }

    # Log the error details regardless of email success
    logger.error(
        f"500 Error Detail:\nPath={error_details['path']}\nMethod={error_details['method']}\nError={error_details['error']}",
        exc_info=exc if exc.__traceback__ else None # Only add exc_info if there's a real traceback
    )


    try:
        # Send email notification using service account
        credentials = get_delegated_credentials(
            user_email="emilio@serniacapital.com",  # TODO: Move to env var?
            scopes=["https://mail.google.com"],
        )
        message_text = f"A 500 error occurred on your application ({os.getenv('RAILWAY_ENVIRONMENT_NAME', 'unknown environment (local?)')}).\n\n" 
        message_text += f"Error: {error_details['error']}\n"
        message_text += f"Path: {error_details['path']}\n"
        message_text += f"Method: {error_details['method']}\n"
        message_text += f"Client IP: {error_details['client_host']}\n\n"
        message_text += f"Traceback:\n{error_details['traceback']}"

        await send_email(
            to="espo412@gmail.com",  # TODO: Move to env var?
            subject=f"ALERT: 500 Error on {os.getenv('RAILWAY_ENVIRONMENT_NAME', 'unknown environment (local?)')}",
            message_text=message_text,
            credentials=credentials,
        )
        logger.info(f"Error notification email sent for 500 on {error_details['path']}")
    except Exception as email_error:
        logger.error(f"Failed to send error notification email: {str(email_error)}", exc_info=True)


# --- Middleware Definitions ---

class ErrorHandlingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        response: Response = None # Ensure response is defined
        try:
            response = await call_next(request)

            if response is not None and response.status_code == 500:
                logger.warning(f"MIDDLEWARE: Handling 500 response for: {request.url.path}")
                logger.warning(f"MIDDLEWARE: Response object type: {type(response)}")
                logger.warning(f"MIDDLEWARE: Response media type attribute: {getattr(response, 'media_type', 'N/A')}")
                logger.warning(f"MIDDLEWARE: Response headers: {response.headers}")

                response_body_content = b""
                
                if hasattr(response, 'body_iterator'): # Check for body_iterator to identify streaming responses
                    logger.warning("MIDDLEWARE: Response has body_iterator. Iterating to get body.")
                    async for chunk in response.body_iterator:
                        if isinstance(chunk, bytes):
                            response_body_content += chunk
                        else: 
                            response_body_content += chunk.encode('utf-8')
                    logger.warning(f"MIDDLEWARE: Read {len(response_body_content)} bytes from body_iterator.")
                elif hasattr(response, 'body'): # For non-streaming, like JSONResponse directly
                    try:
                        # Accessing .body on some response types might trigger rendering if not already done.
                        response_body_content = response.body 
                        logger.warning(f"MIDDLEWARE: Accessed response.body (non-streaming), length: {len(response_body_content) if response_body_content else 0}")
                        if not response_body_content:
                             logger.warning("MIDDLEWARE: response.body (non-streaming) was empty after access.")
                    except Exception as e_body:
                        logger.error(f"MIDDLEWARE: Error accessing response.body (non-streaming): {e_body}", exc_info=True)
                else:
                    logger.warning("MIDDLEWARE: Response object does not have 'body_iterator' or 'body' attribute.")
                
                error_message_detail = f"Handled 500 Response for {request.url.path}" # Default

                content_type_header = response.headers.get('content-type', '').lower()
                is_json_media_type = 'application/json' in content_type_header

                logger.warning(f"MIDDLEWARE: Content-Type header: '{content_type_header}', Is JSON media type: {is_json_media_type}")

                if response_body_content and is_json_media_type:
                    try:
                        body_str = response_body_content.decode('utf-8')
                        logger.warning(f"MIDDLEWARE: Attempting to parse JSON from response_body_content (decoded snippet): {body_str[:250]}")
                        content = json.loads(body_str)
                        if isinstance(content, dict) and 'detail' in content:
                            extracted_detail = content['detail']
                            if isinstance(extracted_detail, str): error_message_detail = extracted_detail
                            else: error_message_detail = json.dumps(extracted_detail)
                            logger.warning(f"MIDDLEWARE: Extracted detail for email: '{error_message_detail[:250]}'")
                        else:
                            logger.warning(f"MIDDLEWARE: 'detail' key not found or content not a dict. Parsed content snippet: {str(content)[:250]}")
                    except Exception as e_json:
                        logger.error(f"MIDDLEWARE: Failed to parse JSON from response body: {e_json}", exc_info=True)
                else:
                    logger.warning(f"MIDDLEWARE: Skipping JSON parsing. Body empty ({not response_body_content}) or media type not application/json (is_json_media_type: {is_json_media_type}).")

                synthetic_exc = Exception(error_message_detail)
                synthetic_exc.__traceback__ = None
                await send_error_notification(request, synthetic_exc)
                
                # If we consumed a streaming response, we need to recreate it to avoid errors like
                # "h11._util.LocalProtocolError: Too little data for declared Content-Length"
                # because the original body_iterator is now exhausted.
                if hasattr(response, 'body_iterator') and response_body_content:
                    logger.warning("MIDDLEWARE: Reconstructing response because body_iterator was consumed.")
                    # We have status_code, headers, and the body content
                    # response.headers is a Starlette Headers object, which is fine for a new Response
                    # response.status_code is also available directly
                    response = Response(
                        content=response_body_content, 
                        status_code=response.status_code, 
                        headers=dict(response.headers), # Convert to dict for constructor
                        media_type=response.headers.get('content-type') # Get media_type from headers
                    )
                    logger.warning(f"MIDDLEWARE: New response created: type={type(response)}, headers={response.headers}")

        except Exception as exc: 
            logger.error(f"MIDDLEWARE: Caught UNHANDLED exception for {request.url.path}", exc_info=True)
            await send_error_notification(request, exc) 
            # For truly unhandled exceptions, create a generic 500 response
            # Ensure response is a Response object before returning
            response = JSONResponse(
                status_code=500,
                content={"error": "Internal Server Error", "detail": "An unexpected server error occurred during middleware processing."},
            )
        
        if response is None: # Should ideally not happen if call_next always returns or exception is caught
            logger.error("MIDDLEWARE: Response object was None at exit of dispatch, creating generic 500 response.")
            response = JSONResponse(
                status_code=500,
                content={"error": "Internal Server Error", "detail": "Middleware processing error; response was None at exit."},
            )
        return response


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
    logger.info("Hello from FastAPI")
    return {"message": "Hello from FastAPI"}


@app.get("/api/health")
async def health_check():
    return {"status": "healthy"}

@app.get("/api/error")
async def error_500_check():
    logger.error("Raising 500 error for testing")

    # raise a 500 error
    raise HTTPException(status_code=500, detail="Test error message")

