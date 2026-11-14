"""Rules about a reply that have exact answers, checked by code."""

import re
from dataclasses import dataclass

from pydantic_evals import Case, Dataset
from pydantic_evals.evaluators import Evaluator, EvaluatorContext

from api.src.zillow_leads.agent import Escalate, NoReply, Reply, load_instructions
from api.src.zillow_leads.case import LeadCase, ListingData
from api.src.zillow_leads.evals.scenarios import CaseEdit, Scenario, case_for

SIGNATURE = "Sernia Capital Team"
GREETING = re.compile(r"\s*(hi|hello)\b", re.I)
# The site's address written without a scheme (and not as part of an email
# address or a longer host name): most email clients will not make it a link.
BARE_SITE = re.compile(r"(?<![\w@/.-])serniacapital\.com")
CLARIFICATION = "Quick clarification"
LINK = re.compile(r"https?://[^\s<>\"')\]]+")


def _bare(text: str) -> str:
    """Links without scheme, ``www.`` or trailing punctuation, so the same
    address written two ways compares equal."""
    return re.sub(r"https?://(www\.)?", "", text.lower()).rstrip("/.,")


def _clarification_leads(body: str) -> bool:
    """A clarification, when there is one, comes right after the greeting.

    One short paragraph may sit between them: the policy allows a line saying
    that something the lead asked for cannot be done.
    """
    paragraphs = [part.strip() for part in body.split("\n\n") if part.strip()]
    places = [index for index, part in enumerate(paragraphs) if part.startswith(CLARIFICATION)]
    if CLARIFICATION not in body:
        return True
    return bool(places) and places[0] <= 2


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
            "has_greeting": bool(GREETING.match(body)),
            "site_address_is_a_link": not BARE_SITE.search(body),
            "clarification_leads": _clarification_leads(body),
            "has_signature": SIGNATURE in body,
            "no_staff_names": not (staff & words),
            # A link the case did not supply was written from memory or invented.
            "links_allowed": all(
                re.search(rf"(?<![\w.-]){re.escape(_bare(link))}(?![\w/-])", given)
                for link in LINK.findall(body)
            ),
        }


def build_dataset(
    scenarios: list[Scenario],
    listing_data: ListingData,
    edits: dict[str, CaseEdit] | None = None,
) -> Dataset[LeadCase, Reply | NoReply | Escalate, dict]:
    edits = edits or {}
    return Dataset(
        name="zillow-lead-replies",
        cases=[
            Case(
                name=scenario.scenario_id,
                inputs=case_for(scenario, listing_data, edits.get(scenario.scenario_id)),
                metadata={
                    "actual_reply": scenario.actual_reply,
                    "actual_reply_delay_minutes": scenario.actual_reply_delay_minutes,
                },
            )
            for scenario in scenarios
        ],
        evaluators=[ReplyChecks()],
    )
