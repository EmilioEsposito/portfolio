"""The Zillow lead case, decision contract and exact-answer checks (no network)."""

import json
import re
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError
from pydantic_ai.models.test import TestModel
from pydantic_evals.evaluators import EvaluatorContext
from pydantic_evals.otel.span_tree import SpanTree

from api.src.sernia_ai.zillow_leads import decision as decision_module
from api.src.sernia_ai.zillow_leads.case import (
    AgentAvailability,
    BookedTour,
    LeadCase,
    Listing,
    OperatingData,
    TourSlot,
    build_case,
    decision_points,
    offerable_slots,
    render_case,
)
from api.src.sernia_ai.zillow_leads.decision import LeadDecision
from api.src.sernia_ai.zillow_leads.evals import (
    Label,
    ReplyChecks,
    build_dataset,
    case_for,
    review_page,
    scenarios_from_thread,
)
from api.src.sernia_ai.zillow_leads.thread import LeadMessage, LeadThread

ET = ZoneInfo("America/New_York")
MONDAY_9AM = datetime(2026, 5, 4, 9, 0, tzinfo=ET)

OPERATING = OperatingData(
    listings=[
        Listing(
            unit="12-A",
            address="12 Example St",
            rent=1500,
            lease_start_earliest=date(2026, 7, 1),
            lease_start_latest=date(2026, 8, 1),
        )
    ],
    rented_units=["12-B"],
    availability=[
        AgentAvailability(
            agent="Alex Agent",
            weekday_times=[time(8, 0), time(10, 0), time(18, 0), time(18, 30)],
            weekend_times=[time(12, 0)],
        ),
        AgentAvailability(agent="Blair Agent", weekday_times=[time(10, 0)]),
    ],
    blackout_dates=[date(2026, 5, 10)],  # the Sunday
)


def message(minutes: int, *, sernia: bool = False, body: str = "Hi") -> LeadMessage:
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


def thread(*messages: LeadMessage) -> LeadThread:
    return LeadThread(mailbox="all@serniacapital.com", thread_id="T", messages=list(messages))


def starts(slots) -> list[datetime]:
    return [slot.start for slot in slots]


class TestOfferableSlots:
    def test_nothing_inside_the_24_hour_notice_period(self):
        slots = offerable_slots(OPERATING, MONDAY_9AM)
        assert min(starts(slots)) == datetime(2026, 5, 5, 10, 0, tzinfo=ET)

    def test_a_slot_exactly_24_hours_out_is_allowed(self):
        slots = offerable_slots(OPERATING, datetime(2026, 5, 4, 10, 0, tzinfo=ET))
        assert datetime(2026, 5, 5, 10, 0, tzinfo=ET) in starts(slots)
        just_late = offerable_slots(OPERATING, datetime(2026, 5, 4, 10, 1, tzinfo=ET))
        assert datetime(2026, 5, 5, 10, 0, tzinfo=ET) not in starts(just_late)

    def test_only_showing_hours(self):
        # 8:00 is before showings start; 18:30 would end after they stop.
        clock_times = {slot.start.time() for slot in offerable_slots(OPERATING, MONDAY_9AM)}
        assert clock_times == {time(10, 0), time(12, 0), time(18, 0)}

    def test_blackout_date_is_skipped_but_the_other_weekend_day_is_not(self):
        days = {slot.start.date() for slot in offerable_slots(OPERATING, MONDAY_9AM)}
        assert date(2026, 5, 9) in days  # Saturday
        assert date(2026, 5, 10) not in days  # Sunday, blacked out

    def test_weekday_and_weekend_patterns_do_not_mix(self):
        for slot in offerable_slots(OPERATING, MONDAY_9AM):
            weekend = slot.start.weekday() >= 5
            assert (slot.start.time() == time(12, 0)) == weekend

    def test_the_clock_is_read_in_local_time(self):
        # 23:30 UTC on Monday is 19:30 Monday in New York, not Tuesday.
        slots = offerable_slots(OPERATING, datetime(2026, 5, 4, 23, 30, tzinfo=UTC))
        assert min(starts(slots)) == datetime(2026, 5, 6, 10, 0, tzinfo=ET)


class TestDecisionPoints:
    def test_each_burst_of_lead_messages_is_one_decision(self):
        t = thread(message(0), message(1), message(30, sernia=True), message(60), message(61))
        assert decision_points(t) == [1, 4]

    def test_a_thread_we_answered_last_owes_nothing_new(self):
        t = thread(message(0), message(30, sernia=True))
        assert decision_points(t) == [0]


class TestBuildCase:
    def test_historical_case_sees_only_what_was_knowable_then(self):
        t = thread(message(0, body="Is it available?"), message(30, sernia=True, body="Yes"))
        booked = [
            BookedTour(unit="12-A", start=MONDAY_9AM - timedelta(days=1)),
            BookedTour(unit="12-A", start=MONDAY_9AM + timedelta(days=2)),
        ]

        case = build_case(t, OPERATING, upto=0, booked_tours=booked)

        assert [m.body for m in case.thread.messages] == ["Is it available?"]
        assert case.now == t.messages[0].sent_at + timedelta(minutes=5)
        assert case.case_id == "T-0"
        assert [tour.start for tour in case.booked_tours] == [MONDAY_9AM + timedelta(days=2)]
        assert min(starts(case.offerable_slots)) == datetime(2026, 5, 5, 10, 0, tzinfo=ET)
        assert len(t.messages) == 2  # the source thread is not modified

    def test_a_unit_leaves_the_listings_on_the_day_it_is_rented(self):
        operating = OPERATING.model_copy(
            update={
                "listings": [
                    Listing(unit="12-A", lease_start_earliest=date(2026, 7, 1)),
                    Listing(unit="12-C", rented_on=date(2026, 5, 4)),
                    Listing(unit="12-D", rented_on=date(2026, 5, 5)),
                ]
            }
        )

        case = build_case(thread(message(0)), operating, upto=0)  # Monday May 4

        assert [item.unit for item in case.listings] == ["12-A", "12-D"]
        assert case.rented_units == ["12-B", "12-C"]
        assert "12-A: lease starts July 1, 2026" in render_case(case)

    def test_rendered_case_never_names_staff_and_lists_a_shared_time_once(self):
        case = build_case(thread(message(0, body="Can I tour Tuesday?")), OPERATING, upto=0)

        text = render_case(case)

        assert "Alex" not in text and "Blair" not in text
        tuesday = next(line for line in text.splitlines() if line.startswith("- Tuesday, May 5"))
        assert tuesday == "- Tuesday, May 5: 10:00 AM, 6:00 PM"  # two people free at 10:00
        assert "Current time: Monday, May 4, 2026 at 9:05 AM" in text
        assert (
            "12-A: 12 Example St; $1,500/month; lease can start July 1 through August 1, 2026"
            in text
        )
        assert "Rented, do not offer: 12-B" in text
        assert "### Message 1: Lead, Monday, May 4 at 9:00 AM" in text
        assert "Can I tour Tuesday?" in text


def decision(**overrides) -> LeadDecision:
    fields = {
        "lead": {},
        "tour": {},
        "action": "reply",
        "reason": "New lead.",
        "reply_body": "Hello,\n\nSernia Capital Team",
    }
    return LeadDecision.model_validate({**fields, **overrides})


class TestDecisionContract:
    def test_a_reply_must_carry_its_text(self):
        with pytest.raises(ValidationError, match="needs reply_body"):
            decision(reply_body=None)

    def test_staying_quiet_cannot_carry_a_draft(self):
        with pytest.raises(ValidationError, match="must not carry reply_body"):
            decision(action="no_reply")

    def test_escalating_must_say_what_is_needed(self):
        with pytest.raises(ValidationError, match="needs question"):
            decision(action="escalate", reply_body=None)
        assert decision(action="escalate", reply_body=None, question="Is 12-C listed?")

    @pytest.mark.asyncio
    async def test_decide_sends_the_policy_and_the_rendered_case(self):
        case = build_case(thread(message(0, body="Is it available?")), OPERATING, upto=0)
        model = TestModel(
            custom_output_args={
                "lead": {},
                "tour": {},
                "action": "no_reply",
                "reason": "Test.",
            }
        )

        with decision_module.decider().override(model=model):
            result = await decision_module.decide(case)

        assert result.action == "no_reply"
        params = model.last_model_request_parameters
        assert params is not None and params.function_tools == []  # no tools, by design
        assert decision_module.decider()._instructions == [decision_module.load_policy()]


def check(case: LeadCase, output: LeadDecision) -> dict[str, bool]:
    ctx = EvaluatorContext(
        name=case.case_id,
        inputs=case,
        metadata={},
        expected_output=None,
        output=output,
        duration=0.0,
        _span_tree=SpanTree(),
        attributes={},
        metrics={},
    )
    return ReplyChecks().evaluate(ctx)


class TestReplyChecks:
    case = build_case(thread(message(0)), OPERATING, upto=0)
    allowed = datetime(2026, 5, 5, 10, 0, tzinfo=ET)

    def test_a_clean_reply_passes_every_check(self):
        # Same instant written with a different offset still counts as allowed.
        result = check(self.case, decision(offered_slots=[self.allowed.astimezone(UTC)]))
        assert result == {
            "offered_slots_allowed": True,
            "no_em_dash": True,
            "has_signature": True,
            "no_staff_names": True,
        }

    def test_offering_a_time_that_is_not_allowed_fails(self):
        inside_notice = datetime(2026, 5, 4, 10, 0, tzinfo=ET)
        assert not check(self.case, decision(offered_slots=[inside_notice]))[
            "offered_slots_allowed"
        ]
        naive = datetime(2026, 5, 5, 10, 0)
        assert not check(self.case, decision(offered_slots=[naive]))["offered_slots_allowed"]

    def test_style_rules(self):
        result = check(self.case, decision(reply_body="Hi — Alex will show you around."))
        assert result["no_em_dash"] is False
        assert result["has_signature"] is False
        assert result["no_staff_names"] is False

    def test_no_reply_is_not_scored(self):
        assert check(self.case, decision(action="no_reply", reply_body=None)) == {}


class TestScenarios:
    past = thread(
        message(0, body="Is it available?"),
        message(45, sernia=True, body="Yes, tours Tuesday."),
        message(90, body="Thanks"),
    )

    def test_each_scenario_records_what_was_really_sent_next(self):
        first, second = scenarios_from_thread(self.past)

        assert (first.scenario_id, second.scenario_id) == ("T-0", "T-2")
        assert [m.body for m in first.thread.messages] == ["Is it available?"]
        assert first.actual_reply == "Yes, tours Tuesday."
        assert first.actual_reply_delay_minutes == 45
        assert second.actual_reply is None and second.actual_reply_delay_minutes is None
        # What we really sent is never part of what the model reads.
        assert "tours Tuesday" not in render_case(case_for(first, OPERATING))

    def test_a_scenario_holds_the_conversation_and_nothing_about_the_business(self):
        first = scenarios_from_thread(self.past)[0]
        assert set(first.model_dump()) == {
            "thread",
            "actual_reply",
            "actual_reply_delay_minutes",
        }

    def test_the_same_scenario_runs_against_changed_operating_data(self):
        first = scenarios_from_thread(self.past)[0]
        richer = OPERATING.model_copy(
            update={
                "listings": [Listing(unit="12-A", facts="Second floor\n- Dishwasher")],
                "standard_answers": "Apply at example.com after a tour.",
            }
        )

        before, after = case_for(first, OPERATING), case_for(first, richer)

        assert before.case_id == after.case_id == first.scenario_id
        assert before.now == after.now and before.thread == after.thread
        text = render_case(after)
        assert "  - Second floor\n  - Dishwasher" in text
        assert "## Standard answers\n- Apply at example.com after a tour." in text
        assert "## Standard answers\n- (none on file)" in render_case(before)
        dataset = build_dataset([first], richer)
        assert [case.name for case in dataset.cases] == ["T-0"]


class TestSlotsFromElsewhere:
    def test_a_calendar_or_booking_service_can_supply_the_slots(self):
        thursday = datetime(2026, 5, 7, 15, 0, tzinfo=ET)
        case = build_case(
            thread(message(0)),
            OPERATING,
            upto=0,
            slots=[TourSlot(start=thursday, agent="Casey Agent")],
        )

        assert starts(case.offerable_slots) == [thursday]
        assert "- Thursday, May 7: 3:00 PM" in render_case(case)
        assert check(case, decision(offered_slots=[thursday]))["offered_slots_allowed"]


class TestReviewPage:
    past = thread(
        message(0, body="Earlier question"),
        message(30, sernia=True, body="Earlier answer"),
        message(60, body="Can I tour? </script><b>x</b>"),
        message(61, body="Also, is there parking?"),
        message(120, sernia=True, body="What we really sent."),
    )

    def page_data(self) -> tuple[str, dict]:
        scenarios = scenarios_from_thread(self.past)
        rows = [
            {
                "case_id": scenarios[1].scenario_id,
                "policy_commit": "abc1234",
                "decision": decision(team_notes=["Text the lead"]).model_dump(mode="json"),
                "checks": {"has_signature": True, "no_em_dash": False},
            }
        ]
        page = review_page("round-x", scenarios, OPERATING, rows)
        embedded = re.search(
            r'<script type="application/json" id="data">(.*?)</script>', page, re.S
        )
        assert embedded is not None
        return page, json.loads(embedded.group(1))

    def test_each_decision_is_shown_with_what_the_reviewer_needs(self):
        _, data = self.page_data()

        assert (data["run"], data["policy_commit"]) == ("round-x", "abc1234")
        (case,) = data["cases"]
        assert case["id"] == "T-3"
        # The lead's latest burst, not the whole thread and not our reply.
        assert case["lead_message"] == "Can I tour? </script><b>x</b>\n\nAlso, is there parking?"
        assert "Earlier answer" in case["case_text"]
        assert case["actual_reply"] == "What we really sent." and case["actual_delay"] == 59
        assert case["failed_checks"] == ["no_em_dash"]
        assert case["team_notes"] == ["Text the lead"]
        assert case["recorded"] == "tour: none"

    def test_email_text_cannot_break_out_of_the_page(self):
        page, _ = self.page_data()
        assert page.count("</script>") == 2  # the data block and the page's own script

    def test_a_downloaded_label_parses(self):
        label = Label.model_validate(
            {"scenario_id": "T-3", "verdict": "fail", "note": "Too long", "action": "reply"}
        )
        assert label.verdict == "fail"
        assert Label(scenario_id="T-3").verdict is None  # a note without a verdict yet
