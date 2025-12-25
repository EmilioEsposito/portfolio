"""
HITL Agent 3 Routes

Two interaction modes, both using the same simple dual-run pattern:
1. Streaming Chat (/chat) - Real-time interaction via Vercel AI SDK
2. Workflow API (/conversation/*) - Start conversations and manage approvals

The approval flow is the same for both:
- First run returns DeferredToolRequests → saved to DB
- Frontend shows approval UI
- Approval triggers second run with DeferredToolResults
"""
import json
import uuid
import functools
import asyncio
import logfire

from fastapi import APIRouter, HTTPException, Depends
from starlette.requests import Request
from starlette.responses import Response
from pydantic import BaseModel
from pydantic_ai.ui.vercel_ai import VercelAIAdapter
from pydantic_ai.ui.vercel_ai.request_types import SubmitMessage

from api.src.ai.hitl_agents.hitl_agent3 import (
    hitl_agent3,
    HITLAgentContext,
    run_agent_with_persistence,
    resume_with_approval,
    list_pending_conversations,
    get_conversation_with_pending,
    extract_pending_approval,
    extract_pending_approval_from_messages,
)
from api.src.ai.models import (
    persist_agent_run_result,
    list_user_conversations,
    get_conversation_messages,
    verify_conversation_ownership,
    delete_conversation,
)
from api.src.utils.swagger_schema import expand_json_schema
from api.src.utils.clerk import verify_serniacapital_user, get_auth_user
from api.src.database.database import DBSession
from clerk_backend_api import User
from typing import Annotated

router = APIRouter(
    prefix="/ai/hitl-agent",
    tags=["hitl-agent"],
    dependencies=[Depends(verify_serniacapital_user)]
)

# Type alias for injected Clerk user
AuthUser = Annotated[User, Depends(get_auth_user)]


# =============================================================================
# Streaming Chat Mode (Vercel AI SDK)
# =============================================================================

_CHAT_RESPONSES = {
    200: {
        "description": "Server-Sent Events (SSE) stream using Vercel AI SDK Data Stream Protocol",
        "content": {
            "text/event-stream": {
                "example": """data: {"type":"start"}
data: {"type":"tool-input-available","toolCallId":"call_123","toolName":"send_sms","input":{"body":"Hello!"}}
data: {"type":"finish"}
data: [DONE]"""
            }
        },
    }
}


class _ModifiedJsonRequest:
    """Wrapper to provide modified JSON body to VercelAIAdapter."""

    def __init__(self, original_request: Request, modified_body: dict):
        self._original = original_request
        self._body = json.dumps(modified_body).encode()

    async def body(self) -> bytes:
        return self._body

    @property
    def headers(self):
        return self._original.headers

    def __getattr__(self, name):
        return getattr(self._original, name)


@router.post("/chat", response_class=Response, responses=_CHAT_RESPONSES)
async def chat(request: Request, user: AuthUser, session: DBSession) -> Response:
    """
    Streaming chat endpoint for real-time interaction.

    When the agent needs approval, the stream includes tool-input-available events.
    The frontend should display an approval UI and call /conversation/{id}/approve.

    Message flow:
    - Frontend sends messages (we only use the last one - the new message)
    - Backend loads message_history from DB (authoritative source)
    - VercelAIAdapter combines them and runs the agent
    - result.all_messages() returns the complete conversation to persist
    """
    logfire.info("HITL agent chat request")

    user_id = user.id

    request_json = await request.json()
    conversation_id = request_json.get("id")
    frontend_messages = request_json.get("messages", [])

    # Load message history from DB (authoritative source)
    backend_message_history = await get_conversation_messages(conversation_id, session=session)

    # If conversation exists, verify ownership using Clerk user ID
    if backend_message_history:
        try:
            await verify_conversation_ownership(conversation_id, user_id, session)
        except ValueError as e:
            raise HTTPException(status_code=404, detail="Error verifying conversation ownership: " + str(e))

    logfire.info(f"Chat conversation_id: {conversation_id}, user_id: {user_id}, frontend_msg_count: {len(frontend_messages)}, db_msg_count: {len(backend_message_history)}")

    # Only use the LAST message from frontend (the new user input)
    # Backend DB has the authoritative history
    last_message = frontend_messages[-1] if frontend_messages else None
    modified_body = {
        **request_json,
        "messages": [last_message] if last_message else [],
    }
    wrapped_request = _ModifiedJsonRequest(request, modified_body)

    # Save the conversation to the DB after the agent runs
    on_complete = functools.partial(
        persist_agent_run_result,
        conversation_id=conversation_id,
        agent_name=hitl_agent3.name,
        user_id=user_id,
    )

    # Backend DB is source of truth for history
    # Wrapped request only contains the new message from the frontend
    response = await VercelAIAdapter.dispatch_request(
        wrapped_request,
        agent=hitl_agent3,
        message_history=backend_message_history if backend_message_history else None,
        deps=HITLAgentContext(user_id=user_id, conversation_id=conversation_id),
        on_complete=on_complete,
    )

    response.headers["X-Accel-Buffering"] = "no"
    response.headers["Cache-Control"] = "no-cache, no-transform"
    response.headers["X-Content-Type-Options"] = "nosniff"
    # Include conversation ID in response headers for frontend to use
    response.headers["X-Conversation-Id"] = conversation_id

    return response


# =============================================================================
# Conversation/Approval API (for both chat and workflow UIs)
# =============================================================================

class StartConversationRequest(BaseModel):
    prompt: str


class ApprovalRequest(BaseModel):
    tool_call_id: str
    approved: bool
    reason: str | None = None
    override_args: dict | None = None  # e.g., {"body": "modified message"}


@router.post("/conversation/start")
async def start_conversation(body: StartConversationRequest, user: AuthUser):
    """
    Start a new conversation with the agent.

    If the agent needs approval (e.g., for send_sms), the response includes
    pending approval details. The conversation is saved to DB.
    """
    conversation_id = str(uuid.uuid4())
    user_id = user.id

    result = await run_agent_with_persistence(
        prompt=body.prompt,
        conversation_id=conversation_id,
        user_id=user_id,
    )

    pending = extract_pending_approval(result)

    return {
        "conversation_id": conversation_id,
        "output": result.output if isinstance(result.output, str) else None,
        "pending": pending,
        "status": "pending_approval" if pending else "completed",
    }


@router.get("/conversation/{conversation_id}")
async def get_conversation(conversation_id: str, user: AuthUser):
    """
    Get conversation details including pending approval info.
    Only accessible by the conversation owner.
    """
    user_id = user.id

    # Verify ownership using Clerk user ID
    try:
        await verify_conversation_ownership(conversation_id, user_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Conversation not found")

    conv = await get_conversation_with_pending(conversation_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")

    return {
        "conversation_id": conversation_id,
        "pending": conv["pending"],
        "agent_name": conv["agent_name"],
        "user_id": conv["user_id"],
        "created_at": conv["created_at"].isoformat() if conv["created_at"] else None,
        "updated_at": conv["updated_at"].isoformat() if conv["updated_at"] else None,
        "status": "pending_approval" if conv["pending"] else "completed",
    }


@router.post("/conversation/{conversation_id}/approve")
async def approve_conversation(conversation_id: str, body: ApprovalRequest, user: AuthUser):
    """
    Approve or deny a pending tool call.
    Only accessible by the conversation owner.

    This resumes the agent with the approval decision and returns the final result.
    """
    logfire.info(f"Approve request for conversation_id: {conversation_id}")
    user_id = user.id

    # Verify ownership before allowing approval using Clerk user ID
    try:
        await verify_conversation_ownership(conversation_id, user_id)
    except ValueError as e:
        logfire.error(f"Approval failed - error verifying conversation ownership: {conversation_id}, user_id: {user_id}, error: {str(e)}")
        raise HTTPException(status_code=404, detail=f"Error verifying conversation ownership: {str(e)}")

    try:
        result = await resume_with_approval(
            conversation_id=conversation_id,
            tool_call_id=body.tool_call_id,
            approved=body.approved,
            override_args=body.override_args,
            denial_reason=body.reason,
            user_id=user_id,
        )

        # Check if there's another pending approval (unlikely but possible)
        pending = extract_pending_approval(result)

        return {
            "conversation_id": conversation_id,
            "output": result.output if isinstance(result.output, str) else None,
            "pending": pending,
            "status": "pending_approval" if pending else "completed",
            "approved": body.approved,
        }
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        # TODO: handle case with multiple pending approvals
        logfire.error(f"Error approving conversation {conversation_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/conversations/pending")
async def list_pending():
    """
    List all conversations with pending approvals.
    """
    pending = await list_pending_conversations()
    return {
        "conversations": pending,
        "count": len(pending),
    }


@router.get("/conversations/history")
async def get_conversation_history(user: AuthUser, limit: int = 20):
    """
    List conversations for the authenticated user.
    Returns recent conversations sorted by updated_at desc.
    """
    user_id = user.id
    conversations = await list_user_conversations(
        user_id=user_id,
        agent_name=hitl_agent3.name,
        limit=limit,
    )
    return {
        "conversations": conversations,
        "count": len(conversations),
        "user_id": user_id,
    }


@router.get("/conversation/{conversation_id}/messages")
async def get_conversation_messages_endpoint(
    conversation_id: str,
    user: AuthUser,
    session: DBSession,
):
    """
    Get conversation messages in Vercel AI SDK format.
    Only accessible by the conversation owner.

    This endpoint allows the frontend to load an existing conversation
    and resume it. Messages are returned in Vercel AI UIMessage format.
    """
    user_id = user.id

    # Verify ownership using Clerk user ID
    try:
        await verify_conversation_ownership(conversation_id, user_id, session)
    except ValueError:
        raise HTTPException(status_code=404, detail="Conversation not found")

    # Load messages from DB in PydanticAI format
    pydantic_messages = await get_conversation_messages(conversation_id, session=session)

    if not pydantic_messages:
        return {"messages": [], "conversation_id": conversation_id}

    # Convert to Vercel AI format using the adapter
    vercel_messages = VercelAIAdapter.dump_messages(pydantic_messages)

    # Also check for pending approval
    pending = extract_pending_approval_from_messages(pydantic_messages)

    return {
        "messages": [msg.model_dump() for msg in vercel_messages],
        "conversation_id": conversation_id,
        "pending": pending,
    }


@router.delete("/conversation/{conversation_id}")
async def delete_conversation_endpoint(conversation_id: str, user: AuthUser):
    """
    Delete a conversation.
    Only accessible by the conversation owner.
    """
    user_id = user.id

    try:
        await delete_conversation(conversation_id, user_id)
        return {"success": True, "conversation_id": conversation_id}
    except ValueError:
        raise HTTPException(status_code=404, detail="Conversation not found")
