"""The LLM judge: what it reads, what it returns, how it is measured (no network)."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError
from pydantic_ai.models.test import TestModel

from api.src.sernia_ai.zillow_leads.evals import judge as judge_module
from api.src.sernia_ai.zillow_leads.evals.cli import outputs_to_judge
from api.src.sernia_ai.zillow_leads.evals.judge import Judgement, agreement, half, judge_input
from api.src.sernia_ai.zillow_leads.evals.labels import Label, latest_per_output, output_hash

REPLY = {"action": "reply", "reply_body": "Hi Sam,\n\nSee you then.", "question": None}
ESCALATE = {"action": "escalate", "reply_body": None, "question": "Which unit?"}


class TestJudgement:
    def test_a_fail_must_name_what_was_broken(self):
        with pytest.raises(ValidationError):
            Judgement(verdict="fail", summary="Something is off.")

    def test_a_pass_cannot_carry_a_failure(self):
        with pytest.raises(ValidationError):
            Judgement(
                verdict="pass",
                failures=[{"rule": "Never use em-dashes.", "evidence": "see you — then"}],
                summary="Fine.",
            )

    def test_a_pass_may_still_raise_a_gap_for_the_owner(self):
        judgement = Judgement(
            verdict="pass", summary="Follows the rules.", policy_gap="Reply when a lead cancels?"
        )

        assert judgement.policy_gap


class TestJudgeCall:
    def test_the_judge_reads_the_instructions_the_case_and_the_decision(self):
        text = judge_input("Reply when owed.", "Current time: Monday", REPLY)

        assert text.index("Reply when owed.") < text.index("Current time: Monday")
        assert text.index("Current time: Monday") < text.index('"action": "reply"')

    @pytest.mark.asyncio
    async def test_the_judge_has_no_tools_and_returns_a_judgement(self):
        model = TestModel(
            custom_output_args={"verdict": "pass", "failures": [], "summary": "Follows the rules."}
        )

        with judge_module.judge_agent().override(model=model):
            result = await judge_module.judge("Reply when owed.", "Current time: Monday", REPLY)

        assert result == Judgement(verdict="pass", summary="Follows the rules.")
        params = model.last_model_request_parameters
        assert params is not None and params.function_tools == []

    def test_the_prompt_says_to_grade_on_what_the_case_contained(self):
        prompt = judge_module.load_judge_prompt()

        assert "Grade only on what the case contains" in prompt
        assert "policy_gap" in prompt


class TestAgreement:
    def test_two_rates_so_passing_everything_does_not_look_good(self):
        pairs = [("pass", "pass")] * 9 + [("fail", "pass")]

        result = agreement(pairs)

        assert result["true positive rate"] == 1.0
        assert result["true negative rate"] == 0.0

    def test_a_rate_with_nothing_to_measure_is_none(self):
        assert agreement([("pass", "fail")])["true negative rate"] is None

    def test_a_thread_always_falls_in_the_same_half(self):
        assert half("19e046d1530a400c") == half("19e046d1530a400c")
        assert {half(f"thread-{number}") for number in range(40)} == {"judge-dev", "judge-test"}


class TestWhatIsJudged:
    def test_the_latest_verdict_on_an_output_wins(self):
        key = output_hash(REPLY)
        early = Label(
            scenario_id="T-1",
            verdict="pass",
            output_hash=key,
            annotated_at=datetime(2026, 11, 12, 1, tzinfo=UTC),
        )
        late = early.model_copy(
            update={"verdict": "fail", "annotated_at": datetime(2026, 11, 12, 9, tzinfo=UTC)}
        )
        skipped = Label(scenario_id="T-2", verdict=None, output_hash="x")

        latest = latest_per_output([late, early, skipped])

        assert {key: label.verdict for key, label in latest.items()} == {("T-1", key): "fail"}

    def test_each_output_is_judged_once_and_only_if_a_person_graded_it(self):
        runs = {
            "experiment-1": [
                {"case_id": "T-1", "decision": REPLY},
                {"case_id": "T-2", "decision": ESCALATE},
            ],
            "experiment-2": [
                {"case_id": "T-1", "decision": REPLY},  # the same words again
                {"case_id": "T-3", "decision": REPLY},
            ],
        }

        everything = outputs_to_judge(runs, None)
        graded = outputs_to_judge(runs, {("T-1", output_hash(REPLY))})

        assert [(name, row["case_id"]) for name, row in everything] == [
            ("experiment-1", "T-1"),
            ("experiment-1", "T-2"),
            ("experiment-2", "T-3"),
        ]
        assert [(name, row["case_id"]) for name, row in graded] == [("experiment-1", "T-1")]
