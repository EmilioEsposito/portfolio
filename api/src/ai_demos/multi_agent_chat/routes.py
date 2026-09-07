"""
Routes for Graph-based router agent that dynamically routes to Emilio or Weather agents
"""

import asyncio
import json
import re

import logfire
from fastapi import APIRouter
from pydantic_ai.ui.vercel_ai.request_types import SubmitMessage
from starlette.requests import Request
from starlette.responses import Response, StreamingResponse

from api.src.ai_demos.multi_agent_chat.graph import (
    MultiAgentInput,
    MultiAgentState,
    multi_agent_graph,
)
from api.src.utils.input_sanitization import sanitize_request_json
from api.src.utils.swagger_schema import expand_json_schema

router = APIRouter(prefix="/multi-agent-chat", tags=["ai"])


_PUBLIC_BUDGET_ERROR = "Public demo budget is exhausted. Please try again later."
_PUBLIC_RATE_ERROR = (
    "The public demo is busy or has reached its usage limit. Please try again later."
)
_PUBLIC_SERVICE_ERROR = "The run could not finish. Please try again shortly."


def _public_error_message(error: Exception | str) -> str:
    """Classify provider failures without forwarding provider bodies to visitors."""
    status = getattr(error, "status_code", None)
    # The Vercel adapter serializes ModelHTTPError to errorText, losing its type.
    if status is None:
        match = re.search(
            r"(?:status_code|status code|error code)[\s:=]+(402|429)\b", str(error), re.IGNORECASE
        )
        status = int(match.group(1)) if match else None
    if status == 402:
        return _PUBLIC_BUDGET_ERROR
    if status == 429:
        return _PUBLIC_RATE_ERROR
    return _PUBLIC_SERVICE_ERROR


def _extract_latest_message_text(request_payload: dict) -> str:
    """Return the text from the most recent UI message payload."""
    messages = request_payload.get("messages") or []
    if not messages:
        return ""

    latest = messages[-1] or {}
    for part in latest.get("parts", []):
        if part.get("type") == "text" and part.get("text"):
            return part["text"]
    return ""


# Swagger/OpenAPI documentation for multi-agent endpoint
_MULTI_AGENT_RESPONSES = {
    200: {
        "description": "Server-Sent Events (SSE) stream using Vercel AI SDK Data Stream Protocol",
        "content": {
            "text/event-stream": {
                "example": """data: {"type":"start"}
data: {"type":"text-start","id":"msg-123"}
data: {"type":"text-delta","id":"msg-123","delta":"Hello"}
data: {"type":"text-delta","id":"msg-123","delta":" there"}
data: {"type":"text-end","id":"msg-123"}
data: {"type":"finish"}
data: [DONE]"""
            }
        },
        "headers": {
            "x-vercel-ai-ui-message-stream": {
                "description": "Vercel AI SDK stream version",
                "schema": {"type": "string", "example": "v1"},
            },
            "X-Accel-Buffering": {
                "description": "Disables buffering for streaming",
                "schema": {"type": "string", "example": "no"},
            },
        },
    }
}


_MULTI_AGENT_REQUEST_EXAMPLES = {
    "single_message": {
        "summary": "Single user message",
        "description": "Send a single message that will be routed to the appropriate agent",
        "value": {
            "trigger": "submit-message",
            "id": "550e8400-e29b-41d4-a716-446655440000",
            "messages": [
                {
                    "id": "550e8400-e29b-41d4-a716-446655440001",
                    "role": "user",
                    "parts": [{"type": "text", "text": "What's the weather in San Francisco?"}],
                }
            ],
        },
    },
    "emilio_question": {
        "summary": "Question about Emilio",
        "description": "Ask about Emilio's portfolio or experience",
        "value": {
            "trigger": "submit-message",
            "id": "550e8400-e29b-41d4-a716-446655440002",
            "messages": [
                {
                    "id": "550e8400-e29b-41d4-a716-446655440003",
                    "role": "user",
                    "parts": [{"type": "text", "text": "Tell me about Emilio's projects"}],
                }
            ],
        },
    },
}

_MULTI_AGENT_OPENAPI_EXTRA = {
    "requestBody": {
        "content": {
            "application/json": {
                "schema": expand_json_schema(SubmitMessage.model_json_schema()),
                "examples": _MULTI_AGENT_REQUEST_EXAMPLES,
            }
        },
        "required": True,
    }
}


@router.post(
    "",
    response_class=Response,
    responses=_MULTI_AGENT_RESPONSES,
    summary="Unified chat endpoint with dynamic agent routing",
    openapi_extra=_MULTI_AGENT_OPENAPI_EXTRA,
)
async def multi_agent_chat(request: Request) -> Response:
    """
    Unified chat endpoint using PydanticAI's Graph Beta API for dynamic routing.

    This endpoint automatically routes user messages to the appropriate specialized agent:
    - **Emilio Agent**: For questions about Emilio Esposito, portfolio, skills, projects, etc.
    - **Weather Agent**: For weather-related questions and forecasts

    The routing is handled by Pydantic AI's Graph Beta API with decisions, which uses an LLM-based router agent
    to analyze the message and route it to the correct agent based on content.

    **Response:**
    Returns a Server-Sent Events (SSE) stream with Content-Type: `text/event-stream`.
    Each event follows the Vercel AI SDK Data Stream Protocol format.
    """
    logfire.info("LOGFIRE: Multi-agent chat request using Graph Beta API")
    logfire.info("CLASSIC LOGGER: Multi-agent chat request using Graph Beta API")

    # Read and sanitize request body to prevent SSRF attacks via document-url parts
    request_json = await request.json()
    sanitized_json = sanitize_request_json(request_json)
    # Replace request body so downstream agents (VercelAIAdapter) use sanitized version
    request._body = json.dumps(sanitized_json).encode()

    user_message = _extract_latest_message_text(sanitized_json)
    # No explicit message_history: VercelAIAdapter parses the full
    # conversation (history + latest prompt) from the request body itself.
    # `message_history` is only for *additional* ModelMessage objects (e.g.
    # DB-loaded history) — raw Vercel UI dicts are rejected by pydantic-ai
    # >=1.9x ("'dict' object has no attribute 'parts'").

    async def stream():
        queue: asyncio.Queue[dict | None] = asyncio.Queue()

        def activity(node: str, status: str) -> None:
            queue.put_nowait(
                {
                    "type": "data-agent-activity",
                    "data": {"node": node, "status": status},
                    "transient": True,
                }
            )

        state = MultiAgentState(
            agent_run_method="vercel_ai",
            vercel_ai_request=request,
            message=user_message,
            on_activity=activity,
        )

        async def run_graph():
            try:
                return await multi_agent_graph.run(
                    state=state, inputs=MultiAgentInput(message=user_message)
                )
            finally:
                queue.put_nowait(None)

        task = asyncio.create_task(run_graph())
        try:
            # Stream graph transitions while the router is actually running.
            async with asyncio.timeout(60):
                while True:
                    event = await queue.get()
                    if event is None:
                        break
                    yield f"data: {json.dumps(event)}\n\n"
                result = await task
                response = result.response
                if not isinstance(response, StreamingResponse):
                    raise RuntimeError("Expected an agent stream")
                stream_failed = False
                pending = ""
                async for chunk in response.body_iterator:
                    pending += chunk.decode() if isinstance(chunk, bytes) else chunk
                    # Parse complete SSE frames: transport chunks need not align
                    # with event boundaries. Never leak a raw adapter errorText.
                    while "\n\n" in pending:
                        frame, pending = pending.split("\n\n", 1)
                        if not frame.startswith("data: "):
                            continue
                        payload = frame[6:]
                        if payload == "[DONE]":
                            continue
                        event = json.loads(payload)
                        if event.get("type") == "error":
                            stream_failed = True
                            event = {
                                "type": "error",
                                "errorText": _public_error_message(event.get("errorText", "")),
                            }
                        elif "errorText" in event:
                            event["errorText"] = "The tool could not finish."
                        yield f"data: {json.dumps(event)}\n\n"
                if pending.strip():
                    raise RuntimeError("Incomplete agent stream")
                if not stream_failed:
                    activity(result.agent_name, "complete")
                    event = await queue.get()
                    yield f"data: {json.dumps(event)}\n\n"
                yield "data: [DONE]\n\n"
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logfire.warning("Agent showcase stream failed", error_type=type(exc).__name__)
            event = {"type": "error", "errorText": _public_error_message(exc)}
            yield f"data: {json.dumps(event)}\n\n"
            yield "data: [DONE]\n\n"
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={
            "x-vercel-ai-ui-message-stream": "v1",
            "X-Accel-Buffering": "no",
            "Cache-Control": "no-cache, no-transform",
            "X-Content-Type-Options": "nosniff",
        },
    )
