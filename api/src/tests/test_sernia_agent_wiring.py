"""Wiring tests for the sernia_agent capability migration (pydantic-ai 1.106).

The agent moved from `builtin_tools=` / `history_processors=` / `instrument=`
to the core capabilities API:

- `WebSearch` / `WebFetch` capabilities replace the per-run
  `builtin_tools` juggling that lived in `model_config.build_run_kwargs()`.
  They adapt per provider: native web fetch is Anthropic-only and is dropped
  automatically on OpenAI runs (no local fallback — `local=False` — because
  the domain allowlist is only enforced natively).
- `ProcessHistory` wraps the existing custom history processors
  (summarize_tool_results, compact_history) unchanged.
- `Instrumentation` replaces `instrument=True`.

These tests guard the wiring and the provider-adaptive behavior we rely on,
plus a synthetic end-to-end run (TestModel, no network) that exercises the
full pipeline: dynamic instructions, capabilities, and output handling.
"""
import dataclasses
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
        processors = [
            c.processor for c in _agent_capabilities() if isinstance(c, ProcessHistory)
        ]
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
    Anthropic runs because OpenAI Responses raises UserError on it. The
    WebFetch capability now handles this by checking the model profile —
    these tests pin the profile facts that make that safe.
    """

    def test_openai_responses_supports_search_but_not_fetch(self):
        from pydantic_ai.models.openai import OpenAIResponsesModel
        from pydantic_ai.providers.openai import OpenAIProvider

        model = OpenAIResponsesModel("gpt-5.4", provider=OpenAIProvider(api_key="test-key"))
        assert WebSearchTool in model.profile.supported_native_tools
        assert WebFetchTool not in model.profile.supported_native_tools

    @pytest.mark.parametrize("model_name", ["claude-sonnet-4-6", "claude-opus-4-7"])
    def test_anthropic_supports_search_and_fetch(self, model_name: str):
        from pydantic_ai.models.anthropic import AnthropicModel
        from pydantic_ai.providers.anthropic import AnthropicProvider

        model = AnthropicModel(model_name, provider=AnthropicProvider(api_key="test-key"))
        assert WebSearchTool in model.profile.supported_native_tools
        assert WebFetchTool in model.profile.supported_native_tools


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

        async def fake_compact(
            ctx: RunContext, messages: list[ModelMessage]
        ) -> list[ModelMessage]:
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
