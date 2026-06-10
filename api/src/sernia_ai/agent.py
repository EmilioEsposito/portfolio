"""
Main Sernia AI agent definition.

Includes memory system (workspace file tools + dynamic instructions),
HITL approval flow, and core toolsets (Quo, Gmail, Calendar, ClickUp, DB search).

Model selection is runtime-configurable via the ``model_config`` app_setting.
Call sites resolve the active model via ``model_config.resolve_active_run_kwargs()``
and spread the result into ``agent.run()`` / ``VercelAIAdapter.dispatch_request()``.
The ``model``/``model_settings`` passed at run-time override what's configured
here. Web search/fetch are provider-adaptive capabilities on the agent itself.
"""
import logfire
from pydantic import BaseModel
from pydantic_ai import Agent, DeferredToolRequests, RunContext
from pydantic_ai.capabilities import Instrumentation, ProcessHistory, WebFetch, WebSearch
from pydantic_ai.native_tools import WebFetchTool, WebSearchTool
from pydantic_ai_filesystem_sandbox import FileSystemToolset, Mount, Sandbox, SandboxConfig


class NoAction(BaseModel):
    """Agent decided no human action is needed."""
    reason: str

from pydantic_ai_skills import SkillsCapability

from api.src.sernia_ai.config import (
    MAIN_AGENT_MODEL,
    WEB_SEARCH_ALLOWED_DOMAINS,
    AGENT_NAME,
    WORKSPACE_PATH,
)
from api.src.sernia_ai.deps import SerniaDeps
from api.src.sernia_ai.instructions import STATIC_INSTRUCTIONS, DYNAMIC_INSTRUCTIONS
from api.src.sernia_ai.tools._logging import ErrorLoggingToolset
from api.src.sernia_ai.tools.quo_tools import quo_toolset
from api.src.sernia_ai.tools.google_tools import google_toolset
from api.src.sernia_ai.tools.clickup_tools import clickup_toolset
from api.src.sernia_ai.tools.db_search_tools import db_search_toolset
from api.src.sernia_ai.tools.code_tools import code_toolset
from api.src.sernia_ai.tools.duckdb_tools import duckdb_toolset
from api.src.sernia_ai.tools.scheduling_tools import scheduling_toolset
from api.src.sernia_ai.sub_agents import summarize_tool_results, compact_history


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

# Skills capability — discovers SKILL.md files at .workspace/.claude/skills/,
# injects the skill registry (name + description) into the system prompt, and
# exposes the list_skills / load_skill / read_skill_resource / run_skill_script
# tools. The path mirrors Claude Code's `.claude/skills/` convention so the
# same knowledge repo is interoperable with `cd workspace && claude` runs.
#
# auto_reload=True re-scans the directory before every run, so edits made by
# the agent itself (or by `apps/sernia_mcp` against the shared sernia-knowledge
# repo) show up without a server restart. Replaces the old SkillsToolset +
# manual ``reload_skills()`` decorator pattern, which never injected the
# skill registry into the prompt — meaning the agent had skill *tools* but
# couldn't see what skills existed without calling list_skills first.
skills_capability = SkillsCapability(
    directories=[WORKSPACE_PATH / ".claude" / "skills"],
    auto_reload=True,
)

sernia_agent = Agent(
    MAIN_AGENT_MODEL,
    deps_type=SerniaDeps,
    instructions=[STATIC_INSTRUCTIONS, *DYNAMIC_INSTRUCTIONS],
    output_type=[str, NoAction, DeferredToolRequests],  # HITL foundation + silent triggers
    toolsets=[
        ErrorLoggingToolset(filesystem_toolset.prefixed("workspace"), name="workspace"),
        ErrorLoggingToolset(quo_toolset.prefixed("quo"), name="quo"),
        ErrorLoggingToolset(google_toolset.prefixed("google"), name="google"),
        ErrorLoggingToolset(clickup_toolset.prefixed("clickup"), name="clickup"),
        ErrorLoggingToolset(db_search_toolset.prefixed("db"), name="db"),
        ErrorLoggingToolset(scheduling_toolset, name="scheduling"),
        ErrorLoggingToolset(code_toolset, name="code"),
        ErrorLoggingToolset(duckdb_toolset, name="duckdb"),
    ],
    capabilities=[
        skills_capability,
        # Provider-adaptive web tools (pydantic-ai core capabilities). Native
        # web search works on both OpenAI Responses and Anthropic. Native web
        # fetch is Anthropic-only — `optional=True` silently drops a tool on
        # models that don't support it, which replaces the old per-run
        # WebFetchTool attachment in model_config.build_run_kwargs().
        # local=False keeps behavior identical to the old builtin_tools
        # setup: no local fallback tool (allowed_domains is only enforced by
        # native tools, so a local fallback would bypass the domain
        # allowlist). The domains are set on the native tool instances, not
        # the capability, because capability-level domain constraints force
        # native-required mode, which raises instead of dropping.
        WebSearch(
            native=WebSearchTool(allowed_domains=WEB_SEARCH_ALLOWED_DOMAINS, optional=True),
            local=False,
        ),
        WebFetch(
            native=WebFetchTool(allowed_domains=WEB_SEARCH_ALLOWED_DOMAINS, optional=True),
            local=False,
        ),
        # History processors run in capability order before each model
        # request: oversized tool-result summarization first, then whole-
        # history compaction (same order as the old history_processors list).
        ProcessHistory(summarize_tool_results),
        ProcessHistory(compact_history),
        Instrumentation(),
    ],
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
                      Use relative patterns like "**/*.md", not absolute paths.
    """
    # Strip /workspace/ prefix and leading slashes - glob() only supports relative patterns
    pattern = glob_pattern
    if pattern.startswith("/workspace/"):
        pattern = pattern[len("/workspace/") :]
    pattern = pattern.lstrip("/")

    results: list[str] = []
    for path in sorted(ctx.deps.workspace_path.glob(pattern)):
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
