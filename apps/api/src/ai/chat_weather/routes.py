"""
Routes for general-purpose chat with weather tool support
"""
from fastapi import APIRouter
from starlette.requests import Request
from starlette.responses import Response
from pydantic_ai.ui.vercel_ai import VercelAIAdapter
from pydantic_ai.ui.vercel_ai.request_types import SubmitMessage

from apps.api.src.ai.chat_weather.agent import agent, ChatContext
from apps.api.src.utils.swagger_schema import expand_json_schema
import logfire

router = APIRouter(tags=["chat"])


# Use PydanticAI's RequestData for type checking/documentation
# RequestData is a union of SubmitMessage | RegenerateMessage
# Both have: trigger, id, messages: list[UIMessage]
# where UIMessage has: id, role, parts: list[UIMessagePart]


# Swagger/OpenAPI documentation for chat endpoint
_CHAT_RESPONSES = {
    200: {
        "description": "Server-Sent Events (SSE) stream using Vercel AI SDK Data Stream Protocol",
        "content": {
            "text/event-stream": {
                "example": """data: {"type":"start"}
data: {"type":"text-start","id":"msg-123"}
data: {"type":"text-delta","id":"msg-123","delta":"Let me check the weather for you."}
data: {"type":"text-end","id":"msg-123"}
data: {"type":"tool-input-start","toolCallId":"call_abc123","toolName":"get_current_weather"}
data: {"type":"tool-input-delta","toolCallId":"call_abc123","inputTextDelta":"{\\"latitude\\":40.7128"}
data: {"type":"tool-input-delta","toolCallId":"call_abc123","inputTextDelta":",\\"longitude\\":-74.0060}"}
data: {"type":"tool-input-available","toolCallId":"call_abc123","toolName":"get_current_weather","input":{"latitude":40.7128,"longitude":-74.0060}}
data: {"type":"tool-output-available","toolCallId":"call_abc123","output":{"current":{"temperature_2m":22.5,"time":"2024-01-15T12:00"},"hourly":{"temperature_2m":[20,21,22,23,24,25]},"daily":{"sunrise":["2024-01-15T07:00"],"sunset":["2024-01-15T17:00"]}}}
data: {"type":"text-start","id":"msg-124"}
data: {"type":"text-delta","id":"msg-124","delta":"The weather in New York is currently 22.5°C."}
data: {"type":"text-end","id":"msg-124"}
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


_CHAT_REQUEST_EXAMPLES = {
    "single_message": {
        "summary": "Single user message",
        "description": "Send a single message to start a conversation",
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
                            "text": "What's the weather like in New York?"
                        }
                    ]
                }
            ]
        }
    },
    "conversation": {
        "summary": "Conversation with history",
        "description": "Send a message with conversation history",
        "value": {
            "trigger": "submit-message",
            "id": "550e8400-e29b-41d4-a716-446655440002",
            "messages": [
                {
                    "id": "550e8400-e29b-41d4-a716-446655440003",
                    "role": "user",
                    "parts": [{"type": "text", "text": "Hello"}]
                },
                {
                    "id": "550e8400-e29b-41d4-a716-446655440004",
                    "role": "assistant",
                    "parts": [{"type": "text", "text": "Hi there! How can I help you?"}]
                },
                {
                    "id": "550e8400-e29b-41d4-a716-446655440005",
                    "role": "user",
                    "parts": [{"type": "text", "text": "Can you check the weather?"}]
                }
            ]
        }
    }
}

_CHAT_OPENAPI_EXTRA = {
    "requestBody": {
        "content": {
            "application/json": {
                "schema": expand_json_schema(SubmitMessage.model_json_schema()),
                "examples": _CHAT_REQUEST_EXAMPLES
            }
        },
        "required": True
    }
}


@router.post(
    "/ai/chat-weather",
    response_class=Response,
    responses=_CHAT_RESPONSES,
    summary="General-purpose chat with weather tool support",
    openapi_extra=_CHAT_OPENAPI_EXTRA,
)
async def chat(request: Request) -> Response:
    """
    Chat endpoint using PydanticAI's VercelAIAdapter.

    This endpoint streams responses using the Vercel AI SDK Data Stream Protocol (SSE format).
    Compatible with @ai-sdk/react v2.0.92+ useChat hook.

    **Features:**
    - General-purpose conversational AI
    - Weather tool for getting current weather at any location
    - Streaming responses in real-time

    **Response:**
    Returns a Server-Sent Events (SSE) stream with Content-Type: `text/event-stream`.
    Each event follows the Vercel AI SDK Data Stream Protocol format.
    """
    logfire.info("Weather chat request using VercelAIAdapter")

    # Log new messages
    request_json = await request.json()
    if request_json.get('trigger') == 'submit-message':
        messages = request_json.get('messages', [])
        # Structured logging for easy querying/alerting in Logfire UI
        latest_message = messages[-1]
        logfire.info("new chat message",
            slack_alert=True,
            endpoint="/api/ai/chat-weather",
            message_text=latest_message.get('parts', [{}])[0].get('text', '') if latest_message.get('parts') else '',
        )
    
    # Use VercelAIAdapter to handle the request and stream response
    # Note: VercelAIAdapter.dispatch_request expects a raw Request object
    # and handles parsing internally, so we can't use Pydantic validation here
    response = await VercelAIAdapter.dispatch_request(
        request,
        agent=agent,
        deps=ChatContext(),
    )
    
    # Add headers to prevent browser/proxy buffering
    # X-Accel-Buffering: no tells nginx and browsers not to buffer the response
    response.headers["X-Accel-Buffering"] = "no"
    response.headers["Cache-Control"] = "no-cache, no-transform"
    response.headers["X-Content-Type-Options"] = "nosniff"
    
    return response 