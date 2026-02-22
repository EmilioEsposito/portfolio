"""
Dependencies dataclass for the Sernia Capital AI agent.
"""
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from sqlalchemy.ext.asyncio import AsyncSession


@dataclass
class SerniaDeps:
    db_session: AsyncSession
    conversation_id: str
    user_identifier: str  # clerk_user_id, phone number, or email
    user_name: str  # Display name for the agent to use
    modality: Literal["sms", "email", "web_chat"]
    workspace_path: Path  # Path to .workspace/ sandbox root
