"""Resume general-agent approvals and preserve its delivery/failure behavior.

Imported only after the shared router selects a general-agent conversation from
stored metadata. Zillow approvals never enter this module.
"""

from clerk_backend_api import User
from fastapi import HTTPException
from pydantic_ai import capture_run_messages
from sqlalchemy.ext.asyncio import AsyncSession

from api.src.agent_conversations.auth import _display_name, _sernia_email
from api.src.agent_conversations.errors import LLMAPIError
from api.src.agent_conversations.hitl import (
    ApprovalDecision,
    extract_tool_results,
    resume_with_approvals,
)
from api.src.agent_conversations.models import AgentConversation
from api.src.agent_conversations.schemas import ApprovalRequest
from api.src.sernia_ai.config import WORKSPACE_PATH
from api.src.sernia_ai.deps import SerniaDeps
from api.src.sernia_ai.general_agent import sernia_agent
from api.src.sernia_ai.model_config import resolve_active_run_kwargs
from api.src.sernia_ai.runs import finalize_run, save_failed_run


async def approve_general_conversation(
    conversation_id: str,
    body: ApprovalRequest,
    *,
    decisions: list[ApprovalDecision],
    conv: AgentConversation | None,
    user: User,
    session: AsyncSession,
):
    clerk_user_id = user.id
    user_name = _display_name(user)
    captured_messages = None
    try:
        reply_to = None
        if conv and conv.modality == "sms" and conv.metadata_:
            reply_to = conv.metadata_.get("trigger_group_participants") or conv.metadata_.get(
                "trigger_phone"
            )

        deps = SerniaDeps(
            db_session=session,
            conversation_id=conversation_id,
            user_identifier=clerk_user_id,
            user_name=user_name,
            user_email=_sernia_email(user),
            modality="sms" if reply_to else "web_chat",
            workspace_path=WORKSPACE_PATH,
            sms_reply_recipients=([reply_to] if isinstance(reply_to, str) else reply_to or []),
        )

        run_kwargs = await resolve_active_run_kwargs()

        with capture_run_messages() as captured_messages:
            result = await resume_with_approvals(
                agent=sernia_agent,
                conversation_id=conversation_id,
                decisions=decisions,
                deps=deps,
                clerk_user_id=None,  # Shared team access
                session=session,
                metadata={"trigger_source": "api/sernia-ai/approve"},
                user_message=body.user_message,
                **run_kwargs,
            )

        # Save the approval result (tool outputs + agent follow-up) and deliver
        # it through the shared pipeline. For a conversation that lives in an
        # SMS thread, `deps.sms_reply_recipients` (set above from the
        # conversation's trigger metadata) sends the agent's answer back into
        # that thread — the group, not just the original sender.
        outcome = await finalize_run(
            result,
            deps=deps,
            clerk_user_id=clerk_user_id,
            notify_user_id=clerk_user_id,
        )
        pending = outcome.pending
        tool_results = extract_tool_results(result)

        return {
            "conversation_id": conversation_id,
            "output": result.output if isinstance(result.output, str) else None,
            "pending": pending,
            "tool_results": tool_results,
            "status": "pending_approval" if pending else "completed",
            "decisions": [
                {"tool_call_id": d.tool_call_id, "approved": d.approved} for d in body.decisions
            ],
        }
    except (HTTPException, ValueError):
        raise
    except Exception as error:
        await save_failed_run(
            captured_messages,
            conversation_id=conversation_id,
            clerk_user_id=clerk_user_id,
            llm_error=isinstance(error, LLMAPIError),
        )
        raise
