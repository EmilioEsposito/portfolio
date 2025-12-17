"""
Minimal end-to-end HITL with:
- PydanticAI deferred tool (ApprovalRequired) to force an agent pause
- DBOSAgent for durable agent execution
- A DBOS workflow that durably waits for approval and resumes the SAME agent run

Docs:
- Deferred tools (ApprovalRequired / resume with DeferredToolResults):
  https://ai.pydantic.dev/deferred-tools/
- DBOS durable execution integration (DBOSAgent):
  https://ai.pydantic.dev/durable_execution/dbos/
- DBOS workflows & events:
  https://docs.dbos.dev/python/workflows/
  https://docs.dbos.dev/python/events/
"""

from __future__ import annotations

from typing import Any, Dict, Union

from dbos import DBOS
from pydantic_ai import Agent
from pydantic_ai.durable_exec.dbos import DBOSAgent
from pydantic_ai.tools import tool, ApprovalRequired, DeferredToolRequests, DeferredToolResults


# ----------------------------
# 1) Agent + approval-gated tool
# ----------------------------

agent = Agent("gpt-5", instructions="Call tools when needed. Some actions require approval before continuing.")

@tool
def do_thing_needing_approval(task: str) -> str:
    """
    Generic action that ALWAYS requires human approval.

    Key behavior:
    - Raising ApprovalRequired causes the agent to STOP and return DeferredToolRequests
      instead of executing the tool immediately.

    Deferred tools docs:
    https://ai.pydantic.dev/deferred-tools/#approval-required
    """
    raise ApprovalRequired(reason=f"Human approval required to proceed with: {task}")


# Wrap the agent so its run-loop + model calls are durable under DBOS.
# DBOSAgent docs:
# https://ai.pydantic.dev/durable_execution/dbos/#dbosagent
dbos_agent = DBOSAgent(agent)


# ----------------------------
# 2) DBOS workflow that orchestrates HITL
# ----------------------------

@DBOS.workflow()
def agent_with_hitl(prompt: str) -> str:
    """
    Workflow responsibilities:
    - Run the agent (durably, via DBOSAgent)
    - If deferred tool requests appear, publish them for a UI/operator
    - Durably WAIT for approval
    - Resume the same agent run with DeferredToolResults + message_history

    DBOS workflows:
    https://docs.dbos.dev/python/workflows/
    DBOS events:
    https://docs.dbos.dev/python/events/
    """

    # Run the agent. If a tool raises ApprovalRequired, output becomes DeferredToolRequests.
    result = dbos_agent.run_sync(prompt, output_type=str | DeferredToolRequests)

    # If the agent finished normally, return the final string.
    if isinstance(result.output, str):
        return result.output

    # Otherwise, the agent is asking for external approval/execution.
    deferred: DeferredToolRequests = result.output

    # Publish what needs approval.
    # In a real system, you'd store this in your DB and/or post to Slack/UI.
    # For minimalism, we expose it via a DBOS event (queryable by workflow_id).
    DBOS.set_event("pending_approval", deferred.model_dump())

    # DURABLE WAIT:
    # The workflow stops here and can survive crashes/redeploys for hours/days.
    #
    # The approver must later send an "approval_response" event with:
    #   {"tool_call_id": "...", "approved": true/false}
    approval: Dict[str, Any] = DBOS.wait_for_event(event_key="approval_response")

    tool_call_id = approval["tool_call_id"]
    approved = bool(approval["approved"])

    # Build the DeferredToolResults object expected by PydanticAI.
    # - If approved: provide the tool's "result" (or None if it returns nothing).
    # - If rejected: provide an exception to be surfaced to / handled by the agent.
    #
    # NOTE: This is intentionally minimal. If you need to return a specific payload
    # (e.g., tool output), set it here when approved.
    deferred_results = DeferredToolResults(
        results={
            tool_call_id: (None if approved else Exception("Rejected by human"))
        }
    )

    # Resume the SAME agent run:
    # - Pass the original message_history from the paused run
    # - Pass deferred_tool_results mapping tool_call_id -> outcome
    resumed = dbos_agent.run_sync(
        prompt,
        message_history=result.message_history,
        deferred_tool_results=deferred_results,
        output_type=str,
    )

    return resumed.output


# ----------------------------
# 3) External approval trigger (example)
# ----------------------------
#
# In practice this would live in a FastAPI endpoint, Slack bot, CLI, etc.
# It can run anywhere as long as it can call DBOS.send_event.
#
# The operator must extract the tool_call_id from the "pending_approval" event
# and then send an approval_response event.

def approve(tool_call_id: str) -> None:
    DBOS.send_event(
        event_key="approval_response",
        payload={"tool_call_id": tool_call_id, "approved": True},
    )

def reject(tool_call_id: str) -> None:
    DBOS.send_event(
        event_key="approval_response",
        payload={"tool_call_id": tool_call_id, "approved": False},
    )