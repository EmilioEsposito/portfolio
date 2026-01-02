"""
HITL Agent Routes

Two interaction modes, both using the same simple dual-run pattern:
1. Streaming Chat (/chat) - Real-time interaction via Vercel AI SDK
2. Workflow API (/workflow/*) - Start conversations and manage approvals programmatically

Endpoint naming convention:
- /chat - Chat-specific (streaming)
- /workflow/* - Workflow-specific (non-streaming, programmatic)
- /conversation/* - Shared endpoints used by both modes

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

from api.src.ai.hitl_agents.hitl_sms_agent import (
    hitl_sms_agent,
    HITLAgentContext,
    resume_with_approvals,
    extract_pending_approvals,
    ApprovalDecision,
)
from api.src.ai.models import (
    persist_agent_run_result,
    list_user_conversations,
    get_conversation_messages,
    delete_conversation,
    list_pending_conversations,
    get_conversation_with_pending,
    extract_pending_approval_from_messages,
)
from api.src.utils.swagger_schema import expand_json_schema
from api.src.utils.clerk import SerniaUser, verify_serniacapital_user
from api.src.database.database import DBSession

router = APIRouter(
    prefix="/ai/hitl-agent",
    tags=["hitl-agent"],
    dependencies=[Depends(verify_serniacapital_user)],
)


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
async def chat(request: Request, user: SerniaUser, session: DBSession) -> Response:
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

    clerk_user_id = user.id

    request_json = await request.json()
    conversation_id = request_json.get("id")
    frontend_messages = request_json.get("messages", [])

    # Load message history from DB (authoritative source)
    # clerk_user_id filter is applied in SQL - returns empty if not found or not owned
    backend_message_history = await get_conversation_messages(
        conversation_id, clerk_user_id, session=session
    )

    logfire.info(f"Chat conversation_id: {conversation_id}, clerk_user_id: {clerk_user_id}, frontend_msg_count: {len(frontend_messages)}, db_msg_count: {len(backend_message_history)}")

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
        agent_name=hitl_sms_agent.name,
        clerk_user_id=clerk_user_id,
        session=session,
    )

    # Backend DB is source of truth for history
    # Wrapped request only contains the new message from the frontend
    response = await VercelAIAdapter.dispatch_request(
        wrapped_request,
        agent=hitl_sms_agent,
        message_history=backend_message_history if backend_message_history else None,
        deps=HITLAgentContext(clerk_user_id=clerk_user_id, conversation_id=conversation_id),
        on_complete=on_complete,
    )

    # Think below are not needed. Keeping commented out for now in case we see performance issues.
    # # Add headers to prevent browser/proxy buffering
    # # X-Accel-Buffering: no tells nginx and browsers not to buffer the response
    # response.headers["X-Accel-Buffering"] = "no"
    # response.headers["Cache-Control"] = "no-cache, no-transform"
    # response.headers["X-Content-Type-Options"] = "nosniff"
    
    # Include conversation ID in response headers for frontend to use
    response.headers["X-Conversation-Id"] = conversation_id

    return response


# =============================================================================
# Conversation/Approval API (for both chat and workflow UIs)
# =============================================================================

class StartConversationRequest(BaseModel):
    prompt: str


class ApprovalDecisionRequest(BaseModel):
    """A single approval decision for one tool call."""
    tool_call_id: str
    approved: bool
    reason: str | None = None
    override_args: dict | None = None  # e.g., {"body": "modified message"}


class ApprovalRequest(BaseModel):
    """Batch approval request for one or more tool calls."""
    decisions: list[ApprovalDecisionRequest]


@router.post("/workflow/start")
async def start_workflow(body: StartConversationRequest, user: SerniaUser, session: DBSession):
    """
    Start a new workflow conversation with the agent (non-streaming).

    If the agent needs approval (e.g., for send_sms), the response includes
    pending approval details as a list. The conversation is saved to DB.
    """
    conversation_id = str(uuid.uuid4())
    clerk_user_id = user.id

    # Agent has persistence patch applied - automatically saves to DB after run
    result = await hitl_sms_agent.run(
        user_prompt=body.prompt,
        deps=HITLAgentContext(clerk_user_id=clerk_user_id, conversation_id=conversation_id, db_session=session),
    )

    pending = extract_pending_approvals(result)

    return {
        "conversation_id": conversation_id,
        "output": result.output if isinstance(result.output, str) else None,
        "pending": pending,  # Always a list (empty if no approvals needed)
        "status": "pending_approval" if pending else "completed",
    }


@router.get("/conversation/{conversation_id}")
async def get_conversation(conversation_id: str, user: SerniaUser, session: DBSession):
    """
    Get conversation details including pending approval info.
    Only accessible by the conversation owner.
    """
    # clerk_user_id filter is applied in SQL - returns None if not found or not owned
    conv = await get_conversation_with_pending(conversation_id, user.id, session=session)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")

    return {
        "conversation_id": conversation_id,
        "pending": conv["pending"],
        "agent_name": conv["agent_name"],
        "clerk_user_id": conv["clerk_user_id"],
        "created_at": conv["created_at"].isoformat() if conv["created_at"] else None,
        "updated_at": conv["updated_at"].isoformat() if conv["updated_at"] else None,
        "status": "pending_approval" if conv["pending"] else "completed",
    }


@router.post("/conversation/{conversation_id}/approve")
async def approve_conversation(conversation_id: str, body: ApprovalRequest, user: SerniaUser, session: DBSession):
    """
    Approve or deny pending tool calls (batch).
    Only accessible by the conversation owner.

    This resumes the agent with approval decisions and returns the result.
    If the agent requests more approvals, they'll be in the pending list.
    """
    logfire.info(f"Approve request for conversation_id: {conversation_id}, decisions: {len(body.decisions)}")
    clerk_user_id = user.id

    try:
        # Convert request models to domain objects
        decisions = [
            ApprovalDecision(
                tool_call_id=d.tool_call_id,
                approved=d.approved,
                override_args=d.override_args,
                denial_reason=d.reason,
            )
            for d in body.decisions
        ]

        result = await resume_with_approvals(
            conversation_id=conversation_id,
            decisions=decisions,
            clerk_user_id=clerk_user_id,
            session=session,
        )

        # Check if there are more pending approvals
        pending = extract_pending_approvals(result)

        # Return the decisions with their approval status for UI display
        processed_decisions = [
            {"tool_call_id": d.tool_call_id, "approved": d.approved}
            for d in body.decisions
        ]

        return {
            "conversation_id": conversation_id,
            "output": result.output if isinstance(result.output, str) else None,
            "pending": pending,  # Always a list
            "status": "pending_approval" if pending else "completed",
            "decisions": processed_decisions,  # What was approved/denied
        }
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logfire.error(f"Error approving conversation {conversation_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/workflow/pending")
async def list_pending_workflows(user: SerniaUser, session: DBSession):
    """
    List workflow conversations with pending approvals for the authenticated user.
    """
    pending = await list_pending_conversations(
        agent_name=hitl_sms_agent.name,
        clerk_user_id=user.id,
        session=session,
    )
    return {
        "conversations": pending,
        "count": len(pending),
    }


@router.get("/conversations/history")
async def get_conversation_history(user: SerniaUser, limit: int = 20):
    """
    List conversations for the authenticated user.
    Returns recent conversations sorted by updated_at desc.
    """
    clerk_user_id = user.id
    conversations = await list_user_conversations(
        clerk_user_id=clerk_user_id,
        agent_name=hitl_sms_agent.name,
        limit=limit,
    )
    return {
        "conversations": conversations,
        "count": len(conversations),
        "clerk_user_id": clerk_user_id,
    }


@router.get("/conversation/{conversation_id}/messages")
async def get_conversation_messages_endpoint(
    conversation_id: str,
    user: SerniaUser,
    session: DBSession,
):
    """
    Get conversation messages in Vercel AI SDK format.
    Only accessible by the conversation owner.

    This endpoint allows the frontend to load an existing conversation
    and resume it. Messages are returned in Vercel AI UIMessage format.
    """
    # Load messages from DB - user_id filter applied in SQL
    pydantic_messages = await get_conversation_messages(
        conversation_id, user.id, session=session
    )

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
async def delete_conversation_endpoint(conversation_id: str, user: SerniaUser):
    """
    Delete a conversation.
    Only accessible by the conversation owner.
    """
    clerk_user_id = user.id

    try:
        await delete_conversation(conversation_id, clerk_user_id)
        return {"success": True, "conversation_id": conversation_id}
    except ValueError:
        raise HTTPException(status_code=404, detail="Conversation not found")
