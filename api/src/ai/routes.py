"""
Routes for PydanticAI-powered portfolio chatbot
"""
import logfire
from typing import List
from fastapi import APIRouter
from starlette.requests import Request
from starlette.responses import Response
from pydantic import BaseModel
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse, 
    UserPromptPart,
    TextPart,
)
from pydantic_ai.ui.vercel_ai import VercelAIAdapter
from pydantic_ai.ui.vercel_ai.request_types import RequestData, SubmitMessage

from api.src.ai.agent import agent, PortfolioContext
from api.src.utils.swagger_schema import expand_json_schema

router = APIRouter(tags=["ai"])



# Use PydanticAI's RequestData for type checking/documentation
# RequestData is a union of SubmitMessage | RegenerateMessage
# Both have: trigger, id, messages: list[UIMessage]
# where UIMessage has: id, role, parts: list[UIMessagePart]

# Essential Documentation Links - DO NOT REMOVE:
# https://ai.pydantic.dev/ui/vercel-ai/
# https://ai-sdk.dev/docs/ai-sdk-ui/stream-protocol#data-stream-protocol


# Swagger/OpenAPI documentation for chat-emilio endpoint
_CHAT_EMILIO_RESPONSES = {
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


_CHAT_EMILIO_REQUEST_EXAMPLES = {
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
                            "text": "What technologies does Emilio work with?"
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
                    "parts": [{"type": "text", "text": "Tell me about Emilio's projects"}]
                }
            ]
        }
    }
}

_CHAT_EMILIO_OPENAPI_EXTRA = {
    "requestBody": {
        "content": {
            "application/json": {
                "schema": expand_json_schema(SubmitMessage.model_json_schema()),
                "examples": _CHAT_EMILIO_REQUEST_EXAMPLES
            }
        },
        "required": True
    }
}




# https://ai.pydantic.dev/ui/vercel-ai/
@router.post(
    "/ai/chat-emilio",
    response_class=Response,
    responses=_CHAT_EMILIO_RESPONSES,
    summary="Chat with Emilio's portfolio assistant",
    openapi_extra=_CHAT_EMILIO_OPENAPI_EXTRA,
)
async def chat_emilio(request: Request) -> Response:
    """
    Chat endpoint using PydanticAI's VercelAIAdapter.

    This endpoint streams responses using the Vercel AI SDK Data Stream Protocol (SSE format).
    Compatible with @ai-sdk/react v2.0.92+ useChat hook.

    **Response:**
    Returns a Server-Sent Events (SSE) stream with Content-Type: `text/event-stream`.
    Each event follows the Vercel AI SDK Data Stream Protocol format.
    """
    logfire.info("Portfolio chat request using VercelAIAdapter")

    # Log new messages
    request_json = await request.json()
    if request_json.get('trigger') == 'submit-message':
        messages = request_json.get('messages', [])
        # Structured logging for easy querying/alerting in Logfire UI
        latest_message = messages[-1]
        logfire.info("new chat message",
            slack_alert=True,
            endpoint="/api/ai/chat-emilio",
            message_text=latest_message.get('parts', [{}])[0].get('text', '') if latest_message.get('parts') else '',
        )

    response = await VercelAIAdapter.dispatch_request(
        request,
        agent=agent,
        deps=PortfolioContext(user_name="visitor"),
    )
    
    # Add headers to prevent browser/proxy buffering
    # X-Accel-Buffering: no tells nginx and browsers not to buffer the response
    response.headers["X-Accel-Buffering"] = "no"
    response.headers["Cache-Control"] = "no-cache, no-transform"
    response.headers["X-Content-Type-Options"] = "nosniff"
    
    return response
