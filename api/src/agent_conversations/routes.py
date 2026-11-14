"""Shared team conversation and approval API.

Both URL mounts use this router and its verified-Sernia gate. Handler selection
uses the persisted conversation metadata, never the request's claimed agent.
Reading or listing conversations does not construct the general agent.
"""

import logfire
from fastapi import APIRouter, Depends, HTTPException
from pydantic_ai.ui.vercel_ai import VercelAIAdapter

from api.src.agent_conversations.auth import (
    SerniaUser,
    _display_name,
    _get_sernia_user,
    _sernia_gate,
)
from api.src.agent_conversations.errors import LLMAPIError, _llm_error_response
from api.src.agent_conversations.history import _merge_sms_if_needed
from api.src.agent_conversations.hitl import ApprovalDecision
from api.src.agent_conversations.identity import AGENT_NAME
from api.src.agent_conversations.models import (
    extract_pending_approval_from_messages,
    get_agent_conversation,
    get_conversation_messages,
    get_conversation_with_pending,
    list_pending_conversations,
    list_user_conversations,
)
from api.src.agent_conversations.run_state import lock_conversation, public_run, require_idle
from api.src.agent_conversations.schemas import ApprovalRequest
from api.src.database.database import DBSession
from api.src.zillow_leads.identity import is_lead_conversation

router = APIRouter(tags=["sernia-ai"], dependencies=[Depends(_sernia_gate)])


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
    # Log each decision verbatim (using repr on override_args values so any
    # hidden \r\n / control chars in edited bodies are visible in logfire).
    decisions_log = [
        {
            "tool_call_id": d.tool_call_id,
            "approved": d.approved,
            "override_args_repr": {k: repr(v) for k, v in (d.override_args or {}).items()} or None,
        }
        for d in body.decisions
    ]
    logfire.info(
        "sernia approve",
        conversation_id=conversation_id,
        decisions=decisions_log,
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

        conv = await get_agent_conversation(session, conversation_id, clerk_user_id=None)
        if conv:
            require_idle(conv.metadata_)
        if conv and is_lead_conversation(conv.metadata_):
            # A Zillow lead conversation is decided in code: an approved reply
            # is sent as approved, to the address stored with it. No agent is
            # resumed, so nothing a lead wrote is read by a model with tools.
            from api.src.zillow_leads.handle import apply_approval as apply_lead_approval

            return await apply_lead_approval(
                session,
                conversation_id=conversation_id,
                metadata=conv.metadata_ or {},
                decisions=decisions,
                approver=user_name,
            )
        from api.src.sernia_ai.conversation_handler import approve_general_conversation

        return await approve_general_conversation(
            conversation_id,
            body,
            decisions=decisions,
            conv=conv,
            user=user,
            session=session,
        )
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except LLMAPIError as e:
        # LLM provider API errors (overloaded, rate limited, etc.) — log as warning,
        # surface with appropriate status code, don't leak raw error body
        status_code, user_message = _llm_error_response(e, "approve")
        logfire.warn(
            "sernia approve LLM API error",
            conversation_id=conversation_id,
            clerk_user_id=clerk_user_id,
            error_type=type(e).__name__,
            provider_status=getattr(e, "status_code", None),
        )
        raise HTTPException(status_code=status_code, detail=user_message)
    except Exception as e:
        logfire.exception(
            "sernia approve error",
            conversation_id=conversation_id,
            clerk_user_id=clerk_user_id,
            error_type=type(e).__name__,
        )
        raise HTTPException(status_code=500, detail="An internal error occurred. Please try again.")


@router.get("/conversation/{conversation_id}")
async def get_conversation(
    conversation_id: str,
    user: SerniaUser = Depends(_get_sernia_user),
    session: DBSession = None,
):
    """Get conversation details including pending approval info."""
    conv = await get_conversation_with_pending(conversation_id, clerk_user_id=None, session=session)
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
    """Get saved progress and authoritative run status in Vercel AI SDK format."""
    conversation = await get_agent_conversation(session, conversation_id, retries=1)
    run = public_run(conversation.metadata_ if conversation else None)
    pydantic_messages = await get_conversation_messages(
        conversation_id,
        clerk_user_id=None,
        session=session,
        retries=1,
        include_terminal=bool(run and run["status"] in {"failed", "cancelled", "interrupted"}),
    )

    # For SMS conversations, merge live Quo messages (source of truth)
    pydantic_messages = await _merge_sms_if_needed(conversation_id, pydantic_messages)

    if not pydantic_messages:
        return {"messages": [], "conversation_id": conversation_id, "run": run}

    vercel_messages = VercelAIAdapter.dump_messages(pydantic_messages)
    pending = extract_pending_approval_from_messages(pydantic_messages)
    if run and run["status"] != "completed":
        pending = []

    return {
        "messages": [msg.model_dump(by_alias=True) for msg in vercel_messages],
        "conversation_id": conversation_id,
        "pending": pending,
        "run": run,
    }


@router.get("/workflow/pending")
async def list_pending_workflows(
    user: SerniaUser = Depends(_get_sernia_user),
    session: DBSession = None,
):
    """List Sernia conversations with pending approvals (team-wide)."""
    pending = await list_pending_conversations(
        agent_name=AGENT_NAME,
        clerk_user_id=None,
        session=session,
    )
    return {"conversations": pending, "count": len(pending)}


@router.get("/conversations/history")
async def get_conversation_history(
    user: SerniaUser = Depends(_get_sernia_user),
    limit: int = 20,
    offset: int = 0,
    modality: str | None = None,
):
    """List recent conversations for all Sernia users (shared team context)."""
    conversations = await list_user_conversations(
        clerk_user_id=None,
        agent_name=AGENT_NAME,
        limit=limit,
        offset=offset,
        modality=modality,
    )
    for conversation in conversations:
        conversation["run"] = public_run({"web_chat_run": conversation.get("run")})
    return {
        "conversations": conversations,
        "count": len(conversations),
    }


@router.delete("/conversation/{conversation_id}")
async def delete_conversation_endpoint(
    conversation_id: str,
    user: SerniaUser = Depends(_get_sernia_user),
    session: DBSession = None,
):
    """Delete a conversation (any Sernia user can delete any Sernia conversation)."""
    await lock_conversation(session, conversation_id)
    conversation = await get_agent_conversation(session, conversation_id, retries=1)
    if not conversation or conversation.agent_name != AGENT_NAME:
        raise HTTPException(status_code=404, detail="Conversation not found")
    require_idle(conversation.metadata_)
    await session.delete(conversation)
    await session.commit()
    return {"success": True, "conversation_id": conversation_id}
