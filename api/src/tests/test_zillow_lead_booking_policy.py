"""The lead policy for booked showings, checked without a model.

- ``auto_reply.states_tour_time``: whether a reply's words state the time its
  outcome confirms, in the policy's fixed form. Not used by the automatic send.
- ``evals.checks.booking_change``: what code would book, move or cancel for a
  decision, read from the case, as ``showing_bookings.lead_bookings`` does it.
- The prompts: every new instruction names the own-showings section, except
  the time form, so a case without that section is told what it was told.
"""

from datetime import UTC, datetime

import pytest

from api.src.tests.test_zillow_lead_agent import decision
from api.src.zillow_leads import agent
from api.src.zillow_leads.auto_reply import states_tour_time
from api.src.zillow_leads.case import (
    OWN_SHOWINGS_HEADING,
    OwnShowing,
    build_case,
    tour_time_words,
)
from api.src.zillow_leads.evals import synthetic
from api.src.zillow_leads.evals.checks import DETAILS_LINE, Booking, booking_change
from api.src.zillow_leads.evals.scenarios import case_for

ITEMS = {item.scenario.scenario_id: item for item in synthetic.scenarios()}
CONFIRM = ITEMS["synthetic-confirm-by-email-0"]
CASE = case_for(CONFIRM.scenario, synthetic.LISTINGS, CONFIRM.edit)
FOUR_PM = synthetic._at(14, 16)  # Wednesday, April 14, 2027, New York


def reply(body: str, start: datetime | None = FOUR_PM, status: str = "confirmed"):
    tour = {"status": status, "start": start.isoformat() if start else None}
    return decision(reply_body=body, tour=tour, lead={"unit": "320-03"})


class TestStatesTourTime:
    def test_the_fixed_form(self):
        assert tour_time_words(FOUR_PM, "America/New_York") == "Wednesday, April 14 at 4:00 PM"

    def test_the_time_is_read_in_the_case_time_zone(self):
        in_utc = FOUR_PM.astimezone(UTC)  # 20:00 UTC
        assert states_tour_time(CASE, reply("See you Wednesday, April 14 at 4:00 PM.", in_utc))
        assert not states_tour_time(CASE, reply("See you Wednesday, April 14 at 8:00 PM.", in_utc))
        chicago = CASE.model_copy(update={"timezone": "America/Chicago"})
        assert states_tour_time(chicago, reply("Wednesday, April 14 at 3:00 PM works.", in_utc))

    def test_daylight_saving_is_the_zone_s_own(self):
        winter = datetime(2027, 1, 13, 21, 0, tzinfo=UTC)  # EST, UTC-5
        assert tour_time_words(winter, "America/New_York") == "Wednesday, January 13 at 4:00 PM"
        assert states_tour_time(CASE, reply("Wednesday, January 13 at 4:00 PM", winter))

    def test_the_hour_has_no_leading_zero(self):
        assert not states_tour_time(CASE, reply("Wednesday, April 14 at 04:00 PM", FOUR_PM))
        ten = synthetic._at(15, 10)
        assert tour_time_words(ten, "America/New_York") == "Thursday, April 15 at 10:00 AM"
        assert states_tour_time(CASE, reply("Thursday, April 15 at 10:00 AM.", ten))

    def test_line_breaks_spaces_and_case_are_ignored(self):
        body = "Hello,\n\nWe will see you\nWednesday, April 14  at 4:00 pm at the unit."
        assert states_tour_time(CASE, reply(body))

    def test_a_different_time_or_another_wording_is_not_the_time(self):
        for body in (
            "See you Wednesday, April 14 at 5:00 PM.",
            "See you Thursday, April 15 at 4:00 PM.",
            "See you Wednesday, April 1 at 4:00 PM.",
            "See you Wednesday, April 14 at 4:00:30 PM.",
            "See you tomorrow at 4.",
            "See you Wednesday 4/14 at 4:00 PM.",
            "See you Wednesday, April 14, at 4:00 PM.",
        ):
            assert not states_tour_time(CASE, reply(body)), body

    def test_no_time_in_the_text_or_no_start(self):
        assert not states_tour_time(CASE, reply("Thanks, see you then."))
        assert not states_tour_time(CASE, reply("Wednesday, April 14 at 4:00 PM", start=None))

    def test_the_moved_from_time_alone_is_not_enough(self):
        thursday = synthetic._at(15, 11)
        body = "We moved you from Wednesday, April 14 at 4:00 PM to Thursday at 11."
        assert not states_tour_time(CASE, reply(body, thursday, "rescheduled"))
        both = "Moved from Wednesday, April 14 at 4:00 PM to Thursday, April 15 at 11:00 AM."
        assert states_tour_time(CASE, reply(both, thursday, "rescheduled"))


class TestBookingChange:
    def outcome(self, status: str, start: datetime | None = None, action: str = "reply"):
        tour = {"status": status, "start": start.isoformat() if start else None}
        return decision(action=action, tour=tour, lead={"unit": "320-03"})

    def case_with(self, *own: OwnShowing):
        return CASE.model_copy(update={"own_showings": list(own)})

    def test_switch_off_books_nothing(self):
        plain = ITEMS["synthetic-without-booking-service-0"]
        case = case_for(plain.scenario, synthetic.LISTINGS, plain.edit)
        assert booking_change(case, self.outcome("confirmed", FOUR_PM)) == Booking()

    def test_a_confirmed_time_books_and_the_same_time_again_changes_nothing(self):
        assert booking_change(CASE, self.outcome("confirmed", FOUR_PM)) == Booking(
            action="book", unit="320-03", start=FOUR_PM
        )
        booked = OwnShowing(unit="320-03", start=FOUR_PM, status="booked")
        assert booking_change(self.case_with(booked), self.outcome("confirmed", FOUR_PM)) == (
            Booking()
        )

    def test_another_time_at_the_same_unit_moves(self):
        booked = OwnShowing(unit="320-03", start=FOUR_PM, status="booked")
        thursday = synthetic._at(15, 11)
        assert booking_change(
            self.case_with(booked), self.outcome("rescheduled", thursday)
        ) == Booking(action="move", unit="320-03", start=thursday)

    def test_cancelled_cancels_only_a_booked_place(self):
        booked = OwnShowing(unit="320-03", start=FOUR_PM, status="booked")
        gone = booked.model_copy(update={"status": "cancelled", "cancelled_by": "team"})
        assert booking_change(self.case_with(booked), self.outcome("cancelled")) == Booking(
            action="cancel", unit="320-03"
        )
        assert booking_change(self.case_with(gone), self.outcome("cancelled")) == Booking()

    def test_staying_quiet_only_ever_cancels_and_escalating_changes_nothing(self):
        booked = OwnShowing(unit="320-03", start=FOUR_PM, status="booked")
        case = self.case_with(booked)
        thursday = synthetic._at(15, 11)
        assert booking_change(case, self.outcome("confirmed", thursday, "no_reply")) == Booking()
        assert booking_change(case, self.outcome("cancelled", action="no_reply")).action == (
            "cancel"
        )
        escalate = decision(
            action="escalate",
            question="?",
            tour={"status": "cancelled"},
            lead={"unit": "320-03"},
        )
        assert booking_change(case, escalate) == Booking()

    def test_virtual_unlisted_or_no_start_books_nothing(self):
        virtual = decision(
            tour={"status": "confirmed", "start": FOUR_PM.isoformat(), "virtual": True},
            lead={"unit": "320-03"},
        )
        unlisted = decision(
            tour={"status": "confirmed", "start": FOUR_PM.isoformat()}, lead={"unit": "999-99"}
        )
        for outcome in (virtual, unlisted, self.outcome("confirmed")):
            assert booking_change(CASE, outcome) == Booking()


class TestPrompts:
    def test_the_details_line_is_written_in_the_policy_as_the_check_reads_it(self):
        policy = agent.load_policy()
        assert f"`{DETAILS_LINE} <the link>`" in policy

    def test_the_fixed_form_in_the_policy_is_the_form_code_checks(self):
        assert f'"{tour_time_words(FOUR_PM, "America/New_York")}"' in agent.load_policy()

    def test_the_case_heading_the_prompts_name_is_the_one_the_case_renders(self):
        assert OWN_SHOWINGS_HEADING == "## This lead's own showings"
        assert '"This lead\'s own showings"' in agent.load_policy()

    @pytest.mark.parametrize("path", [agent.POLICY_PATH, agent.RECORD_PATH])
    def test_each_new_instruction_about_own_showings_is_conditional(self, path):
        """Lines that need the booking service are worded so a case without it skips them."""
        text = path.read_text()
        bullets = [
            bullet
            for bullet in text.replace("\n  ", " ").replace("\n    ", " ").split("\n- ")
            if "details here" in bullet or "own showings" in bullet or "code books" in bullet
        ]
        assert bullets, path.name
        for bullet in bullets:
            assert (
                "the case lists this lead's own showings" in bullet
                or 'listed as booked under "This lead\'s own showings"' in bullet
            ), bullet

    def test_the_policy_never_makes_a_mobile_a_condition(self):
        assert "Never ask for a mobile number or make one" in agent.load_policy()

    def test_nothing_lead_agent_only_went_into_the_shared_policy(self):
        policy = agent.load_policy()
        assert "`tour`" not in policy and "lead.unit" not in policy
        record = " ".join(agent.RECORD_PATH.read_text().split())
        assert "code books, moves and cancels" in record


def test_a_case_built_with_the_switch_off_has_no_own_showings_section():
    plain = build_case(CONFIRM.scenario.thread, synthetic.LISTINGS, upto=0)
    assert plain.own_showings is None
