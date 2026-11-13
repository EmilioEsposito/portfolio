"""Wiring tests for the sernia_agent capability migration (pydantic-ai 1.106).

The agent moved from `builtin_tools=` / `history_processors=` / `instrument=`
to the core capabilities API:

- `WebSearch` / `WebFetch` capabilities replace the per-run
  `builtin_tools` juggling that lived in `model_config.build_run_kwargs()`.
  They adapt per provider: native web fetch is Anthropic-only and is dropped
  automatically on OpenRouter runs (no local fallback — `local=False` —
  because the domain allowlist is only enforced natively).
- `ProcessHistory` wraps the existing custom history processors
  (summarize_tool_results, compact_history) unchanged.
- `Instrumentation` replaces `instrument=True`.

These tests guard the wiring and the provider-adaptive behavior we rely on,
plus a synthetic end-to-end run (TestModel, no network) that exercises the
full pipeline: dynamic instructions, capabilities, and output handling.
"""

from pathlib import Path

import pytest
from pydantic_ai.capabilities import Instrumentation, ProcessHistory, WebFetch, WebSearch
from pydantic_ai.models.test import TestModel
from pydantic_ai.native_tools import WebFetchTool, WebSearchTool

from api.src.sernia_ai.agent import sernia_agent
from api.src.sernia_ai.config import WEB_SEARCH_ALLOWED_DOMAINS
from api.src.sernia_ai.sub_agents import compact_history, summarize_tool_results


def _agent_capabilities() -> list:
    return list(sernia_agent.root_capability.capabilities)


def _single(caps: list, cls: type):
    matches = [c for c in caps if isinstance(c, cls)]
    assert len(matches) == 1, f"expected exactly one {cls.__name__}, got {len(matches)}"
    return matches[0]


class TestCapabilityWiring:
    def test_web_search_capability_configured(self):
        ws = _single(_agent_capabilities(), WebSearch)
        assert ws.native.allowed_domains == WEB_SEARCH_ALLOWED_DOMAINS
        # optional=True: silently dropped on models without native support
        # (parity with the old per-run builtin_tools attachment).
        assert ws.native.optional is True
        # No local fallback: the allowlist is only enforced by native tools.
        assert ws.local is False

    def test_web_fetch_capability_configured(self):
        wf = _single(_agent_capabilities(), WebFetch)
        assert wf.native.allowed_domains == WEB_SEARCH_ALLOWED_DOMAINS
        assert wf.native.optional is True
        assert wf.local is False

    def test_history_processors_wired_in_order(self):
        processors = [c.processor for c in _agent_capabilities() if isinstance(c, ProcessHistory)]
        assert processors == [summarize_tool_results, compact_history]

    def test_instrumentation_enabled(self):
        _single(_agent_capabilities(), Instrumentation)

    def test_native_tools_collected(self):
        kinds = {type(t) for t in sernia_agent._cap_native_tools}  # noqa: SLF001
        assert WebSearchTool in kinds
        assert WebFetchTool in kinds


class TestProviderAdaptiveNativeTools:
    """Guard the provider behavior that replaced per-run WebFetchTool attachment.

    `model_config.build_run_kwargs()` used to add WebFetchTool only on
    Anthropic runs because the GPT provider raises UserError on it. The
    WebFetch capability now handles this by checking the model profile —
    these tests pin the profile facts that make that safe.
    """

    def test_openrouter_supports_search_but_not_fetch(self):
        """OpenRouter serves web search as a server tool; it has no web fetch.

        This is what makes `optional=True` on the WebFetchTool load-bearing on
        the default (GPT) model — the tool is dropped rather than raising.
        """
        from api.src.sernia_ai.model_config import build_run_kwargs

        model = build_run_kwargs("gpt-6-luna")["model"]
        assert WebSearchTool in model.profile["supported_native_tools"]
        assert WebFetchTool not in model.profile["supported_native_tools"]

    @pytest.mark.parametrize("model_name", ["claude-sonnet-4-6", "claude-opus-4-7"])
    def test_anthropic_supports_search_and_fetch(self, model_name: str):
        from pydantic_ai.models.anthropic import AnthropicModel
        from pydantic_ai.providers.anthropic import AnthropicProvider

        model = AnthropicModel(model_name, provider=AnthropicProvider(api_key="test-key"))
        assert WebSearchTool in model.profile["supported_native_tools"]
        assert WebFetchTool in model.profile["supported_native_tools"]


class TestSyntheticRun:
    """End-to-end agent run against TestModel — no network, no API keys.

    Exercises the full pipeline the way a real run does: dynamic instruction
    resolution (memory, filetree, modality), capability setup (native tools
    are dropped on TestModel, which supports none), history processing, and
    string output.
    """

    @pytest.fixture
    def deps(self, tmp_path: Path):
        from api.src.sernia_ai.deps import SerniaDeps

        workspace = tmp_path / "workspace"
        workspace.mkdir()
        (workspace / "MEMORY.md").write_text("Sernia owns the 320 Main St building.")
        (workspace / "areas").mkdir()
        (workspace / "areas" / "topic.md").write_text("notes")
        return SerniaDeps(
            db_session=None,  # type: ignore[arg-type] — no tool in this run touches the DB
            conversation_id="test-wiring-conv",
            user_identifier="user_test",
            user_name="Test User",
            user_email="test@serniacapital.com",
            modality="web_chat",
            workspace_path=workspace,
        )

    @pytest.mark.asyncio
    async def test_agent_runs_end_to_end_with_test_model(self, deps):
        # TestModel rejects native tools outright (it can't search the web),
        # so strip them for the run — same as the old builtin_tools days.
        # output_type=str: the agent's structured output spec (NoAction /
        # DeferredToolRequests) requires tool-mode output, which TestModel's
        # canned text can't produce.
        with sernia_agent.override(native_tools=[]):
            result = await sernia_agent.run(
                "Say hello.",
                deps=deps,
                model=TestModel(call_tools=[], custom_output_text="Hello from Sernia AI."),
                output_type=str,
            )
        assert result.output == "Hello from Sernia AI."

        # Dynamic instructions resolved into the first request.
        request = result.all_messages()[0]
        instructions = request.instructions or ""
        assert "Sernia owns the 320 Main St building." in instructions  # inject_memory
        assert "## Workspace Files" in instructions  # inject_filetree
        assert "web chat" in instructions.lower()  # inject_modality_guidance
        assert "Test User" in instructions  # inject_context

    @pytest.mark.asyncio
    async def test_history_processors_run_during_agent_run(self, deps, monkeypatch):
        """The ProcessHistory capabilities must actually execute per request."""
        from pydantic_ai import RunContext
        from pydantic_ai.capabilities import ProcessHistory
        from pydantic_ai.messages import ModelMessage

        calls: list[str] = []

        # The RunContext annotation matters: ProcessHistory uses it to decide
        # whether to pass ctx to the processor.
        async def fake_summarize(
            ctx: RunContext, messages: list[ModelMessage]
        ) -> list[ModelMessage]:
            calls.append("summarize")
            return messages

        async def fake_compact(ctx: RunContext, messages: list[ModelMessage]) -> list[ModelMessage]:
            calls.append("compact")
            return messages

        for cap in sernia_agent.root_capability.capabilities:
            if isinstance(cap, ProcessHistory):
                if cap.processor is summarize_tool_results:
                    monkeypatch.setattr(cap, "processor", fake_summarize)
                elif cap.processor is compact_history:
                    monkeypatch.setattr(cap, "processor", fake_compact)

        with sernia_agent.override(native_tools=[]):
            await sernia_agent.run(
                "Hi.",
                deps=deps,
                model=TestModel(call_tools=[], custom_output_text="ok"),
                output_type=str,
            )
        assert calls == ["summarize", "compact"]


class TestInstructionsStableWithinRun:
    """The system prompt must be byte-identical across every request of a run.

    pydantic-ai re-evaluates instruction functions before each model request.
    Providers cache prompts by exact prefix and the system prompt precedes the
    message history, so a section that changes mid-run (the clock, MEMORY.md
    after an edit) re-bills the entire history uncached on every later request.
    """

    @pytest.fixture
    def deps(self, tmp_path: Path):
        from api.src.sernia_ai.deps import SerniaDeps

        workspace = tmp_path / "workspace"
        workspace.mkdir()
        (workspace / "MEMORY.md").write_text("ORIGINAL MEMORY")
        return SerniaDeps(
            db_session=None,  # type: ignore[arg-type] — no tool in this run touches the DB
            conversation_id="test-stable-instructions",
            user_identifier="user_test",
            user_name="Test User",
            user_email="test@serniacapital.com",
            modality="web_chat",
            workspace_path=workspace,
        )

    @pytest.mark.asyncio
    async def test_clock_and_memory_changes_do_not_rewrite_the_prompt(self, deps, monkeypatch):
        from pydantic_ai.messages import ModelMessage, ModelRequest, ModelResponse, TextPart
        from pydantic_ai.messages import ToolCallPart as _ToolCallPart
        from pydantic_ai.models.function import AgentInfo, FunctionModel

        from api.src.sernia_ai import instructions

        ticks = iter(f"TICK-{i}" for i in range(100))
        monkeypatch.setattr(instructions, "format_current_datetime", lambda: next(ticks))

        def model_fn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            if len(messages) == 1:
                # Mid-run: the agent's own edit lands on disk, then it calls a tool.
                (deps.workspace_path / "MEMORY.md").write_text("EDITED MEMORY")
                (deps.workspace_path / "new_file.md").write_text("x")
                return ModelResponse(
                    parts=[
                        _ToolCallPart(
                            tool_name="workspace_search_files", args={"pattern": "nothing"}
                        )
                    ]
                )
            return ModelResponse(parts=[TextPart("done")])

        with sernia_agent.override(native_tools=[]):
            result = await sernia_agent.run(
                "Hi.", deps=deps, model=FunctionModel(model_fn), output_type=str
            )

        prompts = [m.instructions for m in result.all_messages() if isinstance(m, ModelRequest)]
        assert len(prompts) == 2, "expected one request per model call"
        assert prompts[0] == prompts[1]
        assert "TICK-0" in prompts[0]
        assert "ORIGINAL MEMORY" in prompts[0]

    @pytest.mark.asyncio
    async def test_each_run_takes_a_fresh_snapshot(self, deps, monkeypatch):
        """Snapshots are scoped to one run's deps — the next run sees new state."""
        from dataclasses import replace

        with sernia_agent.override(native_tools=[]):
            first = await sernia_agent.run(
                "Hi.",
                deps=deps,
                model=TestModel(call_tools=[], custom_output_text="ok"),
                output_type=str,
            )
            (deps.workspace_path / "MEMORY.md").write_text("EDITED MEMORY")
            second = await sernia_agent.run(
                "Hi again.",
                deps=replace(deps, instruction_snapshots={}),
                model=TestModel(call_tools=[], custom_output_text="ok"),
                output_type=str,
            )
        assert "ORIGINAL MEMORY" in (first.all_messages()[0].instructions or "")
        assert "EDITED MEMORY" in (second.all_messages()[0].instructions or "")

    def test_shared_sections_precede_per_run_context(self):
        """Stable-first ordering: what every conversation shares comes before
        what differs per run, so the shared part stays a cacheable prefix."""
        from api.src.sernia_ai.instructions import DYNAMIC_INSTRUCTIONS

        names = [fn.__name__ for fn in DYNAMIC_INSTRUCTIONS]
        assert names.index("inject_memory") < names.index("inject_context")
        assert names.index("inject_filetree") < names.index("inject_context")
        assert names[-1] == "inject_context"

    @pytest.mark.asyncio
    async def test_no_action_ends_the_run_without_running_tools_called_beside_it(self, deps):
        """A silent run must not act on its way out.

        The model returns `NoAction` and calls a tool in the same response.
        pydantic-ai 2.x would run that tool by default; the agent pins
        `end_strategy="early"`, the 1.x behaviour, so it is skipped. A harmless
        tool stands in here for `quo_send_sms` and the other tools that act.
        """
        from pydantic_ai.messages import (
            ModelMessage,
            ModelRequest,
            ModelResponse,
            ToolCallPart,
            ToolReturnPart,
        )
        from pydantic_ai.models.function import AgentInfo, FunctionModel

        from api.src.sernia_ai.agent import NoAction

        def model(_messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            (no_action,) = info.output_tools  # `NoAction` is the only output tool
            return ModelResponse(
                parts=[
                    ToolCallPart("workspace_search_files", {"pattern": "Sernia"}),
                    ToolCallPart(no_action.name, {"reason": "Nothing to do."}),
                ]
            )

        assert sernia_agent.end_strategy == "early"
        with sernia_agent.override(native_tools=[]):
            result = await sernia_agent.run("Check in.", deps=deps, model=FunctionModel(model))

        assert result.output == NoAction(reason="Nothing to do.")
        (search_return,) = [
            part
            for message in result.all_messages()
            if isinstance(message, ModelRequest)
            for part in message.parts
            if isinstance(part, ToolReturnPart) and part.tool_name == "workspace_search_files"
        ]
        # Had the search run, it would have found the fixture's MEMORY.md.
        assert "MEMORY.md" not in str(search_return.content)

    @pytest.mark.asyncio
    async def test_agent_offers_skills_and_loads_one_with_load_capability(self, deps):
        """The path a scheduled run takes: read the catalog, load a skill, act on it."""
        from pydantic_ai.messages import (
            InstructionDeltaPart,
            ModelMessage,
            ModelRequest,
            ModelResponse,
            TextPart,
            ToolCallPart,
        )
        from pydantic_ai.models.function import AgentInfo, FunctionModel

        from api.src.sernia_ai.zillow_leads.skill import SKILL_NAME, lead_policy_text

        first: list[AgentInfo] = []

        def model(_messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            if not first:
                first.append(info)
                return ModelResponse(parts=[ToolCallPart("load_capability", {"id": SKILL_NAME})])
            return ModelResponse(parts=[TextPart("Loaded.")])

        with sernia_agent.override(native_tools=[]):
            result = await sernia_agent.run(
                "How are Zillow leads handled?", deps=deps, model=FunctionModel(model)
            )

        assert result.output == "Loaded."
        tools = {tool.name for tool in first[0].function_tools}
        assert "load_capability" in tools
        # The 1.x skill tools are gone; the instructions name `load_capability`.
        assert not tools & {"list_skills", "load_skill", "read_skill_resource", "run_skill_script"}
        instructions = first[0].instructions or ""
        assert f"- {SKILL_NAME}: " in instructions
        assert "`load_capability`" in instructions
        loaded = [
            part.content
            for message in result.all_messages()
            if isinstance(message, ModelRequest)
            for part in message.parts
            if isinstance(part, InstructionDeltaPart)
        ]
        assert loaded == [lead_policy_text().strip()]

    @pytest.mark.asyncio
    async def test_loading_a_skill_that_is_already_loaded_does_not_end_the_run(self, deps):
        """Two bounced `load_capability` calls in a run used to end it with no reply.

        pydantic-ai answers a load of a skill that is already loaded with a
        retry, and a run fails once a tool runs out of retries. On an SMS
        trigger that means nothing is sent or saved.
        """
        from pydantic_ai.messages import (
            ModelMessage,
            ModelResponse,
            TextPart,
            ToolCallPart,
        )
        from pydantic_ai.models.function import AgentInfo, FunctionModel

        from api.src.sernia_ai.zillow_leads.skill import SKILL_NAME

        steps = iter(
            [
                ToolCallPart("load_capability", {"id": SKILL_NAME}),
                ToolCallPart("load_capability", {"id": SKILL_NAME}),
                ToolCallPart("load_capability", {"id": SKILL_NAME}),
                ToolCallPart("load_capability", {"id": "no-such-skill"}),
            ]
        )

        def model(_messages: list[ModelMessage], _info: AgentInfo) -> ModelResponse:
            part = next(steps, None)
            return ModelResponse(parts=[part or TextPart("Done.")])

        with sernia_agent.override(native_tools=[]):
            result = await sernia_agent.run("Hi.", deps=deps, model=FunctionModel(model))

        assert result.output == "Done."

    @pytest.mark.asyncio
    async def test_skills_are_read_after_the_workspace_pull(self, deps, monkeypatch):
        """A skill changed on GitHub reaches the first run after the change."""
        from pydantic_ai import Agent
        from pydantic_ai.messages import (
            InstructionDeltaPart,
            ModelMessage,
            ModelRequest,
            ModelResponse,
            TextPart,
            ToolCallPart,
        )
        from pydantic_ai.models.function import AgentInfo, FunctionModel

        from api.src.sernia_ai import agent as agent_module
        from api.src.sernia_ai import instructions
        from api.src.sernia_ai.deps import SerniaDeps
        from api.src.sernia_ai.memory import git_sync

        skill_dir = deps.workspace_path / ".claude" / "skills" / "scheduled-checks"

        async def pull(workspace_path):
            # What a pull of an edit made on GitHub does to the checkout.
            skill_dir.mkdir(parents=True, exist_ok=True)
            (skill_dir / "SKILL.md").write_text(
                "---\ndescription: Inbox checks.\n---\n\nPulled steps.\n"
            )

        monkeypatch.setattr(git_sync, "pull_workspace", pull)
        monkeypatch.setattr(instructions, "_pulled_conversation_ids", set())
        skills = agent_module.build_workspace_skills(deps.workspace_path)

        seen: list[str] = []

        def model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            if not seen:
                seen.append(info.instructions or "")
                return ModelResponse(
                    parts=[ToolCallPart("load_capability", {"id": "scheduled-checks"})]
                )
            return ModelResponse(parts=[TextPart("Done.")])

        # The agent's own skills read the deployed workspace path, so this
        # builds the same pair of capabilities over the test workspace.
        agent = Agent(
            FunctionModel(model),
            deps_type=SerniaDeps,
            capabilities=[skills, agent_module.lead_policy_capability],
        )
        result = await agent.run("Scheduled inbox check.", deps=deps)

        assert "- scheduled-checks: Inbox checks." in seen[0]
        loaded = [
            part.content
            for message in result.all_messages()
            if isinstance(message, ModelRequest)
            for part in message.parts
            if isinstance(part, InstructionDeltaPart)
        ]
        assert loaded == ["# Skill: scheduled-checks\n\nPulled steps."]
