"""
Main router for Google API endpoints.
"""

from fastapi import APIRouter
from api_src.google.gmail.routes import router as gmail_router
from api_src.google.pubsub.routes import router as pubsub_router
# from api_src.google.sheets.routes import router as sheets_router

# Create main router
router = APIRouter(prefix="/google", tags=["google"])

# Include subrouters
router.include_router(gmail_router)
router.include_router(pubsub_router)
# router.include_router(sheets_router)
