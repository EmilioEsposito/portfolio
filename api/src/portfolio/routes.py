"""
Public AI demo routes aggregator (eesposito.com).

This module combines the public demo sub-routers into a single router
with the /ai-demos prefix.

Hierarchical structure:
- /api/ai-demos/chat-emilio      -> chat_emilio/routes.py
- /api/ai-demos/chat-weather     -> chat_weather/routes.py
- /api/ai-demos/multi-agent-chat -> multi_agent_chat/routes.py
"""

from fastapi import APIRouter

from api.src.portfolio.chat_emilio.routes import router as chat_emilio_router
from api.src.portfolio.chat_weather.routes import router as chat_weather_router
from api.src.portfolio.multi_agent_chat.routes import router as multi_agent_chat_router

router = APIRouter(prefix="/ai-demos", tags=["ai-demos"])

# Public demo sub-routers (rate-limited)
_public_router = APIRouter()
_public_router.include_router(chat_emilio_router)
_public_router.include_router(chat_weather_router)
_public_router.include_router(multi_agent_chat_router)
router.include_router(_public_router)
