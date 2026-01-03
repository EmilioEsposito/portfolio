"""
Top-level AI routes aggregator.

This module combines all AI-related sub-routers into a single router
with the /ai prefix. Only this router needs to be included in index.py.

Hierarchical structure:
- /api/ai/chat-emilio     -> chat_emilio/routes.py
- /api/ai/chat-weather    -> chat_weather/routes.py
- /api/ai/multi-agent-chat -> multi_agent_chat/routes.py
- /api/ai/hitl-agent/*    -> hitl_agents/routes.py
"""
from fastapi import APIRouter

from api.src.ai.chat_emilio.routes import router as chat_emilio_router
from api.src.ai.chat_weather.routes import router as chat_weather_router
from api.src.ai.multi_agent_chat.routes import router as multi_agent_chat_router
from api.src.ai.hitl_agents.routes import router as hitl_agents_router

router = APIRouter(prefix="/ai", tags=["ai"])

# Include sub-routers - each has its own prefix matching folder name
router.include_router(chat_emilio_router)
router.include_router(chat_weather_router)
router.include_router(multi_agent_chat_router)
router.include_router(hitl_agents_router)
