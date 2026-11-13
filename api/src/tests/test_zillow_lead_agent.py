"""The Zillow lead case, the agent's outcomes and the exact-answer checks (no network)."""

import json
import re
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest
from pydantic import TypeAdapter, ValidationError
from pydantic_ai.models.test import TestModel
from pydantic_evals.evaluators import EvaluatorContext
from pydantic_evals.otel.span_tree import SpanTree

from api.src.sernia_ai.zillow_leads import agent as agent_module
from api.src.sernia_ai.zillow_leads.agent import Escalate, LeadOutcome, NoReply, Reply
from api.src.sernia_ai.zillow_leads.case import (
    AgentAvailability,
    BookedTour,
    LeadCase,
    Listing,
    ListingData,
    TourSlot,
    build_case,
    decision_points,
    offerable_slots,
    render_case,
)
from api.src.sernia_ai.zillow_leads.evals.checks import ReplyChecks, build_dataset
from api.src.sernia_ai.zillow_leads.evals.labels import Label, output_hash
from api.src.sernia_ai.zillow_leads.evals.pages import dataset_page, review_page
from api.src.sernia_ai.zillow_leads.evals.scenarios import (
    assign_splits,
    case_for,
    in_split,
    scenarios_from_thread,
)
from api.src.sernia_ai.zillow_leads.thread import LeadMessage, LeadThread

ET = ZoneInfo("America/New_York")
MONDAY_9AM = datetime(2026, 5, 4, 9, 0, tzinfo=ET)

LISTING_DATA = ListingData(
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
        slots = offerable_slots(LISTING_DATA, MONDAY_9AM)
        assert min(starts(slots)) == datetime(2026, 5, 5, 10, 0, tzinfo=ET)

    def test_a_slot_exactly_24_hours_out_is_allowed(self):
        slots = offerable_slots(LISTING_DATA, datetime(2026, 5, 4, 10, 0, tzinfo=ET))
        assert datetime(2026, 5, 5, 10, 0, tzinfo=ET) in starts(slots)
        just_late = offerable_slots(LISTING_DATA, datetime(2026, 5, 4, 10, 1, tzinfo=ET))
        assert datetime(2026, 5, 5, 10, 0, tzinfo=ET) not in starts(just_late)

    def test_only_showing_hours(self):
        # 8:00 is before showings start; 18:30 would end after they stop.
        clock_times = {slot.start.time() for slot in offerable_slots(LISTING_DATA, MONDAY_9AM)}
        assert clock_times == {time(10, 0), time(12, 0), time(18, 0)}

    def test_blackout_date_is_skipped_but_the_other_weekend_day_is_not(self):
        days = {slot.start.date() for slot in offerable_slots(LISTING_DATA, MONDAY_9AM)}
        assert date(2026, 5, 9) in days  # Saturday
        assert date(2026, 5, 10) not in days  # Sunday, blacked out

    def test_weekday_and_weekend_patterns_do_not_mix(self):
        for slot in offerable_slots(LISTING_DATA, MONDAY_9AM):
            weekend = slot.start.weekday() >= 5
            assert (slot.start.time() == time(12, 0)) == weekend

    def test_the_clock_is_read_in_local_time(self):
        # 23:30 UTC on Monday is 19:30 Monday in New York, not Tuesday.
        slots = offerable_slots(LISTING_DATA, datetime(2026, 5, 4, 23, 30, tzinfo=UTC))
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

        case = build_case(t, LISTING_DATA, upto=0, booked_tours=booked)

        assert [m.body for m in case.thread.messages] == ["Is it available?"]
        assert case.now == t.messages[0].sent_at + timedelta(minutes=5)
        assert case.case_id == "T-0"
        assert [tour.start for tour in case.booked_tours] == [MONDAY_9AM + timedelta(days=2)]
        assert min(starts(case.offerable_slots)) == datetime(2026, 5, 5, 10, 0, tzinfo=ET)
        assert len(t.messages) == 2  # the source thread is not modified

    def test_a_unit_leaves_the_listings_on_the_day_it_is_rented(self):
        listing_data = LISTING_DATA.model_copy(
            update={
                "listings": [
                    Listing(unit="12-A", lease_start_earliest=date(2026, 7, 1)),
                    Listing(unit="12-C", rented_on=date(2026, 5, 4)),
                    Listing(unit="12-D", rented_on=date(2026, 5, 5)),
                ]
            }
        )

        case = build_case(thread(message(0)), listing_data, upto=0)  # Monday May 4

        assert [item.unit for item in case.listings] == ["12-A", "12-D"]
        assert case.rented_units == ["12-B", "12-C"]
        assert "12-A: lease starts July 1, 2026" in render_case(case)

    def test_rendered_case_never_names_staff_and_lists_a_shared_time_once(self):
        case = build_case(thread(message(0, body="Can I tour Tuesday?")), LISTING_DATA, upto=0)

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


_OUTCOME = TypeAdapter(LeadOutcome)


def decision(**overrides) -> Reply | NoReply | Escalate:
    """An outcome, a reply unless ``action`` says otherwise."""
    action = overrides.get("action", "reply")
    fields: dict = {"lead": {}, "tour": {}, "action": action, "reason": "New lead."}
    if action == "reply":
        fields["reply_body"] = "Hello,\n\nSernia Capital Team"
    return _OUTCOME.validate_python({**fields, **overrides})


class TestOutcomes:
    def test_the_three_outcomes_are_three_types(self):
        assert isinstance(decision(), Reply)
        assert isinstance(decision(action="no_reply"), NoReply)
        assert isinstance(decision(action="escalate", question="Is 12-C listed?"), Escalate)

    def test_a_reply_cannot_exist_without_its_text(self):
        with pytest.raises(ValidationError, match="reply_body"):
            _OUTCOME.validate_python(
                {"lead": {}, "tour": {}, "action": "reply", "reason": "New lead."}
            )
        with pytest.raises(ValidationError, match="reply_body"):
            decision(reply_body="")

    def test_staying_quiet_cannot_carry_a_draft(self):
        with pytest.raises(ValidationError, match="reply_body"):
            decision(action="no_reply", reply_body="Hello")

    def test_escalating_must_say_what_is_needed(self):
        with pytest.raises(ValidationError, match="question"):
            decision(action="escalate")

    @pytest.mark.asyncio
    async def test_the_agent_gets_the_policy_the_case_and_no_tools(self):
        case = build_case(thread(message(0, body="Is it available?")), LISTING_DATA, upto=0)
        model = TestModel(
            custom_output_args={
                "lead": {},
                "tour": {},
                "reason": "Test.",
                "reply_body": "Hello,\n\nSernia Capital Team",
            }
        )

        with agent_module.lead_agent().override(model=model):
            result = await agent_module.respond(case)

        assert isinstance(result, Reply)
        params = model.last_model_request_parameters
        assert params is not None
        # The whole design rests on this: nothing to call, three ways to finish.
        assert params.function_tools == []
        assert [tool.name for tool in params.output_tools] == ["reply", "no_reply", "escalate"]
        assert [part.content for part in params.instruction_parts] == [
            agent_module.load_instructions().strip()
        ]


def check(case: LeadCase, output: Reply | NoReply | Escalate) -> dict[str, bool]:
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
    case = build_case(thread(message(0)), LISTING_DATA, upto=0)
    allowed = datetime(2026, 5, 5, 10, 0, tzinfo=ET)

    def test_a_clean_reply_passes_every_check(self):
        # Same instant written with a different offset still counts as allowed.
        result = check(self.case, decision(offered_slots=[self.allowed.astimezone(UTC)]))
        assert result == {
            "offered_slots_allowed": True,
            "no_em_dash": True,
            "has_greeting": True,
            "site_address_is_a_link": True,
            "clarification_leads": True,
            "has_signature": True,
            "no_staff_names": True,
            "links_allowed": True,
        }

    def test_a_reply_opens_with_a_greeting(self):
        signed = "\n\nSernia Capital Team"
        assert check(self.case, decision(reply_body="Hi Sam,\n\nYes." + signed))["has_greeting"]
        assert not check(self.case, decision(reply_body="Yes, it is." + signed))["has_greeting"]

    def test_the_site_address_is_written_as_a_link(self):
        signed = "\n\nSernia Capital Team\nall@serniacapital.com"
        linked = "Hello,\n\nApply at https://serniacapital.com." + signed
        bare = "Hello,\n\nApply at serniacapital.com." + signed
        # The email address in the signature is not the site address.
        assert check(self.case, decision(reply_body=linked))["site_address_is_a_link"]
        assert not check(self.case, decision(reply_body=bare))["site_address_is_a_link"]

    def test_a_clarification_comes_right_after_the_greeting(self):
        signed = "\n\nSernia Capital Team"
        ask = "Quick clarification: what kind of pet do you have?"
        first = f"Hi Sam,\n\n{ask}\n\nAssuming you have a cat, book here." + signed
        after_one_line = f"Hi Sam,\n\n4:30 is not available.\n\n{ask}" + signed
        buried = f"Hi Sam,\n\nYou can book here.\n\nThe unit has a deck.\n\n{ask}" + signed
        inline = "Hi Sam,\n\nYou can book here. Quick clarification: any pets?" + signed
        results = [
            check(self.case, decision(reply_body=body))["clarification_leads"]
            for body in (first, after_one_line, buried, inline)
        ]
        assert results == [True, True, False, False]

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

    def test_a_reply_may_only_use_links_the_case_supplied(self):
        with_link = LISTING_DATA.model_copy(update={"booking_url": "https://book.example/tours"})
        case = build_case(thread(message(0)), with_link, upto=0)
        signed = "\n\nSernia Capital Team"

        text = render_case(case)
        assert "## Booking links\n- 12-A: https://book.example/tours" in text
        assert "## Booking links\n- (none)" in render_case(self.case)

        good = "Click here to book a showing: https://book.example/tours." + signed
        assert check(case, decision(reply_body=good))["links_allowed"] is True
        invented = "Book at https://book.example/other" + signed
        assert check(case, decision(reply_body=invented))["links_allowed"] is False
        # The same link is not allowed where the case did not supply it.
        assert check(self.case, decision(reply_body=good))["links_allowed"] is False
        # A link from the policy is allowed, however the address is written.
        ours = "Apply at https://www.serniacapital.com/." + signed
        assert check(self.case, decision(reply_body=ours))["links_allowed"] is True
        deeper = "Apply at https://serniacapital.com/apply-now" + signed
        assert check(self.case, decision(reply_body=deeper))["links_allowed"] is False

    def test_a_unit_can_have_its_own_booking_link(self):
        listing_data = LISTING_DATA.model_copy(
            update={
                "booking_url": "https://book.example/any",
                "listings": [
                    Listing(unit="12-A", booking_url="https://book.example/12a"),
                    Listing(unit="12-C"),
                ],
            }
        )
        case = build_case(thread(message(0)), listing_data, upto=0)
        assert [item.booking_url for item in case.listings] == [
            "https://book.example/12a",
            "https://book.example/any",
        ]

    def test_no_reply_is_not_scored(self):
        assert check(self.case, decision(action="no_reply")) == {}


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
        assert "tours Tuesday" not in render_case(case_for(first, LISTING_DATA))

    def test_a_scenario_holds_the_conversation_and_nothing_about_the_business(self):
        first = scenarios_from_thread(self.past)[0]
        assert set(first.model_dump()) == {
            "thread",
            "actual_reply",
            "actual_reply_delay_minutes",
        }

    def test_the_same_scenario_runs_against_changed_listing_data(self):
        first = scenarios_from_thread(self.past)[0]
        richer = LISTING_DATA.model_copy(
            update={
                "listings": [Listing(unit="12-A", facts="Second floor\n- Dishwasher")],
                "booking_url": "https://book.example/tours",
            }
        )

        before, after = case_for(first, LISTING_DATA), case_for(first, richer)

        assert before.case_id == after.case_id == first.scenario_id
        assert before.now == after.now and before.thread == after.thread
        text = render_case(after)
        assert "  - Second floor\n  - Dishwasher" in text
        assert "## Booking links\n- 12-A: https://book.example/tours" in text
        assert "## Booking links\n- (none)" in render_case(before)
        # What is the same for every lead lives in the policy, not in the case.
        assert "Standard answers" not in text
        assert "## Standard answers" in agent_module.load_policy()
        dataset = build_dataset([first], richer)
        assert [case.name for case in dataset.cases] == ["T-0"]


class TestSlotsFromElsewhere:
    def test_a_calendar_or_booking_service_can_supply_the_slots(self):
        thursday = datetime(2026, 5, 7, 15, 0, tzinfo=ET)
        case = build_case(
            thread(message(0)),
            LISTING_DATA,
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
        page = review_page("round-x", scenarios, LISTING_DATA, rows, "Reply when </script> owed.")
        embedded = re.search(
            r'<script type="application/json" id="data">(.*?)</script>', page, re.S
        )
        assert embedded is not None
        return page, json.loads(embedded.group(1))

    def test_each_decision_is_shown_with_what_the_reviewer_needs(self):
        _, data = self.page_data()

        assert (data["run"], data["policy_commit"]) == ("round-x", "abc1234")
        assert data["policy"] == "Reply when </script> owed."
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

    def test_a_label_is_tied_to_the_output_it_judged(self):
        scenarios = scenarios_from_thread(self.past)
        first = decision(reply_body="Hello, Tuesday at 10?\n\nSernia Capital Team")
        reworded = decision(reply_body="Hi, how about Tuesday at 10?\n\nSernia Capital Team")
        old_row = {"case_id": "T-3", "decision": first.model_dump(mode="json"), "checks": {}}
        new_row = {"case_id": "T-3", "decision": reworded.model_dump(mode="json"), "checks": {}}
        label = Label(
            scenario_id="T-3",
            verdict="fail",
            note="Too vague",
            output_hash=output_hash(old_row["decision"]),
            run="round-old",
        )

        page = review_page(
            "round-new",
            scenarios,
            LISTING_DATA,
            [new_row],
            labels=[label],
            previous={"round-old": [old_row]},
        )
        embedded = re.search(
            r'<script type="application/json" id="data">(.*?)</script>', page, re.S
        )
        assert embedded is not None
        data = json.loads(embedded.group(1))

        # The reworded output has a different identity, so the page will offer the
        # old verdict for confirmation instead of applying it.
        assert data["cases"][0]["hash"] != label.output_hash
        assert data["previous"] == {"round-old": {"T-3": label.output_hash}}
        assert data["seed_labels"][0]["verdict"] == "fail"
        assert output_hash(old_row["decision"]) == output_hash(first.model_dump(mode="json"))

    def test_a_downloaded_label_parses(self):
        label = Label.model_validate(
            {
                "scenario_id": "T-3",
                "verdict": "fail",
                "note": "Too long",
                "corrected_reply": "Hello, yes. Tuesday at 10?",
                "action": "reply",
            }
        )
        assert label.verdict == "fail" and label.corrected_reply.startswith("Hello")
        assert Label(scenario_id="T-3").verdict is None  # a note without a verdict yet


class TestSplits:
    threads = [f"thread-{number:03d}" for number in range(100)]

    def test_every_thread_gets_one_split_in_the_stated_shares(self):
        splits = assign_splits(self.threads, seen=set(), seed=1)

        assert set(splits) == set(self.threads)
        counts = {name: list(splits.values()).count(name) for name in ("train", "dev", "test")}
        assert counts == {"train": 15, "dev": 45, "test": 40}

    def test_the_same_seed_gives_the_same_split(self):
        again = assign_splits(list(reversed(self.threads)), seen=set(), seed=1)
        assert again == assign_splits(self.threads, seen=set(), seed=1)
        assert again != assign_splits(self.threads, seen=set(), seed=2)

    def test_threads_already_looked_at_can_only_be_dev(self):
        seen = set(self.threads[:30])
        splits = assign_splits(self.threads, seen=seen, seed=1)

        assert {splits[thread] for thread in seen} == {"dev"}
        assert list(splits.values()).count("test") == 40  # test keeps its full share

    def test_a_thread_keeps_all_its_scenarios_in_one_split(self):
        first = thread(message(0), message(30, sernia=True), message(60))
        other = first.model_copy(update={"thread_id": "U"})
        scenarios = scenarios_from_thread(first) + scenarios_from_thread(other)

        dev = in_split(scenarios, {"T": "dev", "U": "test"}, "dev")

        assert [item.scenario_id for item in dev] == ["T-0", "T-2"]
        assert in_split(scenarios, {"T": "dev"}, "test") == []  # unassigned is in no split


class TestDatasetPage:
    def scenarios(self):
        dev = thread(
            message(0, body="Is it available?"),
            message(30, sernia=True, body="Yes, tours Tuesday."),
            message(60, body="Great </script> see you"),
        )
        train = dev.model_copy(
            update={"thread_id": "TR", "messages": [message(5, body="Train thread question")]}
        )
        sealed = dev.model_copy(
            update={"thread_id": "TE", "messages": [message(7, body="SEALED lead message")]}
        )
        return [s for t in (dev, train, sealed) for s in scenarios_from_thread(t)]

    def page_data(self) -> tuple[str, dict]:
        runs = {"round-x": [{"case_id": "T-2", "decision": {"action": "no_reply"}}]}
        page = dataset_page(
            self.scenarios(),
            {"T": "dev", "TR": "train", "TE": "test"},
            runs,
            {"round-x": "runs/round-x.html"},
        )
        embedded = re.search(
            r'<script type="application/json" id="data">(.*?)</script>', page, re.S
        )
        assert embedded is not None
        return page, json.loads(embedded.group(1))

    def test_train_and_dev_are_readable_and_the_test_split_is_only_a_count(self):
        page, data = self.page_data()

        assert data["totals"] == {
            "train": {"threads": 1, "cases": 1},
            "dev": {"threads": 1, "cases": 2},
            "test": {"threads": 1, "cases": 1},
        }
        assert {case["id"]: case["split"] for case in data["cases"]} == {
            "T-0": "dev",
            "T-2": "dev",
            "TR-0": "train",
        }
        assert "SEALED" not in page

    def test_each_case_shows_the_lead_message_the_history_and_links_to_its_review(self):
        _, data = self.page_data()
        by_id = {case["id"]: case for case in data["cases"]}

        follow_up = by_id["T-2"]
        assert follow_up["first"] is False and by_id["T-0"]["first"] is True
        assert follow_up["lead_message"] == "Great </script> see you"
        assert [m["who"] for m in follow_up["earlier"]] == ["Lead", "Us"]
        assert follow_up["when"] == "Mon May 4, 10:00 AM"
        assert follow_up["runs"] == [
            {"run": "round-x", "action": "no_reply", "href": "runs/round-x.html#card-T-2"}
        ]
        assert by_id["T-0"]["actual_reply"] == "Yes, tours Tuesday."
        assert data["reviews"] == [{"run": "round-x", "href": "runs/round-x.html", "count": 1}]

    def test_email_text_cannot_break_out_of_the_page(self):
        page, _ = self.page_data()
        assert page.count("</script>") == 2
