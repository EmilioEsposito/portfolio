"""With the booking integration off, the lead case reads exactly as it did.

``zillow_case_golden.json`` holds, for a set of cases built from the lead
agent's existing test fixtures (and one eval scenario with a hand edit), the
rendered case, the automatic-send problems and the evals' exact-answer checks
(``ReplyChecks``) for a set of replies, as the code produced them before the case
learned about per-lead links, a lead's own showings and per-unit tour times.
Every case here is built the way production builds it with
``lead_agent_uses_open_times`` off: no per-lead links, no own showings, tour
times with no unit. The test fails on any change of a byte.

Regenerate only on purpose, when a change to the case is meant to show with
the switch off too:

    uv run python -m api.src.tests.test_zillow_lead_case_unchanged --write
"""

import json
import sys
from datetime import date, datetime, time, timedelta
from pathlib import Path

import pytest
from pydantic_evals.evaluators import EvaluatorContext
from pydantic_evals.otel.span_tree import SpanTree

from api.src.rental_listings.schema import PreferredWindow
from api.src.tests.test_zillow_lead_agent import (
    ET,
    LISTING_DATA,
    MONDAY_9AM,
    decision,
    message,
    thread,
)
from api.src.zillow_leads.auto_reply import reply_problems
from api.src.zillow_leads.case import (
    BookedTour,
    LeadCase,
    Listing,
    TourSlot,
    build_case,
    render_case,
)
from api.src.zillow_leads.evals.checks import ReplyChecks
from api.src.zillow_leads.evals.scenarios import CaseEdit, case_for, scenarios_from_thread

GOLDEN = Path(__file__).with_name("zillow_case_golden.json")
TUESDAY_10 = datetime(2026, 5, 5, 10, 0, tzinfo=ET)
THURSDAY_3 = datetime(2026, 5, 7, 15, 0, tzinfo=ET)
SIGNED = "\n\nSernia Capital Team"


def _cases() -> dict[str, LeadCase]:
    links = {unit.unit: "https://book.example/tours" for unit in LISTING_DATA.listings}
    per_unit = LISTING_DATA.model_copy(
        update={
            "listings": [
                Listing(unit="12-A", rent=1500),
                Listing(unit="12-C", address="12 Example St, Unit C"),
                Listing(unit="12-D", rented_on=date(2026, 5, 4)),
            ],
        }
    )
    rich = LISTING_DATA.model_copy(
        update={
            "listings": [
                Listing(
                    unit="12-A",
                    address="12 Example St",
                    rent=1500,
                    lease_start_earliest=date(2026, 7, 1),
                    virtual_tours=True,
                    facts="Second floor\n- Dishwasher\n* Street parking",
                    internal_notes=["Resident prefers afternoons", "Lease start can move"],
                    preferred_times=[
                        PreferredWindow(days=["tue", "thu"], start=time(15, 0), end=time(18, 0))
                    ],
                ),
                Listing(unit="12-C", lease_start_latest=date(2026, 8, 15)),
            ]
        }
    )
    history = thread(
        message(0, body="Is it available?"),
        message(45, sernia=True, body="Yes, tours Tuesday."),
        message(90, body="Can I come Thursday at 3?"),
        message(91, body=""),
    )
    return {
        "plain": build_case(thread(message(0)), LISTING_DATA, upto=0),
        "shared_link": build_case(thread(message(0)), LISTING_DATA, booking_links=links, upto=0),
        "per_unit_links_and_rented": build_case(
            thread(message(0)),
            per_unit,
            upto=0,
            booking_links={"12-A": "https://book.example/12a", "12-C": "https://book.example/any"},
        ),
        "rich_listing_booked_and_preferred": build_case(
            thread(message(0, body="Tuesday afternoon?")),
            rich,
            upto=0,
            booked_tours=[
                BookedTour(unit="12-A", start=MONDAY_9AM - timedelta(days=1)),
                BookedTour(unit="12-A", start=datetime(2026, 5, 5, 16, 0, tzinfo=ET)),
            ],
        ),
        "slots_from_a_source": build_case(
            thread(message(0)),
            rich,
            upto=0,
            slots=[
                TourSlot(start=THURSDAY_3, agent="Casey Agent"),
                TourSlot(start=THURSDAY_3, agent="Drew Agent"),
                TourSlot(start=TUESDAY_10, agent="Casey Agent"),
            ],
        ),
        "no_tour_source": build_case(
            thread(message(0)), LISTING_DATA, booking_links=links, upto=0, slots=[]
        ),
        "history_cut_at_a_decision": build_case(history, LISTING_DATA, booking_links=links, upto=3),
        "nothing_listed": build_case(
            thread(message(0)),
            LISTING_DATA.model_copy(update={"listings": [], "rented_units": []}),
            upto=0,
        ),
        # An eval scenario with a hand edit, as the evals build it.
        "eval_scenario_with_an_edit": case_for(
            scenarios_from_thread(history)[-1],
            rich,
            CaseEdit(
                offered=[THURSDAY_3],
                booked=[BookedTour(unit="12-C", start=datetime(2026, 5, 6, 11, 0, tzinfo=ET))],
                note="Made up.",
            ),
        ),
    }


def _replies() -> dict[str, dict]:
    return {
        "clean": {},
        "policy_link": {"reply_body": "Hello,\n\nApply at https://serniacapital.com/." + SIGNED},
        "shared_link": {"reply_body": "Hello,\n\nBook: https://book.example/tours." + SIGNED},
        "unit_link": {"reply_body": "Hello,\n\nBook: https://book.example/12a" + SIGNED},
        "foreign_link": {"reply_body": "Go to https://attacker.example/collect" + SIGNED},
        "bare_www": {"reply_body": "Hello, see www.attacker.example today" + SIGNED},
        "offered_time": {"offered_slots": [TUESDAY_10.isoformat()]},
        "offered_time_for_a_unit": {
            "lead": {"unit": "12-A"},
            "offered_slots": [THURSDAY_3.isoformat(), TUESDAY_10.isoformat()],
        },
        "offered_booked_time": {"offered_slots": ["2026-05-05T16:00:00-04:00"]},
        "time_not_offered": {"offered_slots": ["2026-05-04T10:00:00-04:00"]},
        "blank": {"reply_body": "   "},
    }


def _eval_checks(case: LeadCase, outcome) -> dict[str, bool]:
    """The evals' exact-answer checks (``ReplyChecks``) on one reply."""
    ctx = EvaluatorContext(
        name=case.case_id,
        inputs=case,
        metadata={},
        expected_output=None,
        output=outcome,
        duration=0.0,
        _span_tree=SpanTree(),
        attributes={},
        metrics={},
    )
    return ReplyChecks().evaluate(ctx)


def snapshot() -> dict:
    replies = {name: decision(**fields) for name, fields in _replies().items()}
    return {
        name: {
            "rendered": render_case(case),
            "problems": {
                reply: reply_problems(case, outcome) for reply, outcome in replies.items()
            },
            "eval_checks": {
                reply: _eval_checks(case, outcome) for reply, outcome in replies.items()
            },
        }
        for name, case in _cases().items()
    }


@pytest.mark.parametrize("name", sorted(_cases()))
def test_the_rendered_case_and_send_checks_are_unchanged(name):
    expected = json.loads(GOLDEN.read_text())[name]
    actual = snapshot()[name]
    assert actual["rendered"] == expected["rendered"]
    assert actual["problems"] == expected["problems"]
    assert actual["eval_checks"] == expected["eval_checks"]


def test_every_fixture_case_is_in_the_snapshot():
    assert sorted(snapshot()) == sorted(json.loads(GOLDEN.read_text()))


if __name__ == "__main__" and "--write" in sys.argv:
    GOLDEN.write_text(json.dumps(snapshot(), indent=2, sort_keys=True) + "\n")
    print(f"wrote {GOLDEN}")
