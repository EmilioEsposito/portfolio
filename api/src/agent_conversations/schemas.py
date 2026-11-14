"""Existing HTTP approval payloads; shared by both operational agents."""

from pydantic import BaseModel


class ApprovalDecisionRequest(BaseModel):
    """A single approval decision for one tool call."""

    tool_call_id: str
    approved: bool
    reason: str | None = None
    override_args: dict | None = None


class ApprovalRequest(BaseModel):
    """Batch approval request for one or more tool calls."""

    decisions: list[ApprovalDecisionRequest]
    # Optional user-typed message attached to this approval round. PydanticAI
    # bundles this alongside the ToolReturnParts into a single ModelRequest
    # (see CallToolsNode), so it persists as a real UserPromptPart — used by
    # the "deny with feedback" flow where the user's reply should live in
    # message history as a normal chat turn, not just as a tool-denial reason.
    user_message: str | None = None
