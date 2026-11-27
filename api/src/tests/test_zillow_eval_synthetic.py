"""The made-up booking and ad description scenarios, checked without a model.

Each booking scenario builds a case with per-lead links, the lead's own
showings and per-unit times. Each description scenario builds a case with the
units' Zillow ad descriptions. Every good reply keeps every exact rule (the
eval's checks and the automatic-send check), and the files written for the
``run`` command rebuild the same cases. Running them through the model is not
done here.
"""

import hashlib
import json
from datetime import UTC

import pytest
from pydantic_evals.evaluators import EvaluatorContext
from pydantic_evals.otel.span_tree import SpanTree

from api.src.tests.test_zillow_lead_agent import decision
from api.src.zillow_leads.auto_reply import reply_problems
from api.src.zillow_leads.case import (
    DESCRIPTION_LABEL,
    OWN_SHOWINGS_HEADING,
    ListingData,
    render_case,
)
from api.src.zillow_leads.evals import synthetic
from api.src.zillow_leads.evals.checks import (
    DETAILS_LINE,
    ExpectedOutcome,
    ReplyChecks,
    booking_change,
    build_dataset,
)
from api.src.zillow_leads.evals.scenarios import (
    CaseEdit,
    case_for,
    edit_fingerprint,
    read_case_edits,
    read_scenarios,
)

ITEMS = {item.scenario.scenario_id: item for item in synthetic.scenarios()}


def checks(case, outcome, metadata: dict | None = None, evaluator=None) -> dict[str, bool]:
    ctx = EvaluatorContext(
        name=case.case_id,
        inputs=case,
        metadata=metadata or {},
        expected_output=None,
        output=outcome,
        duration=0.0,
        _span_tree=SpanTree(),
        attributes={},
        metrics={},
    )
    return (evaluator or ReplyChecks()).evaluate(ctx)


def expected_checks(case, outcome, item) -> dict[str, bool]:
    """``ExpectedOutcome`` with the scenario's entry of ``expected.json``."""
    entry = json.loads(
        json.dumps(
            {
                "expected": item.expected,
                **({"booking": item.booking.model_dump(mode="json")} if item.booking else {}),
                **({"details_line": item.details_line} if item.details_line is not None else {}),
            }
        )
    )
    return checks(case, outcome, {"expected": entry}, ExpectedOutcome())


# Every scenario but the one built as production builds it with the switch off.
WITH_SERVICE = sorted(key for key, item in ITEMS.items() if item.edit.own_showings is not None)
WITHOUT_SERVICE = "synthetic-without-booking-service-0"


@pytest.mark.parametrize("scenario_id", WITH_SERVICE)
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
        tour=item.good_tour,
    )

    assert reply_problems(case, good) == []
    assert all(checks(case, good).values()), checks(case, good)
    # And it is what the scenario expects, as ``run --expected`` checks it.
    expected = expected_checks(case, good, item)
    assert expected and all(expected.values()), expected

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


def test_the_case_without_the_booking_service_is_built_as_production_builds_it():
    item = ITEMS[WITHOUT_SERVICE]
    case = case_for(item.scenario, synthetic.LISTINGS, item.edit)
    text = render_case(case)

    assert case.own_showings is None
    assert OWN_SHOWINGS_HEADING not in text
    assert "## Booking links\n- (none)" in text
    assert all(slot.unit is None for slot in case.offerable_slots)
    assert "- Wednesday, April 14: 4:00 PM" in text


def test_a_reply_that_would_book_must_state_the_time_and_carry_the_details_line():
    item = ITEMS["synthetic-confirm-by-email-0"]
    case = case_for(item.scenario, synthetic.LISTINGS, item.edit)
    link = item.edit.booking_links["320-03"]
    tour = item.good_tour
    unit = {"unit": "320-03"}

    good = checks(case, decision(reply_body=item.good_reply, lead=unit, tour=tour))
    assert good["states_tour_time"] and good["details_line"] and good["tour_time_allowed"]

    other_time = item.good_reply.replace("4:00 PM", "5:00 PM")
    assert not checks(case, decision(reply_body=other_time, lead=unit, tour=tour))[
        "states_tour_time"
    ]
    no_line = "Hello,\n\nSee you Wednesday, April 14 at 4:00 PM." + synthetic.SIGNATURE
    assert not checks(case, decision(reply_body=no_line, lead=unit, tour=tour))["details_line"]
    other_link = item.good_reply.replace(
        link, ITEMS["synthetic-move-2"].edit.booking_links["320-03"]
    )
    assert not checks(case, decision(reply_body=other_link, lead=unit, tour=tour))["details_line"]
    # A time not open at the unit: 5:00 on Wednesday is open at 412-01 only.
    late = {"status": "confirmed", "start": synthetic._at(14, 17).isoformat()}
    assert not checks(case, decision(reply_body=other_time, lead=unit, tour=late))[
        "tour_time_allowed"
    ]


def test_the_booking_checks_are_absent_when_nothing_would_be_booked():
    for scenario_id in ("synthetic-own-parking-2", WITHOUT_SERVICE):
        item = ITEMS[scenario_id]
        case = case_for(item.scenario, synthetic.LISTINGS, item.edit)
        found = checks(
            case,
            decision(reply_body=item.good_reply, lead={"unit": "320-03"}, tour=item.good_tour),
        )
        assert not {"states_tour_time", "details_line", "tour_time_allowed"} & set(found)


def test_expected_outcome_reports_a_wrong_action_booking_or_details_line():
    item = ITEMS["synthetic-cancel-2"]
    case = case_for(item.scenario, synthetic.LISTINGS, item.edit)

    quiet = decision(action="no_reply", lead={"unit": "320-03"}, tour={"status": "confirmed"})
    assert expected_checks(case, quiet, item) == {
        "expected_action": False,
        "expected_booking": False,
        "expected_details_line": True,
    }
    # A no_reply recording the cancellation still cancels at once.
    cancels = decision(action="no_reply", lead={"unit": "320-03"}, tour={"status": "cancelled"})
    assert expected_checks(case, cancels, item)["expected_booking"]


def test_a_run_without_expectations_gets_no_expected_checks():
    item = ITEMS["synthetic-cancel-2"]
    case = case_for(item.scenario, synthetic.LISTINGS, item.edit)
    assert checks(case, decision(), {}, ExpectedOutcome()) == {}


def test_each_thread_subject_names_the_unit_the_scenario_is_about():
    by_unit = {listing.unit: listing.address for listing in synthetic.LISTINGS.listings}
    for item in ITEMS.values():
        unit = item.good_unit or "320-03"
        subjects = {message.subject for message in item.scenario.thread.messages}
        assert subjects == {f"{by_unit[unit]} inquiry"}, item.scenario.scenario_id


def test_a_reply_confirming_a_virtual_tour_must_not_carry_the_details_line():
    item = ITEMS["synthetic-virtual-confirmed-0"]
    case = case_for(item.scenario, synthetic.LISTINGS, item.edit)
    unit = {"unit": "412-01"}
    good = decision(reply_body=item.good_reply, lead=unit, tour=item.good_tour)
    line = "To get a text reminder and a calendar invite, add your details here: "
    bad_body = item.good_reply.removesuffix(synthetic.SIGNATURE) + (
        f"\n\n{line}{item.edit.booking_links['412-01']}" + synthetic.SIGNATURE
    )
    bad = decision(reply_body=bad_body, lead=unit, tour=item.good_tour)

    assert checks(case, good)["details_line"] is True
    assert checks(case, bad)["details_line"] is False
    # Nothing is booked for a virtual tour, so the booking checks do not apply.
    assert "states_tour_time" not in checks(case, good)


def test_restating_an_own_showing_needs_the_details_line_only_while_details_are_missing():
    line = "To get a text reminder and a calendar invite, add your details here: "
    unit = {"unit": "320-03"}
    added = ITEMS["synthetic-reconfirm-details-added-2"]
    pending = ITEMS["synthetic-reconfirm-provisional-2"]
    for item, needs_line in ((added, False), (pending, True)):
        case = case_for(item.scenario, synthetic.LISTINGS, item.edit)
        link = item.edit.booking_links["320-03"]
        plain = item.good_reply.removesuffix(synthetic.SIGNATURE).split("\n\nTo get")[0]
        with_line = plain + f"\n\n{line}{link}" + synthetic.SIGNATURE
        without = plain + synthetic.SIGNATURE
        tour = item.good_tour
        found = {
            body: checks(case, decision(reply_body=body, lead=unit, tour=tour))["details_line"]
            for body in (with_line, without)
        }
        assert found == {with_line: needs_line, without: not needs_line}, item.scenario
        # Nothing is booked or moved, so the time and booking checks do not apply.
        assert "states_tour_time" not in checks(
            case, decision(reply_body=with_line, lead=unit, tour=tour)
        )


@pytest.mark.parametrize(
    ("scenario_id", "needs_line"),
    [("synthetic-move-2", False), ("synthetic-move-provisional-2", True)],
)
def test_a_move_needs_the_details_line_only_when_the_moved_showing_has_no_details(
    scenario_id, needs_line
):
    item = ITEMS[scenario_id]
    case = case_for(item.scenario, synthetic.LISTINGS, item.edit)
    plain = item.good_reply.removesuffix(synthetic.SIGNATURE).split("\n\nTo get")[0]
    with_line = f"{plain}\n\n{DETAILS_LINE} {item.edit.booking_links['320-03']}"
    for body, has_line in ((with_line, True), (plain, False)):
        reply = decision(
            reply_body=body + synthetic.SIGNATURE, lead={"unit": "320-03"}, tour=item.good_tour
        )
        assert booking_change(case, reply).action == "move"
        assert checks(case, reply)["details_line"] is (has_line == needs_line)
        assert expected_checks(case, reply, item)["expected_details_line"] is (
            has_line == needs_line
        )


def test_details_at_another_unit_or_on_a_cancelled_showing_do_not_excuse_the_line():
    """Only a booked place the reply moves counts, and a new booking moves nothing."""
    item = ITEMS["synthetic-move-provisional-2"]
    case = case_for(item.scenario, synthetic.LISTINGS, item.edit)
    added = case.own_showings[0].model_copy(update={"details_added": True})
    elsewhere = added.model_copy(update={"unit": "412-01"})
    cancelled = added.model_copy(update={"status": "cancelled", "cancelled_by": "prospect"})
    bare = decision(
        reply_body=item.good_reply.split("\n\nTo get")[0] + synthetic.SIGNATURE,
        lead={"unit": "320-03"},
        tour=item.good_tour,
    )
    for own, action in (
        ([*case.own_showings, elsewhere], "move"),
        ([*case.own_showings, cancelled], "move"),
        ([elsewhere], "book"),
        ([cancelled], "book"),
        ([], "book"),
    ):
        changed = case.model_copy(update={"own_showings": own})
        assert booking_change(changed, bare).action == action
        assert checks(changed, bare)["details_line"] is False, own


def test_no_case_offers_a_lead_the_showing_they_are_in():
    """As ``build_case`` does for a live case; times an edit adds come after it."""
    for item in ITEMS.values():
        case = case_for(item.scenario, synthetic.LISTINGS, item.edit)
        mine = {(own.unit, own.start) for own in case.own_showings or [] if own.status == "booked"}
        offered = {(slot.unit, slot.start) for slot in case.offerable_slots}
        booked = {(tour.unit, tour.start) for tour in [*case.booked_tours, *item.edit.booked]}
        assert not mine & (offered | booked), item.scenario.scenario_id


@pytest.mark.asyncio
async def test_a_run_with_expected_reports_the_checks_for_each_good_decision(tmp_path):
    """The ``run --expected`` path, with each scenario's good decision in place of a model."""
    synthetic.write(tmp_path)
    scenarios = read_scenarios(tmp_path / "scenarios.jsonl")
    edits = read_case_edits(tmp_path / "case-edits.json")
    listings = ListingData.model_validate_json((tmp_path / "listings.json").read_text())
    expected = json.loads((tmp_path / "expected.json").read_text())
    by_case = {key: item for key, item in ITEMS.items() if item.good_reply}
    picked = [scenario for scenario in scenarios if scenario.scenario_id in by_case]

    async def good(case):
        item = by_case[case.case_id]
        return decision(
            reply_body=item.good_reply,
            offered_slots=[start.isoformat() for start in item.good_offered],
            lead={"unit": item.good_unit},
            tour=item.good_tour,
        )

    report = await build_dataset(picked, listings, edits, expected).evaluate(good, progress=False)

    assert len(report.cases) == len(picked) and not report.failures
    for case in report.cases:
        assert "expected_action" in case.assertions, case.name
        assert all(result.value for result in case.assertions.values()), case.name
    moved = next(case for case in report.cases if case.name == "synthetic-move-2")
    assert {"states_tour_time", "details_line", "expected_booking"} <= set(moved.assertions)


# The made-up scenarios for a unit's Zillow ad description.
DESCRIBED = {item.scenario.scenario_id: item for item in synthetic.description_scenarios()}


@pytest.mark.parametrize("scenario_id", sorted(DESCRIBED))
def test_each_description_case_carries_the_ad_as_production_builds_it(scenario_id):
    item = DESCRIBED[scenario_id]
    case = case_for(item.scenario, synthetic.DESCRIBED, item.edit)
    text = render_case(case)

    assert text.count(f"  - {DESCRIPTION_LABEL}") == 2
    assert "    Available May 15 for $1,450/month. Virtual tours available." in text
    assert "    Street parking only, no driveway access." in text
    # Built as production builds it with the booking service off.
    assert case.own_showings is None and OWN_SHOWINGS_HEADING not in text


@pytest.mark.parametrize(
    "scenario_id", sorted(key for key, item in DESCRIBED.items() if item.good_reply)
)
def test_each_good_description_reply_keeps_every_exact_rule(scenario_id):
    item = DESCRIBED[scenario_id]
    case = case_for(item.scenario, synthetic.DESCRIBED, item.edit)
    good = decision(reply_body=item.good_reply, lead={"unit": item.good_unit})

    assert reply_problems(case, good) == []
    assert all(checks(case, good).values()), checks(case, good)
    expected = expected_checks(case, good, item)
    assert expected and all(expected.values()), expected
    # The ad's out-of-date terms never reach a reply.
    assert not [word for word in ("$1,450", "May 15", "In-unit") if word in item.good_reply]


def test_the_description_rules_each_have_a_scenario():
    assert {key: item.expected for key, item in DESCRIBED.items()} == {
        "synthetic-description-answer-0": "reply",
        "synthetic-description-terms-0": "reply",
        "synthetic-description-virtual-0": "reply",
        "synthetic-description-correction-0": "reply",
        "synthetic-description-conflict-0": "escalate",
    }


def test_the_written_description_files_rebuild_the_same_cases(tmp_path):
    synthetic.write(tmp_path, "description")

    scenarios = read_scenarios(tmp_path / "scenarios.jsonl")
    edits = read_case_edits(tmp_path / "case-edits.json")
    listings = ListingData.model_validate_json((tmp_path / "listings.json").read_text())
    expected = json.loads((tmp_path / "expected.json").read_text())

    assert [item.scenario_id for item in scenarios] == list(DESCRIBED)
    assert {key: entry["expected"] for key, entry in expected.items()} == {
        key: item.expected for key, item in DESCRIBED.items()
    }
    for scenario in scenarios:
        item = DESCRIBED[scenario.scenario_id]
        assert render_case(case_for(scenario, listings, edits[scenario.scenario_id])) == (
            render_case(case_for(item.scenario, synthetic.DESCRIBED, item.edit))
        )
