"""
Main Sernia AI agent definition.

Includes memory system (workspace file tools + dynamic instructions),
HITL approval flow, and core toolsets (Quo, Gmail, Calendar, ClickUp, DB search).
"""
import logfire
from pydantic import BaseModel
from pydantic_ai import Agent, DeferredToolRequests, RunContext, WebSearchTool
from pydantic_ai.settings import ModelSettings
from pydantic_ai_filesystem_sandbox import FileSystemToolset, Mount, Sandbox, SandboxConfig


class NoAction(BaseModel):
    """Agent decided no human action is needed."""
    reason: str

from pydantic_ai_skills import SkillsToolset

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


def _build_builtin_tools() -> list:
    """Build builtin tools. WebSearchTool works on Anthropic, OpenAI Responses,
    Groq, Google, xAI, and OpenRouter; safe to include unconditionally as long
    as MAIN_AGENT_MODEL uses one of those providers.
    """
    return [WebSearchTool(allowed_domains=WEB_SEARCH_ALLOWED_DOMAINS)]


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

# Skills toolset — loads SKILL.md files from .workspace/skills/
# These are knowledge-repo content (sernia-knowledge git repo), not server-side code.
# A broken SKILL.md must never crash the server — all loading is error-wrapped.
# Initial discovery may find nothing if workspace hasn't been git-synced yet.
# Call reload_skills() after workspace init in lifespan to pick up synced skills.
skills_toolset = SkillsToolset(directories=[WORKSPACE_PATH / "skills"])


def reload_skills() -> None:
    """Re-discover skills from disk with per-directory error handling.

    Called during lifespan startup and before every agent run (via decorator).
    Per-directory try/except ensures a broken SKILL.md never crashes the agent.
    """
    skills_toolset._skills.clear()
    for skill_dir in skills_toolset._skill_directories:
        try:
            for skill in skill_dir.get_skills().values():
                skills_toolset._skills[skill.name] = skill
        except Exception:
            logfire.exception(
                "Failed to load skills from directory — skipping",
                directory=str(skill_dir._skill_directory),
            )

sernia_agent = Agent(
    MAIN_AGENT_MODEL,
    deps_type=SerniaDeps,
    instructions=[STATIC_INSTRUCTIONS, *DYNAMIC_INSTRUCTIONS],
    output_type=[str, NoAction, DeferredToolRequests],  # HITL foundation + silent triggers
    # `thinking` is PydanticAI's unified reasoning-effort setting (maps to
    # openai_reasoning_effort on OpenAI Responses, anthropic_thinking on
    # Anthropic). Cross-provider-safe.
    model_settings=ModelSettings(thinking="high"),
    builtin_tools=_build_builtin_tools(),
    toolsets=[
        ErrorLoggingToolset(filesystem_toolset.prefixed("workspace")),
        ErrorLoggingToolset(quo_toolset.prefixed("quo")),
        ErrorLoggingToolset(google_toolset.prefixed("google")),
        ErrorLoggingToolset(clickup_toolset.prefixed("clickup")),
        ErrorLoggingToolset(db_search_toolset.prefixed("db")),
        ErrorLoggingToolset(scheduling_toolset),
        ErrorLoggingToolset(code_toolset),
        ErrorLoggingToolset(duckdb_toolset),
        ErrorLoggingToolset(skills_toolset),
    ],
    history_processors=[summarize_tool_results, compact_history],
    instrument=True,
    name=AGENT_NAME,
)


@sernia_agent.instructions
async def refresh_skills_before_run(ctx: RunContext[SerniaDeps]) -> None:
    """Re-discover skills from disk before every agent run.

    Only reloads — does NOT return instructions (the toolset's own
    get_instructions() handles that, avoiding the old duplicate injection).
    Errors are swallowed so a broken SKILL.md never crashes the agent.
    """
    try:
        reload_skills()
    except Exception:
        logfire.exception("refresh_skills_before_run failed — agent will run with stale skills")


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
