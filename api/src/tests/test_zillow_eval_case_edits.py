"""Hand-set tour times in an eval case, and rerunning only the edited cases (no network)."""

import json
import re
from datetime import UTC, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from api.src.sernia_ai.zillow_leads.case import (
    AgentAvailability,
    BookedTour,
    Listing,
    ListingData,
    render_case,
)
from api.src.sernia_ai.zillow_leads.evals.checks import build_dataset
from api.src.sernia_ai.zillow_leads.evals.cli import carry_over, in_run
from api.src.sernia_ai.zillow_leads.evals.pages import review_page
from api.src.sernia_ai.zillow_leads.evals.scenarios import (
    CaseEdit,
    Scenario,
    case_for,
    edit_fingerprint,
    edit_run_with,
    read_case_edits,
)
from api.src.sernia_ai.zillow_leads.thread import LeadMessage, LeadThread

ET = ZoneInfo("America/New_York")
MONDAY_9AM = datetime(2026, 5, 4, 9, 0, tzinfo=ET)
# Not in the weekly pattern below: the team offered it in the thread that week.
THURSDAY_7PM = datetime(2026, 5, 7, 19, 0, tzinfo=ET)
PATTERN_TIME = datetime(2026, 5, 6, 10, 0, tzinfo=ET)

LISTING_DATA = ListingData(
    listings=[Listing(unit="12-A", address="12 Example St", rent=1500)],
    availability=[AgentAvailability(agent="Alex Agent", weekday_times=[time(10, 0)])],
)


def _message(minutes: int, *, sernia: bool, body: str) -> LeadMessage:
    return LeadMessage(
        message_id=f"m{minutes}",
        rfc_message_id=f"<m{minutes}@x>",
        sent_at=MONDAY_9AM.astimezone(UTC) + timedelta(minutes=minutes),
        from_address="all@serniacapital.com" if sernia else "lead@convo.zillow.com",
        reply_to=None,
        from_sernia=sernia,
        subject="12 Example St inquiry",
        body=body,
    )


SCENARIO = Scenario(
    thread=LeadThread(
        mailbox="all@serniacapital.com",
        thread_id="T",
        messages=[
            _message(0, sernia=False, body="Can I see it this week?"),
            _message(10, sernia=True, body="We can show it Thursday at 7."),
            _message(20, sernia=False, body="Thursday at 7 works."),
        ],
    )
)
EDIT = CaseEdit(offered=[THURSDAY_7PM], note="Our second message offered Thursday at 7 pm.")


def _decision(action: str = "escalate") -> dict:
    return {
        "action": action,
        "reason": "r",
        "question": "Can we honor Thursday at 7?",
        "lead": {},
        "tour": {"status": "none"},
        "team_notes": [],
    }


class TestCaseEdit:
    def test_without_an_edit_the_case_has_only_the_weekly_pattern(self):
        case = case_for(SCENARIO, LISTING_DATA)

        assert THURSDAY_7PM not in {slot.start for slot in case.offerable_slots}
        assert "7:00 PM" not in render_case(case)

    def test_an_offered_time_joins_the_times_that_may_be_offered(self):
        case = case_for(SCENARIO, LISTING_DATA, EDIT)
        text = render_case(case)

        assert THURSDAY_7PM in {slot.start for slot in case.offerable_slots}
        offered = text.split("## Tour times that may be offered")[1].split("##")[0]
        assert "- Thursday, May 7: 10:00 AM, 7:00 PM" in offered
        # The pattern's own times are still there, in order.
        starts = [slot.start for slot in case.offerable_slots]
        assert starts == sorted(starts) and len(starts) > 1

    def test_a_time_the_pattern_already_has_is_not_listed_twice(self):
        case = case_for(SCENARIO, LISTING_DATA, CaseEdit(offered=[PATTERN_TIME]))

        assert [slot.start for slot in case.offerable_slots].count(PATTERN_TIME) == 1

    def test_a_booked_tour_is_listed_as_booked(self):
        edit = CaseEdit(booked=[BookedTour(unit="12-A", start=THURSDAY_7PM)])

        text = render_case(case_for(SCENARIO, LISTING_DATA, edit))

        assert "- 12-A: Thursday, May 7 at 7:00 PM" in text.split("## Tours already booked")[1]

    def test_an_edit_never_names_a_person(self):
        case = case_for(SCENARIO, LISTING_DATA, EDIT)

        assert [slot.agent for slot in case.offerable_slots if slot.start == THURSDAY_7PM] == [""]

    def test_the_dataset_gives_the_model_the_edited_case(self):
        dataset = build_dataset([SCENARIO], LISTING_DATA, {SCENARIO.scenario_id: EDIT})

        assert THURSDAY_7PM in {slot.start for slot in dataset.cases[0].inputs.offerable_slots}

    def test_edits_are_read_by_scenario_and_need_a_utc_offset(self, tmp_path: Path):
        path = tmp_path / "case-edits.json"
        path.write_text(
            json.dumps({"T-2": {"offered": ["2026-05-07T19:00:00-04:00"], "note": "why"}})
        )

        edits = read_case_edits(path)

        assert edits["T-2"].offered == [THURSDAY_7PM]
        assert edits["T-2"].note == "why"


class TestCarryOver:
    def test_only_a_case_with_a_new_edit_is_run_again(self):
        earlier = [
            {"case_id": "A", "decision": _decision()},
            {"case_id": "B", "decision": _decision()},
            {
                "case_id": "C",
                "decision": _decision(),
                "case_edit": True,
                "case_edit_hash": edit_fingerprint(EDIT),
            },
        ]
        edits = {"B": EDIT, "C": EDIT}

        again, kept = carry_over(earlier, edits)

        assert again == ["B"]
        assert list(kept) == ["A", "C"]  # C was already run with this edit

    def test_a_case_whose_edit_changed_or_was_removed_is_run_again(self):
        ran_with = {"case_edit": True, "case_edit_hash": edit_fingerprint(EDIT)}
        earlier = [
            {"case_id": "changed", "decision": _decision(), **ran_with},
            {"case_id": "removed", "decision": _decision(), **ran_with},
            {"case_id": "same", "decision": _decision(), **ran_with},
        ]
        other = CaseEdit(booked=[BookedTour(unit="12-A", start=THURSDAY_7PM)])

        again, kept = carry_over(earlier, {"changed": other, "same": EDIT})

        assert again == ["changed", "removed"]
        assert list(kept) == ["same"]

    def test_a_row_edited_before_fingerprints_were_stored_is_run_again(self):
        earlier = [{"case_id": "old", "decision": _decision(), "case_edit": True}]

        again, _ = carry_over(earlier, {"old": EDIT})

        assert again == ["old"]  # nothing says which edit it was run with


class TestEditFingerprint:
    def test_it_follows_the_tour_facts_and_not_the_note(self):
        reworded = EDIT.model_copy(update={"note": "Same time, explained differently."})
        same_instant = CaseEdit(offered=[THURSDAY_7PM.astimezone(UTC)])
        moved = CaseEdit(offered=[THURSDAY_7PM + timedelta(hours=1)])

        assert edit_fingerprint(reworded) == edit_fingerprint(EDIT)
        assert edit_fingerprint(same_instant) == edit_fingerprint(EDIT)
        assert edit_fingerprint(moved) != edit_fingerprint(EDIT)
        assert edit_fingerprint(None) == ""

    def test_a_stored_row_is_rebuilt_only_with_the_edit_it_was_run_with(self):
        row = {"case_id": "T", "case_edit": True, "case_edit_hash": edit_fingerprint(EDIT)}
        moved = CaseEdit(offered=[THURSDAY_7PM + timedelta(hours=1)])

        assert edit_run_with(row, {"T": EDIT}) == EDIT
        assert edit_run_with({"case_id": "T"}, {"T": EDIT}) is None  # run before the edit
        with pytest.raises(ValueError, match="different in"):
            edit_run_with(row, {"T": moved})
        with pytest.raises(ValueError, match="missing from"):
            edit_run_with(row, {})


class TestReviewPage:
    def _card(self, row: dict) -> dict:
        page = review_page("x", [SCENARIO], LISTING_DATA, [row], edits={SCENARIO.scenario_id: EDIT})
        embedded = re.search(
            r'<script type="application/json" id="data">(.*?)</script>', page, re.S
        )
        assert embedded is not None
        return json.loads(embedded.group(1))["cases"][0]

    def test_a_row_run_with_its_edit_shows_the_edited_case_and_says_so(self):
        card = self._card(
            {"case_id": SCENARIO.scenario_id, "decision": _decision(), "case_edit": True}
        )

        assert "7:00 PM" in card["case_text"]
        assert card["case_edit"] == {
            "offered": ["Thu May 7, 7:00 PM"],
            "booked": [],
            "note": "Our second message offered Thursday at 7 pm.",
        }

    def test_a_row_run_before_the_edit_shows_the_case_it_was_run_with(self):
        card = self._card({"case_id": SCENARIO.scenario_id, "decision": _decision()})

        assert "7:00 PM" not in card["case_text"]
        assert card["case_edit"] is None


class TestSameCasesAgain:
    def test_the_cases_of_an_earlier_run_are_picked_in_its_order(self, tmp_path: Path):
        other = SCENARIO.model_copy(
            update={"thread": SCENARIO.thread.model_copy(update={"thread_id": "U"})}
        )
        unused = SCENARIO.model_copy(
            update={"thread": SCENARIO.thread.model_copy(update={"thread_id": "V"})}
        )
        run_file = tmp_path / "earlier.jsonl"
        run_file.write_text(
            "".join(
                json.dumps({"case_id": case_id, "decision": _decision()}) + "\n"
                for case_id in (other.scenario_id, SCENARIO.scenario_id, "gone-0")
            )
        )

        picked = in_run([SCENARIO, unused, other], run_file)

        assert [item.scenario_id for item in picked] == [other.scenario_id, SCENARIO.scenario_id]
