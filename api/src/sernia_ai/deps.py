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
    # When True, the external-email HITL approval card is skipped for this run.
    # Set by triggers that have an explicit per-trigger opt-out (e.g. Zillow
    # auto-reply with `require_approval=False`). Defaults to False so every
    # other code path keeps the standard HITL gate.
    bypass_external_email_approval: bool = False
    # Per-run tally of recoverable tool failures, keyed by a hash of
    # (tool_name, tool_args). Maintained by ErrorLoggingToolset so it can
    # detect a model looping on byte-identical failing arguments. Lives on
    # deps (not a module-level cache) so the scope is exactly one agent run.
    recoverable_tool_error_counts: dict[str, int] = field(default_factory=dict)
