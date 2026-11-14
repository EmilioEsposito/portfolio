"""
Dependencies dataclass for the Sernia AI agent.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from sqlalchemy.ext.asyncio import AsyncSession


@dataclass
class SerniaDeps:
    db_session: AsyncSession
    conversation_id: str
    user_identifier: str  # clerk_user_id, phone number, or email
    user_name: str  # Display name for the agent to use
    user_email: str  # @serniacapital.com email for Google delegation
    modality: Literal["sms", "email", "web_chat"]
    workspace_path: Path  # Path to .workspace/ sandbox root
    # Set only by code that delivers the final answer into an SMS thread.
    # send_sms must not also deliver to this same recipient set.
    sms_reply_recipients: list[str] = field(default_factory=list)
    # Recipient domains this run may send threaded email replies to WITHOUT
    # the HITL approval card. Empty (the default) means every external email
    # pauses for approval. Retained for explicitly scoped callers; the Zillow
    # queue now uses its dedicated agent and never sets this field.
    # This is deliberately a recipient scope, not a run-wide on/off switch:
    # those runs are driven by inbound email written by strangers, so text in
    # a lead's message must not be able to turn "auto-reply to this lead" into
    # "auto-send anything to anyone". See google_tools.is_auto_approved_reply.
    auto_approved_reply_domains: frozenset[str] = frozenset()
    # Per-run tally of recoverable tool failures, keyed by a hash of
    # (tool_name, tool_args). Maintained by ErrorLoggingToolset so it can
    # detect a model looping on byte-identical failing arguments. Lives on
    # deps (not a module-level cache) so the scope is exactly one agent run.
    recoverable_tool_error_counts: dict[str, int] = field(default_factory=dict)
    # Per-run snapshots of dynamic instruction sections, keyed by function
    # name. pydantic-ai re-evaluates instructions before EVERY model request,
    # so anything volatile (the clock, MEMORY.md after the agent edits it)
    # would otherwise rewrite the system prompt mid-run and invalidate the
    # provider prompt cache for the whole message history that follows it.
    # See ``instructions.once_per_run``. Lives on deps for the same reason as
    # the tally above: the scope is exactly one agent run.
    instruction_snapshots: dict[str, str] = field(default_factory=dict)
