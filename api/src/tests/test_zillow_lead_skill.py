"""One policy file, read by both agents (no network)."""

import warnings
from pathlib import Path

from pydantic_ai_skills import SkillsToolset

from api.src.sernia_ai.agent import skills_capability
from api.src.sernia_ai.zillow_leads import agent as agent_module
from api.src.sernia_ai.zillow_leads.skill import PREFACE, SKILL_NAME, lead_policy_skill


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

        assert skill.content == PREFACE + agent_module.load_policy()
        assert skill.description

    def test_general_agent_has_the_skill(self):
        skill = skills_capability._toolset.skills[SKILL_NAME]

        assert skill.content.endswith(agent_module.load_policy())

    def test_a_workspace_skill_of_the_same_name_cannot_replace_it(self, tmp_path: Path):
        stale = tmp_path / SKILL_NAME
        stale.mkdir()
        (stale / "SKILL.md").write_text(
            f"---\nname: {SKILL_NAME}\ndescription: An older copy.\n---\n\nOld rules.\n"
        )
        other = tmp_path / "lease-renewal"
        other.mkdir()
        (other / "SKILL.md").write_text(
            "---\nname: lease-renewal\ndescription: Renewals.\n---\n\nRenewal steps.\n"
        )

        with warnings.catch_warnings():
            warnings.simplefilter("error")  # a silent skip, not an override warning
            toolset = SkillsToolset(
                skills=[lead_policy_skill()], directories=[tmp_path], auto_reload=True
            )
            assert "Old rules." not in toolset.skills[SKILL_NAME].content
            assert "lease-renewal" in toolset.skills

            toolset.reload()

        assert toolset.skills[SKILL_NAME].content.endswith(agent_module.load_policy())
        assert "lease-renewal" in toolset.skills
