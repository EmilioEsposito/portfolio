"""
Main Sernia Capital AI agent definition.

Phase 2: Memory system (workspace file tools + dynamic instructions)
     + HITL foundation (output_type includes DeferredToolRequests).

Custom toolsets (Quo, Google, ClickUp, etc.) will be added in later phases.
"""
from pydantic_ai import Agent, DeferredToolRequests

from api.src.ai_sernia.config import (
    MAIN_AGENT_MODEL,
    WEB_SEARCH_ALLOWED_DOMAINS,
    AGENT_NAME,
    WORKSPACE_PATH,
)
from api.src.ai_sernia.deps import SerniaDeps
from api.src.ai_sernia.memory import ensure_workspace_dirs
from api.src.ai_sernia.memory.toolset import memory_toolset
from api.src.ai_sernia.instructions import register_instructions


STATIC_INSTRUCTIONS = """\
You are the Sernia Capital LLC AI assistant. You help the team manage their \
rental real estate business — answering questions, looking up information, \
managing tasks, and keeping track of important context across conversations.

You are helpful, concise, and business-oriented.

## Memory System
You have a persistent workspace with files that survive across conversations. \
Your long-term memory (MEMORY.md) is injected at the start of every conversation.

- **Proactively update memory**: When you learn something important (a new \
property, tenant name, process, preference), write it to MEMORY.md.
- **Daily notes**: Use daily_notes/YYYY-MM-DD.md for transient context \
(today's tasks, call summaries, follow-ups).
- **Areas**: Use areas/ for deep topic knowledge (e.g. areas/properties.md).
- Use the file tools (read_file, write_file, append_to_file, list_directory) \
to manage your workspace.

## Approval-Gated Actions
Some tools (sending SMS, emails, etc.) require human approval before executing. \
When you use one of these tools, the system will pause and ask the user to \
approve or deny. Do NOT ask the user for confirmation before calling the tool — \
the approval system handles that automatically. Just call the tool naturally.
"""


def _build_builtin_tools() -> list:
    """Build builtin tools list. WebSearchTool/WebFetchTool require Anthropic."""
    tools: list = []
    if MAIN_AGENT_MODEL.startswith("anthropic:"):
        from pydantic_ai import WebSearchTool, WebFetchTool
        tools.append(WebSearchTool(allowed_domains=WEB_SEARCH_ALLOWED_DOMAINS))
        tools.append(WebFetchTool(allowed_domains=WEB_SEARCH_ALLOWED_DOMAINS))
    return tools


# Ensure workspace directory structure exists
ensure_workspace_dirs(WORKSPACE_PATH)

sernia_agent = Agent(
    MAIN_AGENT_MODEL,
    deps_type=SerniaDeps,
    instructions=STATIC_INSTRUCTIONS,
    output_type=[str, DeferredToolRequests],  # HITL foundation
    builtin_tools=_build_builtin_tools(),
    toolsets=[memory_toolset],
    instrument=True,
    name=AGENT_NAME,
)

# Register dynamic instructions (context, memory, daily notes, modality)
register_instructions(sernia_agent)
