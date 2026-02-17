"""
Routes for the Sernia Capital AI agent.

Phase 1: Web chat endpoint with streaming via Vercel AI SDK Data Stream Protocol.
"""
import functools

import logfire
from fastapi import APIRouter
from starlette.requests import Request
from starlette.responses import Response
from pydantic_ai.ui.vercel_ai import VercelAIAdapter

from api.src.ai_sernia.agent import sernia_agent
from api.src.ai_sernia.config import AGENT_NAME, WORKSPACE_PATH
from api.src.ai_sernia.deps import SerniaDeps
from api.src.ai.models import persist_agent_run_result
from api.src.database.database import DBSession

router = APIRouter(prefix="/sernia", tags=["ai", "sernia"])


@router.post(
    "/chat",
    response_class=Response,
    summary="Chat with Sernia Capital AI assistant",
)
async def chat_sernia(request: Request, session: DBSession) -> Response:
    """
    Streaming chat endpoint for the Sernia Capital AI agent.

    Uses PydanticAI's VercelAIAdapter for Vercel AI SDK Data Stream Protocol (SSE).
    Compatible with @ai-sdk/react useChat hook.
    """
    request_json = await request.json()
    conversation_id = request_json.get("id")

    if request_json.get("trigger") == "submit-message":
        messages = request_json.get("messages", [])
        if messages:
            latest = messages[-1]
            logfire.info(
                "sernia chat message",
                endpoint="/api/ai/sernia/chat",
                message_text=(
                    latest.get("parts", [{}])[0].get("text", "")
                    if latest.get("parts")
                    else ""
                ),
            )

    # TODO: Extract real user identity from Clerk auth headers
    # For now, use a placeholder
    user_name = "User"
    user_identifier = "unknown"

    deps = SerniaDeps(
        db_session=session,
        conversation_id=conversation_id or "",
        user_identifier=user_identifier,
        user_name=user_name,
        modality="web_chat",
        workspace_path=WORKSPACE_PATH,
    )

    on_complete_callback = functools.partial(
        persist_agent_run_result,
        conversation_id=conversation_id,
        agent_name=AGENT_NAME,
        clerk_user_id=user_identifier,
        session=session,
    )

    response = await VercelAIAdapter.dispatch_request(
        request,
        agent=sernia_agent,
        deps=deps,
        on_complete=on_complete_callback,
    )

    response.headers["X-Accel-Buffering"] = "no"
    response.headers["Cache-Control"] = "no-cache, no-transform"
    response.headers["X-Content-Type-Options"] = "nosniff"

    return response
