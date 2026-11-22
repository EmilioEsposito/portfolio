"""Rules about a reply that have exact answers, checked by code."""

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from pydantic import AwareDatetime, BaseModel
from pydantic_evals import Case, Dataset
from pydantic_evals.evaluators import Evaluator, EvaluatorContext

from api.src.showing_bookings.lead_bookings import wanted
from api.src.zillow_leads.agent import Escalate, NoReply, Reply, load_instructions
from api.src.zillow_leads.auto_reply import states_tour_time
from api.src.zillow_leads.case import LeadCase, ListingData, allowed_tour_times, times_allowed
from api.src.zillow_leads.evals.scenarios import CaseEdit, Scenario, case_for

SIGNATURE = "Sernia Capital Team"
GREETING = re.compile(r"\s*(hi|hello)\b", re.I)
# The site's address written without a scheme (and not as part of an email
# address or a longer host name): most email clients will not make it a link.
BARE_SITE = re.compile(r"(?<![\w@/.-])serniacapital\.com")
CLARIFICATION = "Quick clarification"
LINK = re.compile(r"https?://[^\s<>\"')\]]+")
# How the line offering a text reminder starts. The policy has a reply that
# confirms a tour carry it, with the lead's own link after it on the same
# line, while the case lists the lead's own showings, unless the case lists
# that showing, or the one it moves, with its details added.
DETAILS_LINE = "To get a text reminder and a calendar invite, add your details here:"


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


def _has_details_line(body: str, link: str | None = None) -> bool:
    """Whether a line of the reply is the details line, ending in ``link`` when given."""
    for line in body.splitlines():
        line = line.strip()
        if line.startswith(DETAILS_LINE) and (
            link is None or line[len(DETAILS_LINE) :].strip().rstrip(".") == link
        ):
            return True
    return False


class Booking(BaseModel):
    """What sending a decision would do to the lead's own showings."""

    action: Literal["none", "book", "move", "cancel"] = "none"
    unit: str | None = None
    start: AwareDatetime | None = None

    def matches(self, other: "Booking") -> bool:
        return (
            self.action == other.action
            and (self.unit or "").casefold() == (other.unit or "").casefold()
            and self.start == other.start
        )


def booking_change(case: LeadCase, outcome: Reply | NoReply | Escalate) -> Booking:
    """What code would book, move or cancel for this decision, read from the case.

    Mirrors ``showing_bookings.lead_bookings``: ``plan_reply`` for a reply,
    ``cancel_from_decision`` for a ``no_reply``, nothing for an escalation,
    with the case's own booked showings standing in for the thread's places in
    the database. A case without the own-showings section is one built with
    the switch off, when code books nothing.
    """
    if case.own_showings is None or isinstance(outcome, Escalate):
        return Booking()
    want = wanted(outcome.tour, outcome.lead, [listing.unit for listing in case.listings])
    if want is None or (isinstance(outcome, NoReply) and want.action != "cancel"):
        return Booking()
    places = [
        own.start
        for own in case.own_showings
        if own.status == "booked" and own.unit.casefold() == want.unit.casefold()
    ]
    if want.action == "cancel":
        return Booking(action="cancel", unit=want.unit) if places else Booking()
    others = [start for start in places if start != want.start]
    if want.start in places and not others:
        return Booking()  # already booked, nothing changes
    return Booking(action="move" if others else "book", unit=want.unit, start=want.start)


def _confirmed_place(case: LeadCase, outcome: Reply) -> tuple[str, datetime] | None:
    """The unit and time a reply records as confirmed, in person at a listed unit,
    while the case has the own-showings section; None otherwise."""
    if case.own_showings is None:
        return None
    want = wanted(outcome.tour, outcome.lead, [listing.unit for listing in case.listings])
    if want is None or want.action != "book" or want.start is None:
        return None
    return want.unit, want.start


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
        staff = {slot.agent.split()[0].lower() for slot in case.offerable_slots if slot.agent}
        words = {word.strip(".,!?:;()").lower() for word in body.split()}
        # Every link the model was given: in the case, or in its instructions.
        given = _bare(
            " ".join(
                [load_instructions()]
                + [f"{listing.booking_url} {listing.facts}" for listing in case.listings]
            )
        )
        checks = {
            # The same calculation as the automatic-send check. A time without
            # a UTC offset cannot be compared, so it fails.
            "offered_slots_allowed": times_allowed(case, outcome.offered_slots, outcome.lead.unit),
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
        # Only with the own-showings section, so a case without it is checked
        # exactly as before. For a reply that would book or move the lead's showing:
        change = booking_change(case, outcome)
        if change.action in ("book", "move") and change.start is not None:
            checks |= {
                # The words name the time code would book, in the policy's form.
                "states_tour_time": states_tour_time(case, outcome),
                "tour_time_allowed": change.start in allowed_tour_times(case, change.unit),
            }
        # For a reply that confirms a time, new, moved or the lead's own showing
        # restated: the details line when that showing's details are not in, and
        # never when they are. A moved showing keeps the details of the one it
        # replaces (``lead_bookings``), so for a move the lead's other booked
        # places at the unit count as well as the time confirmed.
        place = _confirmed_place(case, outcome)
        if place and (change.action in ("book", "move") or states_tour_time(case, outcome)):
            unit, start = place
            link = next(
                listing.booking_url
                for listing in case.listings
                if listing.unit.casefold() == unit.casefold()
            )
            added = any(
                own.status == "booked"
                and own.unit.casefold() == unit.casefold()
                and (own.start == start or change.action == "move")
                and own.details_added
                for own in case.own_showings or []
            )
            checks["details_line"] = (
                not _has_details_line(body)
                if added
                else bool(link) and _has_details_line(body, link)
            )
        # A virtual tour books nothing, so there is no place for the link to complete.
        elif (
            case.own_showings is not None
            and outcome.tour.virtual
            and outcome.tour.status in ("confirmed", "rescheduled")
        ):
            checks["details_line"] = not _has_details_line(body)
        return checks


@dataclass
class ExpectedOutcome(Evaluator[LeadCase, Reply | NoReply | Escalate, dict]):
    """A made-up scenario's expectations, when the run was given them.

    Reads ``expected`` from the case's metadata (an entry of the synthetic
    set's ``expected.json``): the action a person would want and, when set,
    what sending it should do to the lead's showings and whether the reply
    should carry the details line. Nothing without it.
    """

    def evaluate(
        self, ctx: EvaluatorContext[LeadCase, Reply | NoReply | Escalate, dict]
    ) -> dict[str, bool]:
        expected = (ctx.metadata or {}).get("expected")
        if not expected:
            return {}
        outcome, case = ctx.output, ctx.inputs
        checks = {"expected_action": outcome.action == expected["expected"]}
        if expected.get("booking") is not None:
            checks["expected_booking"] = booking_change(case, outcome).matches(
                Booking.model_validate(expected["booking"])
            )
        if expected.get("details_line") is not None:
            body = outcome.reply_body if isinstance(outcome, Reply) else ""
            checks["expected_details_line"] = _has_details_line(body) == expected["details_line"]
        return checks


def build_dataset(
    scenarios: list[Scenario],
    listing_data: ListingData,
    edits: dict[str, CaseEdit] | None = None,
    expected: dict[str, dict] | None = None,
) -> Dataset[LeadCase, Reply | NoReply | Escalate, dict]:
    """The scenarios as eval cases. ``expected`` (a synthetic set's ``expected.json``)
    adds the ``ExpectedOutcome`` checks to the scenarios it names."""
    edits = edits or {}
    expected = expected or {}
    return Dataset(
        name="zillow-lead-replies",
        cases=[
            Case(
                name=scenario.scenario_id,
                inputs=case_for(scenario, listing_data, edits.get(scenario.scenario_id)),
                metadata={
                    "actual_reply": scenario.actual_reply,
                    "actual_reply_delay_minutes": scenario.actual_reply_delay_minutes,
                    **(
                        {"expected": expected[scenario.scenario_id]}
                        if scenario.scenario_id in expected
                        else {}
                    ),
                },
            )
            for scenario in scenarios
        ],
        evaluators=[ReplyChecks(), ExpectedOutcome()],
    )
