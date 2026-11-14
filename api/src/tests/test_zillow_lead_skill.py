"""One policy file, read by both agents (no network)."""

import re
import warnings
from pathlib import Path

import pytest
from pydantic_ai import Agent
from pydantic_ai.capabilities import Instrumentation
from pydantic_ai.messages import (
    InstructionDeltaPart,
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel

from api.src.sernia_ai.deps import SerniaDeps
from api.src.sernia_ai.general_agent import (
    RESERVED_SKILL_NAMES,
    build_workspace_skills,
    sernia_agent,
)
from api.src.sernia_ai.zillow_leads import agent as agent_module
from api.src.sernia_ai.zillow_leads.skill import (
    PREFACE,
    SKILL_NAME,
    lead_policy_skill,
    lead_policy_text,
)


def _write_skill(workspace: Path, name: str, description: str, body: str) -> None:
    directory = workspace / ".claude" / "skills" / name
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\n{body}\n"
    )


class _Run:
    """What one run showed the model: its instructions, and what loading gave it."""

    def __init__(self) -> None:
        self.instructions = ""
        self.tools: list[str] = []
        self.loaded: list[str] = []


async def _run_with_skills(workspace: Path, load: list[str], instrumented: bool = False) -> _Run:
    """Run an agent wired like the general agent's skills, loading ``load``."""
    seen = _Run()

    def model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        if not seen.instructions:
            seen.instructions = info.instructions or ""
            seen.tools = [tool.name for tool in info.function_tools]
            if load:
                return ModelResponse(
                    parts=[ToolCallPart("load_capability", {"id": name}) for name in load]
                )
        return ModelResponse(parts=[TextPart("done")])

    agent = Agent(
        FunctionModel(model),
        deps_type=SerniaDeps,
        capabilities=[
            build_workspace_skills(workspace),
            lead_policy_skill(),
            *([Instrumentation()] if instrumented else []),
        ],
    )
    result = await agent.run("hello", deps=None)  # type: ignore[arg-type]
    # A loaded skill's text is recorded in history as an instruction change on
    # the request that answers the load.
    seen.loaded = [
        part.content
        for message in result.all_messages()
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, InstructionDeltaPart)
    ]
    return seen


class TestInstructions:
    def test_lead_agent_gets_role_then_policy_then_record(self):
        role = agent_module.ROLE_PATH.read_text()
        record = agent_module.RECORD_PATH.read_text()
        policy = agent_module.load_policy()

        assert agent_module.load_instructions() == f"{role}\n{policy}\n{record}"

    def test_policy_holds_nothing_true_only_for_the_lead_agent(self):
        policy = agent_module.load_policy()

        # The general agent reads this file too, and it does have tools.
        assert "You have no tools" not in policy
        assert "You have no tools" in agent_module.ROLE_PATH.read_text()
        # Output fields belong to the lead agent's own types.
        assert "## What to record" not in policy
        assert agent_module.RECORD_PATH.read_text().startswith("## What to record")
        assert policy.startswith("## Choosing the action")


class TestSkill:
    def test_skill_is_the_policy_file_after_a_preface(self):
        skill = lead_policy_skill()

        assert (
            lead_policy_text() == f"# Skill: {SKILL_NAME}\n\n{PREFACE}{agent_module.load_policy()}"
        )
        assert skill.get_instructions() == lead_policy_text()
        assert skill.id == SKILL_NAME
        assert skill.description
        assert skill.defer_loading is True

    def test_general_agent_has_the_skill(self):
        capabilities = sernia_agent.root_capability.capabilities
        (skill,) = [c for c in capabilities if getattr(c, "id", None) == SKILL_NAME]

        assert skill.defer_loading is True

    @pytest.mark.asyncio
    async def test_general_agent_loads_the_policy_with_load_capability(self, tmp_path: Path):
        seen = await _run_with_skills(tmp_path, load=[SKILL_NAME])

        assert "load_capability" in seen.tools
        assert f"- {SKILL_NAME}: " in seen.instructions
        # pydantic-ai trims surrounding whitespace from instructions.
        assert seen.loaded == [lead_policy_text().strip()]
        assert seen.loaded[0].endswith(agent_module.load_policy().strip())

    @pytest.mark.asyncio
    async def test_a_workspace_skill_of_the_same_name_cannot_replace_it(self, tmp_path: Path):
        _write_skill(tmp_path, SKILL_NAME, "An older copy.", "Old rules.")
        _write_skill(tmp_path, "lease-renewal", "Renewals.", "Renewal steps.")

        with warnings.catch_warnings():
            warnings.simplefilter("error")  # a silent skip, not an override warning
            seen = await _run_with_skills(tmp_path, load=[SKILL_NAME, "lease-renewal"])

        assert "An older copy." not in seen.instructions
        assert "- lease-renewal: Renewals." in seen.instructions
        assert seen.loaded == [
            lead_policy_text().strip(),
            "# Skill: lease-renewal\n\nRenewal steps.",
        ]
        assert not any("Old rules." in text for text in seen.loaded)


class TestWorkspaceSkills:
    @pytest.mark.asyncio
    async def test_workspace_skills_are_read_again_before_each_run(self, tmp_path: Path):
        _write_skill(tmp_path, "lease-renewal", "Renewals.", "Renewal steps.")
        first = await _run_with_skills(tmp_path, load=["lease-renewal"])

        _write_skill(tmp_path, "lease-renewal", "Renewals.", "Renewal steps, revised.")
        _write_skill(tmp_path, "move-out", "Move-outs.", "Move-out steps.")
        second = await _run_with_skills(tmp_path, load=["lease-renewal"])

        assert "- move-out: Move-outs." not in first.instructions
        assert "- move-out: Move-outs." in second.instructions
        assert first.loaded == ["# Skill: lease-renewal\n\nRenewal steps."]
        assert second.loaded == ["# Skill: lease-renewal\n\nRenewal steps, revised."]

    @pytest.mark.asyncio
    async def test_a_workspace_without_a_skills_directory_does_not_fail_the_run(
        self, tmp_path: Path
    ):
        seen = await _run_with_skills(tmp_path, load=[])

        assert f"- {SKILL_NAME}: " in seen.instructions

    @pytest.mark.asyncio
    async def test_a_skill_that_does_not_parse_is_skipped_and_the_rest_load(self, tmp_path: Path):
        _write_skill(tmp_path, "lease-renewal", "Renewals.", "Renewal steps.")
        broken = tmp_path / ".claude" / "skills" / "broken"
        broken.mkdir(parents=True)
        (broken / "SKILL.md").write_text("---\nname: [unclosed\n---\n\nBody.\n")

        with pytest.warns(UserWarning, match="broken"):
            seen = await _run_with_skills(tmp_path, load=["lease-renewal"])

        assert "- lease-renewal: Renewals." in seen.instructions
        assert "broken" not in seen.instructions
        assert seen.loaded == ["# Skill: lease-renewal\n\nRenewal steps."]

    @pytest.mark.asyncio
    async def test_a_skill_file_that_cannot_be_read_does_not_fail_the_run(self, tmp_path: Path):
        _write_skill(tmp_path, "lease-renewal", "Renewals.", "Renewal steps.")
        unreadable = tmp_path / ".claude" / "skills" / "lease-renewal" / "SKILL.md"
        unreadable.chmod(0)
        try:
            seen = await _run_with_skills(tmp_path, load=[])
        finally:
            unreadable.chmod(0o644)

        # No workspace skills this run; the deployed skill is still offered.
        assert "lease-renewal" not in seen.instructions
        assert f"- {SKILL_NAME}: " in seen.instructions

    @pytest.mark.asyncio
    async def test_a_skill_named_like_another_capability_does_not_fail_the_run(
        self, tmp_path: Path
    ):
        for name in RESERVED_SKILL_NAMES:
            _write_skill(tmp_path, name, "A clash.", "Clashing body.")
        _write_skill(tmp_path, "lease-renewal", "Renewals.", "Renewal steps.")

        seen = await _run_with_skills(tmp_path, load=["lease-renewal"], instrumented=True)

        assert "A clash." not in seen.instructions
        assert seen.loaded == ["# Skill: lease-renewal\n\nRenewal steps."]

    def test_every_capability_id_a_skill_could_take_is_reserved(self):
        """A skill name is lowercase letters, digits and single hyphens.

        Any other capability on the agent whose id fits that shape could be
        claimed by a workspace skill, which fails the run, so it must be in
        ``RESERVED_SKILL_NAMES``. Add the id there when this fails.
        """
        skill_name = re.compile(r"[a-z0-9]+(-[a-z0-9]+)*")
        ids = {
            capability.id
            for capability in sernia_agent.root_capability.capabilities
            if capability.id and capability.id != "skills"
        }

        assert {i for i in ids if skill_name.fullmatch(i)} <= RESERVED_SKILL_NAMES
