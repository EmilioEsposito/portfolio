"""
Routes for PydanticAI-powered portfolio chatbot
"""
import logging
from typing import List
from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse, 
    UserPromptPart,
    TextPart,
    SystemPromptPart,
)

from api.src.ai.agent import agent, PortfolioContext

logger = logging.getLogger(__name__)

router = APIRouter()


class Message(BaseModel):
    """Message model for chat requests"""
    role: str
    content: str


class ChatRequest(BaseModel):
    """Chat request with message history"""
    messages: List[Message]


def convert_to_pydantic_messages(messages: List[Message]) -> List[ModelMessage]:
    """
    Convert frontend messages to PydanticAI message format.
    
    Args:
        messages: List of messages from the frontend
        
    Returns:
        List of PydanticAI ModelMessage objects
    """
    pydantic_messages: List[ModelMessage] = []
    
    for msg in messages:
        if msg.role == "user":
            pydantic_messages.append(
                ModelRequest(parts=[UserPromptPart(content=msg.content)])
            )
        elif msg.role == "assistant":
            pydantic_messages.append(
                ModelResponse(parts=[TextPart(content=msg.content)])
            )
        elif msg.role == "system":
            # System messages are typically handled by the agent's system_prompt
            # but we can include them if needed
            pydantic_messages.append(
                ModelRequest(parts=[SystemPromptPart(content=msg.content)])
            )
    
    return pydantic_messages


async def stream_chat_response(messages: List[Message]):
    """
    Stream chat responses using PydanticAI agent with Vercel AI SDK format.
    
    Args:
        messages: Chat message history
        
    Yields:
        Server-sent events in Vercel AI SDK format
    """
    import json
    
    try:
        # Get the last user message
        last_user_message = None
        for msg in reversed(messages):
            if msg.role == "user":
                last_user_message = msg.content
                break
        
        if not last_user_message:
            yield '0:"No user message found"\n'
            yield 'e:{"finishReason":"error","usage":{"promptTokens":0,"completionTokens":0},"isContinued":false}\n'
            return
        
        # Build message history (excluding the last user message)
        message_history = convert_to_pydantic_messages(messages[:-1]) if len(messages) > 1 else []
        
        # Create context
        ctx = PortfolioContext(user_name="visitor")
        
        # Stream the response
        async with agent.run_stream(
            last_user_message,
            message_history=message_history,
            deps=ctx,
        ) as result:
            async for text in result.stream_text(delta=True):
                # Vercel AI SDK format: type:payload\n
                # type 0 = text chunk
                if text:
                    yield f'0:{json.dumps(text)}\n'
            
            # Get final result for usage stats
            final_result = await result.get_data()
            
            # Send finish event
            # type e = end/finish
            usage_data = {
                "finishReason": "stop",
                "usage": {
                    "promptTokens": 0,  # PydanticAI doesn't expose these directly
                    "completionTokens": 0,
                },
                "isContinued": False,
            }
            yield f'e:{json.dumps(usage_data)}\n'
            
    except Exception as e:
        logger.error(f"Error in stream_chat_response: {e}", exc_info=True)
        yield f'0:{json.dumps(f"Error: {str(e)}")}\n'
        yield 'e:{"finishReason":"error","usage":{"promptTokens":0,"completionTokens":0},"isContinued":false}\n'


@router.post("/ai/chat")
async def portfolio_chat(request: ChatRequest):
    """
    Chat endpoint for portfolio assistant.
    
    Streams responses using PydanticAI and formats them for Vercel AI SDK.
    """
    logger.info(f"Portfolio chat request with {len(request.messages)} messages")
    
    response = StreamingResponse(
        stream_chat_response(request.messages),
        media_type="text/plain",
    )
    response.headers["x-vercel-ai-data-stream"] = "v1"
    return response


@router.get("/ai/health")
async def ai_health_check():
    """Health check endpoint for AI service"""
    return {"status": "healthy", "service": "portfolio-ai"}
