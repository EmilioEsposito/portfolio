"""
Main Sernia AI agent definition.

Includes memory system (workspace file tools + dynamic instructions),
HITL approval flow, and core toolsets (OpenPhone, Gmail, Calendar, ClickUp, DB search).
"""
from pydantic_ai import Agent, DeferredToolRequests, RunContext
from pydantic_ai_filesystem_sandbox import FileSystemToolset, Mount, Sandbox, SandboxConfig

from api.src.sernia_ai.config import (
    MAIN_AGENT_MODEL,
    WEB_SEARCH_ALLOWED_DOMAINS,
    AGENT_NAME,
    WORKSPACE_PATH,
)
from api.src.sernia_ai.deps import SerniaDeps
from api.src.sernia_ai.instructions import STATIC_INSTRUCTIONS, DYNAMIC_INSTRUCTIONS
from api.src.sernia_ai.tools.openphone_tools import quo_toolset
from api.src.sernia_ai.tools.google_tools import google_toolset
from api.src.sernia_ai.tools.clickup_tools import clickup_toolset
from api.src.sernia_ai.tools.db_search_tools import db_search_toolset
from api.src.sernia_ai.tools.code_tools import code_toolset
from api.src.sernia_ai.sub_agents import summarize_tool_results, compact_history


def _build_builtin_tools() -> list:
    """Build builtin tools list. WebSearchTool/WebFetchTool require Anthropic."""
    tools: list = []
    if MAIN_AGENT_MODEL.startswith("anthropic:"):
        from pydantic_ai import WebSearchTool, WebFetchTool
        tools.append(WebSearchTool(allowed_domains=WEB_SEARCH_ALLOWED_DOMAINS))
        tools.append(WebFetchTool(allowed_domains=WEB_SEARCH_ALLOWED_DOMAINS))
    return tools


# Ensure workspace directory exists (full init with git sync happens in lifespan)
WORKSPACE_PATH.mkdir(parents=True, exist_ok=True)

# Sandboxed filesystem toolset for agent memory (.workspace/)
_sandbox = Sandbox(SandboxConfig(mounts=[
    Mount(
        host_path=WORKSPACE_PATH,
        mount_point="/workspace",
        mode="rw",
        suffixes=[".md", ".txt", ".json"],
        write_approval=False,
        read_approval=False,
    ),
]))
filesystem_toolset = FileSystemToolset(_sandbox)

sernia_agent = Agent(
    MAIN_AGENT_MODEL,
    deps_type=SerniaDeps,
    instructions=[STATIC_INSTRUCTIONS, *DYNAMIC_INSTRUCTIONS],
    output_type=[str, DeferredToolRequests],  # HITL foundation
    builtin_tools=_build_builtin_tools(),
    toolsets=[
        filesystem_toolset,
        quo_toolset,
        google_toolset,
        clickup_toolset,
        db_search_toolset,
        code_toolset,
    ],
    history_processors=[summarize_tool_results, compact_history],
    instrument=True,
    name=AGENT_NAME,
)


@sernia_agent.tool
async def search_files(
    ctx: RunContext[SerniaDeps],
    query: str,
    glob_pattern: str = "**/*.md",
) -> str:
    """Search workspace files for a text query. Returns matching lines with file paths.

    Args:
        query: Text to search for (case-insensitive substring match).
        glob_pattern: Glob pattern to filter files (default: all .md files).
    """
    results: list[str] = []
    for path in sorted(ctx.deps.workspace_path.glob(glob_pattern)):
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        rel = path.relative_to(ctx.deps.workspace_path)
        for i, line in enumerate(text.splitlines(), 1):
            if query.lower() in line.lower():
                results.append(f"/workspace/{rel}:{i}: {line.rstrip()}")
        if len(results) > 100:
            results.append("...(truncated at 100 matches)")
            break
    if not results:
        return f"No matches for '{query}' in {glob_pattern}"
    return "\n".join(results)
