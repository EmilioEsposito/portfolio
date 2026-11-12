"""Rules about a reply that have exact answers, checked by code."""

import re
from dataclasses import dataclass

from pydantic_evals import Case, Dataset
from pydantic_evals.evaluators import Evaluator, EvaluatorContext

from api.src.sernia_ai.zillow_leads.agent import Escalate, NoReply, Reply, load_instructions
from api.src.sernia_ai.zillow_leads.case import LeadCase, OperatingData
from api.src.sernia_ai.zillow_leads.evals.scenarios import Scenario, case_for

SIGNATURE = "Sernia Capital Team"
LINK = re.compile(r"https?://[^\s<>\"')\]]+")


def _bare(text: str) -> str:
    """Links without scheme, ``www.`` or trailing punctuation, so the same
    address written two ways compares equal."""
    return re.sub(r"https?://(www\.)?", "", text.lower()).rstrip("/.,")


@dataclass
class ReplyChecks(Evaluator[LeadCase, Reply | NoReply | Escalate, dict]):
    """Rules about a reply that have exact answers. Skipped when there is no reply."""

    def evaluate(
        self, ctx: EvaluatorContext[LeadCase, Reply | NoReply | Escalate, dict]
    ) -> dict[str, bool]:
        outcome, case = ctx.output, ctx.inputs
        if not isinstance(outcome, Reply):
            return {}
        body = outcome.reply_body
        allowed = {slot.start for slot in case.offerable_slots} | {
            tour.start for tour in case.booked_tours
        }
        staff = {slot.agent.split()[0].lower() for slot in case.offerable_slots if slot.agent}
        words = {word.strip(".,!?:;()").lower() for word in body.split()}
        # Every link the model was given: in the case, or in its instructions.
        given = _bare(
            " ".join(
                [load_instructions()]
                + [f"{listing.booking_url} {listing.facts}" for listing in case.listings]
            )
        )
        return {
            # A time without a UTC offset cannot be compared, so it fails.
            "offered_slots_allowed": all(
                slot.tzinfo is not None and slot in allowed for slot in outcome.offered_slots
            ),
            "no_em_dash": "—" not in body,
            "has_signature": SIGNATURE in body,
            "no_staff_names": not (staff & words),
            # A link the case did not supply was written from memory or invented.
            "links_allowed": all(
                re.search(rf"(?<![\w.-]){re.escape(_bare(link))}(?![\w/-])", given)
                for link in LINK.findall(body)
            ),
        }


def build_dataset(
    scenarios: list[Scenario], operating: OperatingData
) -> Dataset[LeadCase, Reply | NoReply | Escalate, dict]:
    return Dataset(
        name="zillow-lead-replies",
        cases=[
            Case(
                name=scenario.scenario_id,
                inputs=case_for(scenario, operating),
                metadata={
                    "actual_reply": scenario.actual_reply,
                    "actual_reply_delay_minutes": scenario.actual_reply_delay_minutes,
                },
            )
            for scenario in scenarios
        ],
        evaluators=[ReplyChecks()],
    )
