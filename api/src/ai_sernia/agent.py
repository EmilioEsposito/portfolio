"""
Main Sernia Capital AI agent definition.

Phase 1: Basic agent with static instructions, WebSearchTool, and WebFetchTool.
Custom toolsets (Quo, Google, ClickUp, etc.) will be added in later phases.
"""
from datetime import datetime
from zoneinfo import ZoneInfo

from pydantic_ai import Agent, RunContext

from api.src.ai_sernia.config import (
    MAIN_AGENT_MODEL,
    WEB_SEARCH_ALLOWED_DOMAINS,
    AGENT_NAME,
)
from api.src.ai_sernia.deps import SerniaDeps


STATIC_INSTRUCTIONS = """\
You are the Sernia Capital LLC AI assistant. You help the team manage their \
rental real estate business — answering questions, looking up information, \
managing tasks, and keeping track of important context across conversations.

You are helpful, concise, and business-oriented. You remember context from \
prior conversations and proactively update your memory when you learn something \
important.

When speaking via SMS, keep responses short. When speaking via email, be more \
formal. In web chat, use a natural conversational tone.
"""


def _build_builtin_tools() -> list:
    """Build builtin tools list. WebSearchTool/WebFetchTool require Anthropic."""
    tools: list = []
    if MAIN_AGENT_MODEL.startswith("anthropic:"):
        from pydantic_ai import WebSearchTool, WebFetchTool
        tools.append(WebSearchTool(allowed_domains=WEB_SEARCH_ALLOWED_DOMAINS))
        tools.append(WebFetchTool(allowed_domains=WEB_SEARCH_ALLOWED_DOMAINS))
    return tools


sernia_agent = Agent(
    MAIN_AGENT_MODEL,
    deps_type=SerniaDeps,
    instructions=STATIC_INSTRUCTIONS,
    # history_processors will be added in Phase 4
    builtin_tools=_build_builtin_tools(),
    instrument=True,
    name=AGENT_NAME,
)


@sernia_agent.instructions
def inject_context(ctx: RunContext[SerniaDeps]) -> str:
    now = datetime.now(ZoneInfo("America/New_York"))
    return (
        f"Current: {now.strftime('%Y-%m-%d %I:%M %p ET')}. "
        f"Speaking with {ctx.deps.user_name} via {ctx.deps.modality}."
    )
