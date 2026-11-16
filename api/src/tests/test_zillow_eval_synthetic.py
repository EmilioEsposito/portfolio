"""The made-up booking scenarios, checked without a model.

Each builds a case with per-lead links, the lead's own showings and per-unit
times; its good reply keeps every exact rule (the eval's checks and the
automatic-send check); and the files written for the ``run`` command rebuild
the same cases. Running them through the model is not done here.
"""

import hashlib
import json
from datetime import UTC

import pytest
from pydantic_evals.evaluators import EvaluatorContext
from pydantic_evals.otel.span_tree import SpanTree

from api.src.tests.test_zillow_lead_agent import decision
from api.src.zillow_leads.auto_reply import reply_problems
from api.src.zillow_leads.case import OWN_SHOWINGS_HEADING, ListingData, render_case
from api.src.zillow_leads.evals import synthetic
from api.src.zillow_leads.evals.checks import ReplyChecks
from api.src.zillow_leads.evals.scenarios import (
    CaseEdit,
    case_for,
    edit_fingerprint,
    read_case_edits,
    read_scenarios,
)

ITEMS = {item.scenario.scenario_id: item for item in synthetic.scenarios()}


def checks(case, outcome) -> dict[str, bool]:
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


@pytest.mark.parametrize("scenario_id", sorted(ITEMS))
def test_each_case_carries_what_the_booking_service_adds(scenario_id):
    item = ITEMS[scenario_id]
    case = case_for(item.scenario, synthetic.LISTINGS, item.edit)
    text = render_case(case)

    token = item.edit.booking_links["320-03"].split("?t=")[1]
    assert [listing.booking_url for listing in case.listings] == [
        f"{synthetic.BASE}/320-03?t={token}",
        f"{synthetic.BASE}/412-01?t={token}",
    ]
    assert OWN_SHOWINGS_HEADING in text
    assert "- 412-01, Wednesday, April 14: 4:00 PM, 5:00 PM" in text
    assert case.own_showings == [
        own for own in item.edit.own_showings or [] if own.start > case.now
    ]


@pytest.mark.parametrize(
    "scenario_id", sorted(key for key, item in ITEMS.items() if item.good_reply)
)
def test_each_good_reply_keeps_every_exact_rule(scenario_id):
    item = ITEMS[scenario_id]
    case = case_for(item.scenario, synthetic.LISTINGS, item.edit)
    good = decision(
        reply_body=item.good_reply,
        offered_slots=[start.isoformat() for start in item.good_offered],
        lead={"unit": item.good_unit},
    )

    assert reply_problems(case, good) == []
    assert all(checks(case, good).values()), checks(case, good)

    # The same reply with the unit's page and no token, or another lead's token, is held.
    other = next(key for key in ITEMS if key != scenario_id)
    for wrong in (
        f"{synthetic.BASE}/320-03",
        ITEMS[other].edit.booking_links["320-03"],
    ):
        body = f"Hello,\n\nBook here: {wrong}" + synthetic.SIGNATURE
        assert reply_problems(case, decision(reply_body=body)), wrong


def test_a_time_open_at_the_other_unit_only_is_held():
    item = ITEMS["synthetic-join-0"]
    case = case_for(item.scenario, synthetic.LISTINGS, item.edit)
    five = synthetic._at(14, 17)  # open at 412-01 only
    reply = decision(lead={"unit": "320-03"}, offered_slots=[five.isoformat()])
    assert reply_problems(case, reply) == [
        "The reply offers a time outside the supplied availability."
    ]


def test_the_written_files_rebuild_the_same_cases(tmp_path):
    synthetic.write(tmp_path)

    scenarios = read_scenarios(tmp_path / "scenarios.jsonl")
    edits = read_case_edits(tmp_path / "case-edits.json")
    listings = ListingData.model_validate_json((tmp_path / "listings.json").read_text())
    expected = json.loads((tmp_path / "expected.json").read_text())

    assert [item.scenario_id for item in scenarios] == list(ITEMS)
    assert set(expected) == set(ITEMS)
    for scenario in scenarios:
        item = ITEMS[scenario.scenario_id]
        edit = edits[scenario.scenario_id]
        assert edit_fingerprint(edit) == edit_fingerprint(item.edit)
        assert render_case(case_for(scenario, listings, edit)) == render_case(
            case_for(item.scenario, synthetic.LISTINGS, item.edit)
        )


def test_an_edit_without_booking_facts_keeps_its_old_fingerprint():
    edit = CaseEdit.model_validate(
        {
            "offered": ["2026-05-07T15:00:00-04:00"],
            "booked": [{"unit": "12-A", "start": "2026-05-08T10:00:00-04:00"}],
            "note": "From the thread.",
        }
    )
    # The fingerprint as it was computed before the booking facts existed.
    facts = {
        "offered": sorted(start.astimezone(UTC).isoformat() for start in edit.offered),
        "booked": sorted(
            [tour.unit, tour.start.astimezone(UTC).isoformat()] for tour in edit.booked
        ),
    }
    old = hashlib.sha256(json.dumps(facts).encode()).hexdigest()[:12]
    assert edit_fingerprint(edit) == old
    assert edit_fingerprint(ITEMS["synthetic-join-0"].edit) != edit_fingerprint(
        ITEMS["synthetic-join-0"].edit.model_copy(update={"booking_links": {}})
    )
