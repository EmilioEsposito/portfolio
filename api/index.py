import logging
from dotenv import load_dotenv, find_dotenv

# Load local development variables (does not impact preview/production)
load_dotenv(find_dotenv(".env.development.local"), override=True)


from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from strawberry.fastapi import GraphQLRouter
from strawberry.tools import merge_types
import strawberry
import os
from starlette.middleware.sessions import SessionMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
import traceback

# Import from api_src
from api_src.chat.routes import router as chat_router
from api_src.open_phone import router as open_phone_router
from api_src.cron import router as cron_router
from api_src.google.common.routes import router as google_router
from api_src.examples.routes import router as examples_router
from api_src.google.gmail.service import send_email
from api_src.google.common.service_account_auth import get_delegated_credentials

# Import all GraphQL schemas
from api_src.examples.schema import Query as ExamplesQuery, Mutation as ExamplesMutation

# from api_src.future_features.schema import Query as FutureQuery, Mutation as FutureMutation
# from api_src.another_feature.schema import Query as AnotherQuery, Mutation as AnotherMutation


# Verify critical environment variables
required_env_vars = {
    "SESSION_SECRET_KEY": (
        "Required for secure session handling. "
        "Generate unique values for each environment:\n"
        "  Development: vercel env add SESSION_SECRET_KEY development $(openssl rand -hex 32)\n"
        "  Preview: vercel env add SESSION_SECRET_KEY preview $(openssl rand -hex 32)\n"
        "  Production: vercel env add SESSION_SECRET_KEY production $(openssl rand -hex 32)"
    ),
    "GOOGLE_OAUTH_CLIENT_ID": (
        "Required for Google OAuth. Set up in Google Cloud Console.\n"
        "Create separate OAuth 2.0 Client IDs for each environment and add with:\n"
        "  Development: vercel env add GOOGLE_OAUTH_CLIENT_ID development\n"
        "  Preview: vercel env add GOOGLE_OAUTH_CLIENT_ID preview\n"
        "  Production: vercel env add GOOGLE_OAUTH_CLIENT_ID production"
    ),
    "GOOGLE_OAUTH_CLIENT_SECRET": (
        "Required for Google OAuth. Set up in Google Cloud Console.\n"
        "Use the corresponding client secrets for each environment's OAuth 2.0 Client ID:\n"
        "  Development: vercel env add GOOGLE_OAUTH_CLIENT_SECRET development\n"
        "  Preview: vercel env add GOOGLE_OAUTH_CLIENT_SECRET preview\n"
        "  Production: vercel env add GOOGLE_OAUTH_CLIENT_SECRET production"
    ),
    "GOOGLE_OAUTH_REDIRECT_URI": (
        "Required for Google OAuth. Must match the URIs configured in Google Cloud Console:\n"
        "  Development: vercel env add GOOGLE_OAUTH_REDIRECT_URI development 'http://localhost:3000/api/google/auth/callback'\n"
        "  Preview: vercel env add GOOGLE_OAUTH_REDIRECT_URI preview 'https://dev.eesposito.com/api/google/auth/callback'\n"
        "  Production: vercel env add GOOGLE_OAUTH_REDIRECT_URI production 'https://eesposito.com/api/google/auth/callback'"
    ),
    "OPEN_PHONE_WEBHOOK_SECRET": (
        "Required for OpenPhone webhook. Set up in OpenPhone dashboard.\n"
        "  Development: vercel env add OPEN_PHONE_WEBHOOK_SECRET development\n"
        "  Preview: vercel env add OPEN_PHONE_WEBHOOK_SECRET preview\n"
        "  Production: vercel env add OPEN_PHONE_WEBHOOK_SECRET production"
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
        + "\nAfter setting variables, pull them locally with: vercel env pull .env.development.local"
    )

app = FastAPI(docs_url="/api/docs", openapi_url="/api/openapi.json")

# Configure logging
# TODO: Configure logging properly - TBD on best practice for Vercel Serverless Functions
# logging.basicConfig(level=logging.INFO)
# logger = logging.getlogging(__name__)


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
    logging.error(
        f"500 Error Detail: Path={error_details['path']}, Method={error_details['method']}, Error={error_details['error']}",
        exc_info=exc if exc.__traceback__ else None # Only add exc_info if there's a real traceback
    )


    try:
        # Send email notification using service account
        credentials = get_delegated_credentials(
            user_email="emilio@serniacapital.com",  # TODO: Move to env var?
            scopes=["https://mail.google.com"],
        )
        message_text = f"A 500 error occurred on your application ({os.getenv('VERCEL_ENV', 'local')})."
        message_text += f"Error: {error_details['error']}"
        message_text += f"Path: {error_details['path']}"
        message_text += f"Method: {error_details['method']}"
        message_text += f"Client IP: {error_details['client_host']}"
        message_text += f"Traceback:\n{error_details['traceback']}"

        send_email(
            to="espo412@gmail.com",  # TODO: Move to env var?
            subject=f"ALERT: 500 Error on {os.getenv('VERCEL_ENV', 'unknown environment')}",
            message_text=message_text,
            credentials=credentials,
        )
        logging.info(f"Error notification email sent for 500 on {error_details['path']}")
    except Exception as email_error:
        logging.error(f"Failed to send error notification email: {str(email_error)}", exc_info=True)


# --- Middleware Definitions ---

class ErrorHandlingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        try:
            # Try processing the request
            response = await call_next(request)

            # Check if the response is a 500 error (from HTTPException or manual return)
            if response.status_code == 500:
                logging.warning(f"Caught handled 500 response for {request.url.path}")
                # Create a generic exception to pass details to the notifier
                handled_500_exception = Exception(f"Handled 500 Response for {request.url.path}")
                await send_error_notification(request, handled_500_exception)

        except Exception as exc:
            # Catch any uncaught exceptions from the application
            logging.error(f"Caught unhandled exception for {request.url.path}", exc_info=True)
            await send_error_notification(request, exc)

            # Return a standard 500 response
            # Note: We are generating the response here. If you wanted FastAPI's default
            # exception handling to still run for specific exception types *after* notification,
            # you might re-raise the exception here instead of returning a response.
            # For general uncaught exceptions, returning a generic 500 is usually desired.
            response = JSONResponse(
                status_code=500,
                content={
                    "error": "Internal Server Error",
                    "detail": "An unexpected error occurred.", # Keep detail generic for security
                    "status_code": 500,
                },
            )
        return response


# --- Middleware Registration ---
# Order matters: Middlewares process requests top-to-bottom, responses bottom-to-top.
# Error handling should wrap everything, so it's usually added early (but after essential ones like CORS/Session).

is_hosted = len(os.getenv("VERCEL_ENV","")) > 0 or len(os.getenv("RAILWAY_ENVIRONMENT_NAME","") > 0)

# Add session middleware - MUST be added before CORS middleware
app.add_middleware(
    SessionMiddleware,
    secret_key=os.getenv("SESSION_SECRET_KEY"),  # Will raise error if not set
    same_site="lax",  # Required for OAuth redirects
    # TODO: Set secure=True for production based on env var? Needs testing.
    # secure=os.getenv("VERCEL_ENV") == "production", # Enable for production HTTPS only
    https_only=is_hosted,
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], # TODO: Restrict in production? ["https://eesposito.com", "https://*.vercel.app"] ?
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
app.include_router(chat_router, prefix="/api")
app.include_router(open_phone_router, prefix="/api")
app.include_router(cron_router, prefix="/api")
app.include_router(google_router, prefix="/api")
app.include_router(examples_router, prefix="/api")


@app.get("/api/hello")
async def hello_fast_api():
    logging.info("Hello from FastAPI")
    return {"message": "Hello from FastAPI"}


@app.get("/api/health")
async def health_check():
    return {"status": "healthy"}
