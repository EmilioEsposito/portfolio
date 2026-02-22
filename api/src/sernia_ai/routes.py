"""
Routes for the Sernia AI agent.

All routes are gated to @serniacapital.com users via a router-level dependency.
The verified Clerk User object is stashed on request.state.sernia_user by the
gate and retrieved by individual handlers via the SerniaUser dependency.
"""
import asyncio
import json
import functools
from typing import Literal

import logfire
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from starlette.responses import Response
from pydantic_ai.ui.vercel_ai import VercelAIAdapter
from clerk_backend_api import User

from api.src.sernia_ai.agent import sernia_agent
from api.src.sernia_ai.config import AGENT_NAME, WORKSPACE_PATH
from api.src.sernia_ai.deps import SerniaDeps
from api.src.ai_demos.hitl_utils import (
    extract_pending_approvals,
    ApprovalDecision,
    resume_with_approvals,
)
from api.src.ai_demos.models import (
    persist_agent_run_result,
    list_user_conversations,
    get_conversation_messages,
    delete_conversation,
    list_pending_conversations,
    get_conversation_with_pending,
    extract_pending_approval_from_messages,
)
from api.src.sernia_ai.memory.git_sync import commit_and_push
from api.src.utils.clerk import verify_serniacapital_user
from api.src.database.database import DBSession


# =============================================================================
# Router-level auth gate
# =============================================================================

async def _sernia_gate(request: Request) -> None:
    """Router-level dependency: verify @serniacapital.com and stash user."""
    user = await verify_serniacapital_user(request)
    request.state.sernia_user = user


async def _get_sernia_user(request: Request) -> User:
    """Per-endpoint dependency: retrieve user set by the router gate."""
    return request.state.sernia_user


SerniaUser = User  # plain type alias — resolved by _get_sernia_user dependency

router = APIRouter(
    prefix="/sernia-ai",
    tags=["sernia-ai"],
    dependencies=[Depends(_sernia_gate)],
)


# Helper to resolve display name from Clerk User
def _display_name(user: User) -> str:
    return f"{user.first_name or ''} {user.last_name or ''}".strip() or "User"


# =============================================================================
# Streaming Chat
# =============================================================================

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


@router.post(
    "/chat",
    response_class=Response,
    summary="Chat with Sernia AI assistant",
)
async def chat_sernia(
    request: Request,
    user: SerniaUser = Depends(_get_sernia_user),
    session: DBSession = None,
) -> Response:
    """
    Streaming chat endpoint for the Sernia AI agent.

    Uses PydanticAI's VercelAIAdapter for Vercel AI SDK Data Stream Protocol (SSE).
    Backend DB is the authoritative source for message history.
    Only the latest frontend message is forwarded; history is loaded from DB.
    """
    clerk_user_id = user.id
    user_name = _display_name(user)

    request_json = await request.json()
    conversation_id = request_json.get("id")
    frontend_messages = request_json.get("messages", [])

    # Load message history from DB (authoritative source)
    backend_message_history = await get_conversation_messages(
        conversation_id, clerk_user_id, session=session
    )

    logfire.info(
        "sernia chat",
        conversation_id=conversation_id,
        clerk_user_id=clerk_user_id,
        frontend_msg_count=len(frontend_messages),
        db_msg_count=len(backend_message_history),
    )

    # Only use the LAST message from frontend (the new user input)
    last_message = frontend_messages[-1] if frontend_messages else None
    modified_body = {
        **request_json,
        "messages": [last_message] if last_message else [],
    }
    wrapped_request = _ModifiedJsonRequest(request, modified_body)

    deps = SerniaDeps(
        db_session=session,
        conversation_id=conversation_id or "",
        user_identifier=clerk_user_id,
        user_name=user_name,
        modality="web_chat",
        workspace_path=WORKSPACE_PATH,
    )

    async def _on_complete(result):
        await persist_agent_run_result(
            result,
            conversation_id=conversation_id,
            agent_name=AGENT_NAME,
            clerk_user_id=clerk_user_id,
            session=session,
        )
        asyncio.create_task(commit_and_push(WORKSPACE_PATH))

    on_complete = _on_complete

    response = await VercelAIAdapter.dispatch_request(
        wrapped_request,
        agent=sernia_agent,
        message_history=backend_message_history if backend_message_history else None,
        deps=deps,
        on_complete=on_complete,
    )

    response.headers["X-Accel-Buffering"] = "no"
    response.headers["Cache-Control"] = "no-cache, no-transform"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Conversation-Id"] = conversation_id or ""

    return response


# =============================================================================
# Conversation / Approval API
# =============================================================================

class ApprovalDecisionRequest(BaseModel):
    """A single approval decision for one tool call."""
    tool_call_id: str
    approved: bool
    reason: str | None = None
    override_args: dict | None = None


class ApprovalRequest(BaseModel):
    """Batch approval request for one or more tool calls."""
    decisions: list[ApprovalDecisionRequest]


@router.post("/conversation/{conversation_id}/approve")
async def approve_conversation(
    conversation_id: str,
    body: ApprovalRequest,
    user: SerniaUser = Depends(_get_sernia_user),
    session: DBSession = None,
):
    """
    Approve or deny pending tool calls for a Sernia agent conversation.
    Resumes the agent with decisions and returns the result.
    """
    logfire.info(
        "sernia approve",
        conversation_id=conversation_id,
        decisions=len(body.decisions),
    )
    clerk_user_id = user.id
    user_name = _display_name(user)

    try:
        decisions = [
            ApprovalDecision(
                tool_call_id=d.tool_call_id,
                approved=d.approved,
                override_args=d.override_args,
                denial_reason=d.reason,
            )
            for d in body.decisions
        ]

        deps = SerniaDeps(
            db_session=session,
            conversation_id=conversation_id,
            user_identifier=clerk_user_id,
            user_name=user_name,
            modality="web_chat",
            workspace_path=WORKSPACE_PATH,
        )

        result = await resume_with_approvals(
            agent=sernia_agent,
            conversation_id=conversation_id,
            decisions=decisions,
            deps=deps,
            clerk_user_id=clerk_user_id,
            session=session,
        )
        asyncio.create_task(commit_and_push(WORKSPACE_PATH))

        pending = extract_pending_approvals(result)

        return {
            "conversation_id": conversation_id,
            "output": result.output if isinstance(result.output, str) else None,
            "pending": pending,
            "status": "pending_approval" if pending else "completed",
            "decisions": [
                {"tool_call_id": d.tool_call_id, "approved": d.approved}
                for d in body.decisions
            ],
        }
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logfire.error(f"Error approving sernia conversation {conversation_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/conversation/{conversation_id}")
async def get_conversation(
    conversation_id: str,
    user: SerniaUser = Depends(_get_sernia_user),
    session: DBSession = None,
):
    """Get conversation details including pending approval info."""
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


@router.get("/conversation/{conversation_id}/messages")
async def get_conversation_messages_endpoint(
    conversation_id: str,
    user: SerniaUser = Depends(_get_sernia_user),
    session: DBSession = None,
):
    """Get conversation messages in Vercel AI SDK format."""
    pydantic_messages = await get_conversation_messages(
        conversation_id, user.id, session=session
    )

    if not pydantic_messages:
        return {"messages": [], "conversation_id": conversation_id}

    vercel_messages = VercelAIAdapter.dump_messages(pydantic_messages)
    pending = extract_pending_approval_from_messages(pydantic_messages)

    return {
        "messages": [msg.model_dump() for msg in vercel_messages],
        "conversation_id": conversation_id,
        "pending": pending,
    }


@router.get("/workflow/pending")
async def list_pending_workflows(
    user: SerniaUser = Depends(_get_sernia_user),
    session: DBSession = None,
):
    """List Sernia conversations with pending approvals."""
    pending = await list_pending_conversations(
        agent_name=AGENT_NAME,
        clerk_user_id=user.id,
        session=session,
    )
    return {"conversations": pending, "count": len(pending)}


@router.get("/conversations/history")
async def get_conversation_history(
    user: SerniaUser = Depends(_get_sernia_user),
    limit: int = 20,
):
    """List recent conversations for the authenticated user."""
    conversations = await list_user_conversations(
        clerk_user_id=user.id,
        agent_name=AGENT_NAME,
        limit=limit,
    )
    return {
        "conversations": conversations,
        "count": len(conversations),
    }


@router.delete("/conversation/{conversation_id}")
async def delete_conversation_endpoint(
    conversation_id: str,
    user: SerniaUser = Depends(_get_sernia_user),
):
    """Delete a conversation."""
    try:
        await delete_conversation(conversation_id, user.id)
        return {"success": True, "conversation_id": conversation_id}
    except ValueError:
        raise HTTPException(status_code=404, detail="Conversation not found")


# =============================================================================
# Admin
# =============================================================================

@router.get("/admin/system-instructions")
async def get_system_instructions(
    user: SerniaUser = Depends(_get_sernia_user),
    modality: Literal["sms", "email", "web_chat"] = "web_chat",
    user_name: str | None = None,
):
    """
    Return the fully resolved system instructions as they would appear
    at the start of an agent run.

    Query params allow mocking the context deps from the frontend:
    - modality: sms | email | web_chat (default: web_chat)
    - user_name: override display name (default: from Clerk user)
    """
    from types import SimpleNamespace

    from api.src.sernia_ai.instructions import STATIC_INSTRUCTIONS, DYNAMIC_INSTRUCTIONS

    resolved_name = user_name or _display_name(user)

    # Build a fake RunContext — the instruction functions only access ctx.deps.*
    deps = SerniaDeps(
        db_session=None,  # type: ignore[arg-type]
        conversation_id="",
        user_identifier=user.id,
        user_name=resolved_name,
        modality=modality,
        workspace_path=WORKSPACE_PATH,
    )
    fake_ctx = SimpleNamespace(deps=deps)

    sections = [{"label": "Static Instructions", "content": STATIC_INSTRUCTIONS}]
    for fn in DYNAMIC_INSTRUCTIONS:
        content = fn(fake_ctx)  # type: ignore[arg-type]
        sections.append({"label": fn.__name__, "content": content or "(empty)"})

    combined = "\n\n".join(s["content"] for s in sections)

    return {
        "sections": sections,
        "combined": combined,
        "model": sernia_agent.model.model_name if hasattr(sernia_agent.model, "model_name") else str(sernia_agent.model),
        "deps": {
            "user_name": resolved_name,
            "modality": modality,
        },
    }
