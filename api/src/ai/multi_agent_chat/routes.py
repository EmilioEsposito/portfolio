"""
Routes for Graph-based router agent that dynamically routes to Emilio or Weather agents
"""
import logfire
from fastapi import APIRouter
from starlette.requests import Request
from starlette.responses import Response
from pydantic_ai.ui.vercel_ai.request_types import SubmitMessage

from api.src.ai.multi_agent_chat.graph import (
    MultiAgentInput,
    MultiAgentState,
    multi_agent_graph,
)
from api.src.utils.swagger_schema import expand_json_schema

router = APIRouter(tags=["ai"])


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
                "schema": {"type": "string", "example": "v1"}
            },
            "X-Accel-Buffering": {
                "description": "Disables buffering for streaming",
                "schema": {"type": "string", "example": "no"}
            }
        }
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
                    "parts": [
                        {
                            "type": "text",
                            "text": "What's the weather in San Francisco?"
                        }
                    ]
                }
            ]
        }
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
                    "parts": [
                        {
                            "type": "text",
                            "text": "Tell me about Emilio's projects"
                        }
                    ]
                }
            ]
        }
    }
}

_MULTI_AGENT_OPENAPI_EXTRA = {
    "requestBody": {
        "content": {
            "application/json": {
                "schema": expand_json_schema(SubmitMessage.model_json_schema()),
                "examples": _MULTI_AGENT_REQUEST_EXAMPLES
            }
        },
        "required": True
    }
}


@router.post(
    "/ai/multi-agent-chat",
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
    logfire.info("Multi-agent chat request using Graph Beta API")

    request_json = await request.json()
    user_message = _extract_latest_message_text(request_json)
    message_history = request_json.get("messages") or None

    if request_json.get('trigger') == 'submit-message':
        logfire.info(
            "new multi-agent chat message",
            slack_alert=True,
            endpoint="/api/ai/multi-agent-chat",
            message_text=user_message,
        )

    state = MultiAgentState(
        agent_run_method="vercel_ai",
        vercel_ai_request=request,
        message=user_message,
        message_history=message_history,
    )
    input_data = MultiAgentInput(message=user_message, message_history=message_history)
    graph_result = await multi_agent_graph.run(state=state, inputs=input_data)

    response = graph_result.response
    if not isinstance(response, Response):
        response = Response(content=response)
    # Add headers to prevent browser/proxy buffering
    response.headers["X-Accel-Buffering"] = "no"
    response.headers["Cache-Control"] = "no-cache, no-transform"
    response.headers["X-Content-Type-Options"] = "nosniff"
    
    return response
