"""
AI demo routes aggregator.

This module combines all AI demo sub-routers into a single router
with the /ai-demos prefix.

Hierarchical structure:
- /api/ai-demos/chat-emilio      -> chat_emilio/routes.py
- /api/ai-demos/chat-weather     -> chat_weather/routes.py
- /api/ai-demos/multi-agent-chat -> multi_agent_chat/routes.py
- /api/ai-demos/hitl-agent/*     -> hitl_agents/routes.py
"""
from fastapi import APIRouter, Depends

from api.src.ai_demos.chat_emilio.routes import router as chat_emilio_router
from api.src.ai_demos.chat_weather.routes import router as chat_weather_router
from api.src.ai_demos.multi_agent_chat.routes import router as multi_agent_chat_router
from api.src.ai_demos.hitl_agents.routes import router as hitl_agents_router
from api.src.utils.rate_limit import RateLimiter

# Public demo endpoints: 20 requests per minute per IP
_demo_rate_limit = RateLimiter(max_requests=20, window_seconds=60)

router = APIRouter(prefix="/ai-demos", tags=["ai-demos"])

# Public demo sub-routers (rate-limited)
_public_router = APIRouter(dependencies=[Depends(_demo_rate_limit)])
_public_router.include_router(chat_emilio_router)
_public_router.include_router(chat_weather_router)
_public_router.include_router(multi_agent_chat_router)
router.include_router(_public_router)

# HITL agent (already auth-gated via SerniaUser, no public rate limit needed)
router.include_router(hitl_agents_router)
