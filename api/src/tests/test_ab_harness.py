"""Offline smoke test for the A/B harness.

The harness reaches into agent wiring that changes with pydantic-ai upgrades,
and nothing else exercises it — it previously broke on a private attribute
rename and nobody noticed until the next experiment. This runs one case end
to end against a local model, so a wiring break fails CI instead.
"""

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from api.src.sernia_ai.ab_tests import _core


@pytest.mark.asyncio
@pytest.mark.parametrize("disable_builtin_tools", [True, False])
async def test_harness_runs_a_case_end_to_end(
    tmp_path: Path, monkeypatch, capsys, disable_builtin_tools: bool
):
    seen: list[AgentInfo] = []

    def model_fn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        seen.append(info)
        return ModelResponse(parts=[TextPart("rental real estate is housing leased to tenants")])

    monkeypatch.setattr(_core, "WORKSPACE_PATH", tmp_path)
    with patch("api.src.sernia_ai.memory.git_sync.pull_workspace", new=AsyncMock()):
        await _core.run_experiment(
            experiment_name="smoke",
            prompts=["What is rental real estate?"],
            variants={"local": FunctionModel(model_fn)},
            # Native tools can't run on a local model; the rote path clears
            # them, the search path relies on the model dropping them.
            disable_builtin_tools=disable_builtin_tools,
        )

    assert len(seen) == 1, "expected exactly one model request for one case"
    # Default is side-effect free: none of the production toolsets (SMS,
    # email, ClickUp, scheduling, workspace writes) reach the model. Only the
    # read-only workspace search registered directly on the agent remains,
    # with `load_capability`, which only loads a skill's instructions.
    assert {t.name for t in seen[0].function_tools} <= {"search_files", "load_capability"}
    assert "evaluating variant=local" in capsys.readouterr().out
