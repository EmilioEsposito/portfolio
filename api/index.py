# this file is entry point for the API, so we need to import custom logger here
from api.logger import logger 
from dotenv import load_dotenv, find_dotenv
# Load local development variables (does not impact preview/production)
load_dotenv(find_dotenv(".env.development.local"), override=True)

import logging
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from strawberry.fastapi import GraphQLRouter
from strawberry.tools import merge_types
import strawberry
import os
from starlette.middleware.sessions import SessionMiddleware

# Import from api_src
from api_src.chat.routes import router as chat_router
from api_src.open_phone import router as open_phone_router
from api_src.cron import router as cron_router
from api_src.google.common.routes import router as google_router
from api_src.examples.routes import router as examples_router

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
    )
}

missing_vars = []
for var, description in required_env_vars.items():
    if not os.getenv(var):
        missing_vars.append(f"- {var}:\n{description}\n")

if missing_vars:
    raise ValueError(
        "Missing required environment variables:\n\n" + 
        "\n".join(missing_vars) +
        "\nAfter setting variables, pull them locally with: vercel env pull .env.development.local"
    )

app = FastAPI(docs_url="/api/docs", openapi_url="/api/openapi.json")

# Add session middleware - MUST be added before CORS middleware
app.add_middleware(
    SessionMiddleware,
    secret_key=os.getenv("SESSION_SECRET_KEY"),  # Will raise error if not set
    same_site="lax",  # Required for OAuth redirects
    https_only=False  # Set to True in production
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

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

# Add error handling
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error(f"Error processing request: {str(exc)}", exc_info=True)
    
    # Handle HTTPException specially
    if isinstance(exc, HTTPException):
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": str(exc.detail),
                "detail": str(exc.detail),
                "status_code": exc.status_code
            }
        )
    
    # Handle other exceptions
    return JSONResponse(
        status_code=500,
        content={
            "error": str(exc),
            "detail": "Internal Server Error",
            "status_code": 500
        }
    )

@app.get("/api/hello")
async def hello_fast_api():
    return {"message": "Hello from FastAPI"}

@app.get("/api/health")
async def health_check():
    return {"status": "healthy"}



