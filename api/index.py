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

# Import from api_src
from api_src.chat.routes import router as chat_router
from api_src.open_phone import router as open_phone_router
from api_src.cron import router as cron_router
from api_src.google.routes import router as google_router

# Import all GraphQL schemas
from api_src.examples.schema import Query as ExamplesQuery, Mutation as ExamplesMutation
# from api_src.future_features.schema import Query as FutureQuery, Mutation as FutureMutation
# from api_src.another_feature.schema import Query as AnotherQuery, Mutation as AnotherMutation

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

app = FastAPI(docs_url="/api/docs", openapi_url="/api/openapi.json")

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



