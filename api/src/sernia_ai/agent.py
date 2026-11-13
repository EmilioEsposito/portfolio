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

from pathlib import Path

import logfire
from pydantic import BaseModel
from pydantic_ai import Agent, DeferredToolRequests, RunContext
from pydantic_ai.capabilities import (
    AbstractCapability,
    Instrumentation,
    ProcessHistory,
    WebFetch,
    WebSearch,
)
from pydantic_ai.native_tools import WebFetchTool, WebSearchTool
from pydantic_ai.workspaces import LocalWorkspaceBackend
from pydantic_ai_harness import Skills


class NoAction(BaseModel):
    """Agent decided no human action is needed."""

    reason: str


from api.src.sernia_ai.config import (
    AGENT_NAME,
    MAIN_AGENT_MODEL,
    WEB_SEARCH_ALLOWED_DOMAINS,
    WORKSPACE_PATH,
)
from api.src.sernia_ai.deps import SerniaDeps
from api.src.sernia_ai.instructions import (
    DYNAMIC_INSTRUCTIONS,
    STATIC_INSTRUCTIONS,
    refresh_from_remote,
)
from api.src.sernia_ai.model_config import resolve_model
from api.src.sernia_ai.sub_agents import compact_history, summarize_tool_results
from api.src.sernia_ai.tools._logging import ErrorLoggingToolset
from api.src.sernia_ai.tools.clickup_tools import clickup_toolset
from api.src.sernia_ai.tools.code_tools import code_toolset
from api.src.sernia_ai.tools.db_search_tools import db_search_toolset
from api.src.sernia_ai.tools.duckdb_tools import duckdb_toolset
from api.src.sernia_ai.tools.escalation_tools import escalation_toolset
from api.src.sernia_ai.tools.google_tools import google_toolset
from api.src.sernia_ai.tools.quo_tools import quo_toolset
from api.src.sernia_ai.tools.scheduling_tools import scheduling_toolset
from api.src.sernia_ai.tools.templated_message_tools import templated_message_toolset
from api.src.sernia_ai.tools.workspace_tools import CAPABILITY_ID as WORKSPACE_CAPABILITY_ID
from api.src.sernia_ai.tools.workspace_tools import SerniaWorkspace, build_workspace_toolset
from api.src.sernia_ai.zillow_leads.data_tools import CAPABILITY_ID as LEAD_DATA_CAPABILITY_ID
from api.src.sernia_ai.zillow_leads.data_tools import lead_data_tools
from api.src.sernia_ai.zillow_leads.skill import SKILL_NAME as LEAD_POLICY_SKILL_NAME
from api.src.sernia_ai.zillow_leads.skill import lead_policy_skill

# Ensure workspace directory exists (full init with git sync happens in lifespan)
WORKSPACE_PATH.mkdir(parents=True, exist_ok=True)

# File tools for the agent's memory (.workspace/): the harness FileSystem tools
# behind an adapter that keeps `/workspace/...` paths, the .md/.txt/.json limit
# and text error replies. See tools/workspace_tools.py. The harness only runs
# these tools when a capability contributes them, so they are a capability.
workspace_capability = SerniaWorkspace(
    ErrorLoggingToolset(build_workspace_toolset().prefixed("workspace"), name="workspace")
)

# Workspace skills — the harness `Skills` capability reads each SKILL.md under
# .workspace/.claude/skills/ and offers it as a deferred capability: the skill
# catalog (name + description) is injected into the instructions, and the model
# loads a skill's body with the `load_capability` tool. The path mirrors Claude
# Code's `.claude/skills/` convention so the same knowledge repo is
# interoperable with `cd workspace && claude` runs.
#
# The libraries are read at the start of every run, after the workspace's git
# pull, so edits made on GitHub or by `apps/sernia_mcp` against the shared
# sernia-knowledge repo show up without a server restart. A skill the agent
# edits during a run is read again from the next run on.
#
# `workspace=` reads them from the host path. The agent has no pydantic-ai
# workspace of its own; its file tools work in the sandbox mounted at
# /workspace, so a loaded skill does not name a host directory.
#
# The knowledge repo must never fail a run: a missing skills directory is
# skipped, a SKILL.md that does not parse is skipped with a warning, and
# `WorkspaceSkills` covers the rest.

# Workspace skill names that are never offered. A skill shares one id space
# with the agent's other capabilities, and a clash fails the run.
# - The Zillow lead policy is built in code (below), and a workspace skill of
#   the same name must not replace the deployed policy.
# - `zillow-lead-data` is the tools that change what the lead agent is told
#   about units (below).
# - `instrumentation` and `sernia-workspace` are the ids of the Instrumentation
#   capability and the workspace file tools.
# test_zillow_lead_skill.py checks this covers every capability on the agent.
RESERVED_SKILL_NAMES = frozenset(
    {
        LEAD_POLICY_SKILL_NAME,
        LEAD_DATA_CAPABILITY_ID,
        "instrumentation",
        WORKSPACE_CAPABILITY_ID,
    }
)


class WorkspaceSkills(Skills[SerniaDeps]):
    """Harness `Skills` that never fails a run over what is in the knowledge repo."""

    async def for_run(self, ctx: RunContext[SerniaDeps]) -> AbstractCapability[SerniaDeps]:
        # Pull the workspace before the skills are read. The pull also runs as
        # the first dynamic instruction, but capabilities are set up before
        # instructions, so without this a run would read the skills as they
        # were before the pull. It pulls once per conversation, so the later
        # call does nothing.
        if ctx.deps is not None:
            await refresh_from_remote(ctx)
        try:
            # The harness reports a skill it skips as a Python warning only.
            # Read the catalog once more to put each one in Logfire, where
            # someone asking why a skill is missing will look.
            for skipped in (await self.load()).skipped:
                logfire.warn("Workspace skill skipped: {reason}", reason=skipped.message)
            return await super().for_run(ctx)
        except Exception:
            # e.g. a SKILL.md the process cannot read. Returning this capability
            # as it is adds no skills, which is better than no run at all.
            logfire.exception("Workspace skills could not be read; running without them")
            return self


def build_workspace_skills(workspace_path: Path) -> WorkspaceSkills:
    return WorkspaceSkills(
        ".claude/skills",
        workspace=LocalWorkspaceBackend(workspace_path),
        exclude=RESERVED_SKILL_NAMES,
        unknown_names="skip",
        missing_directories="skip",
    )


workspace_skills = build_workspace_skills(WORKSPACE_PATH)

# One skill is built in code instead: the Zillow lead policy, which the lead
# agent also runs on. It sits in the same catalog as the workspace skills and
# loads the same way. See zillow_leads/skill.py.
lead_policy_capability = lead_policy_skill()

# The tools that read and change the lead agent's operating data (listings,
# rented status, booking links, showing rules). Deferred like a skill, so they
# cost one catalog line until a run needs them. See zillow_leads/data_tools.py.
lead_data_capability = lead_data_tools()

sernia_agent = Agent(
    # `resolve_model` turns the `openrouter:` string into the SerniaOpenRouterModel
    # subclass that records the billed cost, so the fallback default (when the DB
    # lookup is bypassed) is costed like any other run.
    resolve_model(MAIN_AGENT_MODEL),
    deps_type=SerniaDeps,
    instructions=[STATIC_INSTRUCTIONS, *DYNAMIC_INSTRUCTIONS],
    output_type=[str, NoAction, DeferredToolRequests],  # HITL foundation + silent triggers
    # When the model returns `NoAction` and calls tools in the same response,
    # end the run without running those tools: a silent trigger run must not
    # send an SMS or an email on its way out. This was pydantic-ai 1.x's
    # default; 2.x defaults to "graceful", which runs them.
    end_strategy="early",
    # A tool may be retried three times in a run before the run fails; 1.x and
    # 2.x default to one. `load_capability` answers a skill that is already
    # loaded, or a name the catalog does not have, with a retry, and a model
    # that tries twice would otherwise end the run with no reply (on an SMS
    # trigger, nothing is sent or saved). Retries are failed calls, so this
    # adds model requests, never side effects. Output validation keeps 1.
    retries={"tools": 3, "output": 1},
    toolsets=[
        ErrorLoggingToolset(quo_toolset.prefixed("quo"), name="quo"),
        ErrorLoggingToolset(google_toolset.prefixed("google"), name="google"),
        ErrorLoggingToolset(clickup_toolset.prefixed("clickup"), name="clickup"),
        ErrorLoggingToolset(db_search_toolset.prefixed("db"), name="db"),
        ErrorLoggingToolset(scheduling_toolset, name="scheduling"),
        ErrorLoggingToolset(templated_message_toolset, name="templated_messages"),
        ErrorLoggingToolset(escalation_toolset.prefixed("emergency"), name="emergency"),
        ErrorLoggingToolset(code_toolset, name="code"),
        ErrorLoggingToolset(duckdb_toolset, name="duckdb"),
    ],
    capabilities=[
        # The workspace file tools; each run's workspace is its deps.workspace_path.
        workspace_capability,
        workspace_skills,
        lead_policy_capability,
        lead_data_capability,
        # Provider-adaptive web tools (pydantic-ai core capabilities). Native
        # web search works on OpenRouter (as a server tool) and Anthropic.
        # Native web fetch is Anthropic-only — `optional=True` silently drops a
        # tool on models that don't support it, which replaces the old per-run
        # WebFetchTool attachment in model_config.build_run_kwargs().
        # local=False keeps behavior identical to the old builtin_tools
        # setup: no local fallback tool (allowed_domains is only enforced by
        # native tools, so a local fallback would bypass the domain
        # allowlist). The domains are set on the native tool instances, not
        # the capability, because capability-level domain constraints force
        # native-required mode, which raises instead of dropping. On OpenRouter
        # runs pydantic-ai sends `allowed_domains` in the parameters of the
        # `openrouter:web_search` server tool.
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
