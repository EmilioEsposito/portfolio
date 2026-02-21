"""
Shared HITL (Human-in-the-Loop) utilities for all agents.

Agent-agnostic helpers for extracting pending approvals and resuming
agents with approval decisions. Used by hitl_sms_agent and sernia_agent.
"""
import json
from dataclasses import dataclass

from pydantic_ai import (
    Agent,
    AgentRunResult,
    DeferredToolRequests,
    DeferredToolResults,
    ToolApproved,
    ToolDenied,
)
from pydantic_ai.messages import ModelMessage
from sqlalchemy.ext.asyncio import AsyncSession
import logfire

from api.src.ai_demos.models import get_conversation_messages
from api.src.database.database import provide_session


def extract_pending_approvals(result: AgentRunResult) -> list[dict]:
    """
    Extract all pending approval info from an agent result.

    Returns empty list if no approvals are pending, otherwise returns list of:
    {
        "tool_call_id": str,
        "tool_name": str,
        "args": dict,
    }
    """
    if not isinstance(result.output, DeferredToolRequests):
        return []

    if not result.output.approvals:
        return []

    return [
        {
            "tool_call_id": approval.tool_call_id,
            "tool_name": approval.tool_name,
            "args": json.loads(approval.args) if isinstance(approval.args, str) else approval.args,
        }
        for approval in result.output.approvals
    ]


def extract_pending_approval(result: AgentRunResult) -> dict | None:
    """
    Extract first pending approval from an agent result.
    Thin wrapper for backward compatibility.
    """
    approvals = extract_pending_approvals(result)
    return approvals[0] if approvals else None


@dataclass
class ApprovalDecision:
    """A single approval/denial decision for a tool call."""
    tool_call_id: str
    approved: bool
    override_args: dict | None = None
    denial_reason: str | None = None


async def resume_with_approvals(
    agent: Agent,
    conversation_id: str,
    decisions: list[ApprovalDecision],
    deps: object,
    clerk_user_id: str = "anonymous",
    session: AsyncSession | None = None,
) -> AgentRunResult:
    """
    Resume a paused agent with approval decisions. Agent-agnostic.

    Args:
        agent: The PydanticAI agent to resume
        conversation_id: ID of the conversation to resume
        decisions: List of approval decisions for pending tool calls
        deps: Agent-specific deps object
        clerk_user_id: Clerk user ID for DB ownership filter
        session: Optional existing DB session
    """
    async with provide_session(session) as s:
        messages = await get_conversation_messages(conversation_id, clerk_user_id, session=s)

    if not messages:
        raise ValueError(f"No conversation found with ID: {conversation_id}")

    approvals_dict = {}
    for decision in decisions:
        if decision.approved:
            approvals_dict[decision.tool_call_id] = ToolApproved(override_args=decision.override_args)
        else:
            approvals_dict[decision.tool_call_id] = ToolDenied(decision.denial_reason or "Denied by user")

    deferred_results = DeferredToolResults(approvals=approvals_dict)

    result = await agent.run(
        message_history=messages,
        deferred_tool_results=deferred_results,
        deps=deps,
    )

    return result
