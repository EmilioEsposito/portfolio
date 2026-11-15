"""Every branch of the guard that decides whether a notice really goes out. No database.

A notice is sent only in live mode, and then only (a) in production for a
live unit, or (b) with test sends on, to test recipients only. Off skips;
everything else is captured.
"""

from itertools import product

import pytest

from api.src.showing_bookings import guard
from api.src.showing_bookings.guard import Decision, decide
from api.src.showing_bookings.settings import ShowingBookingSettings, read_test_recipients

UNIT = "320-03"
PROSPECT = "+14125550117"
TEAM_PHONE = "+14125550150"
TEAM_EMAIL = "team.test@example.com"
TESTERS = frozenset({TEAM_PHONE, TEAM_EMAIL})
NOT_PRODUCTION = ["", "development", "portfolio-pr-401", "Production", "production-copy"]


def settings(mode="live", live_units=(UNIT,), test_sends=False) -> ShowingBookingSettings:
    return ShowingBookingSettings(
        send_mode=mode, live_units=list(live_units), test_sends_enabled=test_sends
    )


def run(rules, environment="production", unit=UNIT, addresses=(PROSPECT,)) -> Decision:
    return decide(
        settings=rules,
        environment=environment,
        unit=unit,
        addresses=list(addresses),
        test_recipients=TESTERS,
    )


def test_off_skips_even_a_live_unit_in_production():
    assert run(settings(mode="off")) == Decision("skip", "send_mode_off")
    assert run(settings(mode="off", test_sends=True), addresses=[TEAM_PHONE]).action == "skip"


def test_capture_captures_even_a_live_unit_or_a_test_recipient():
    assert run(settings(mode="capture")) == Decision("capture", "send_mode_capture")
    assert (
        run(settings(mode="capture", test_sends=True), addresses=[TEAM_PHONE]).action == "capture"
    )


def test_live_sends_for_a_live_unit_in_production():
    assert run(settings()) == Decision("send", "live_unit")


def test_live_units_match_without_regard_to_case():
    assert run(settings(live_units=["12-a"]), unit="12-A") == Decision("send", "live_unit")


def test_live_captures_a_unit_that_is_not_live_in_production():
    assert run(settings(live_units=["320-04"])) == Decision("capture", "unit_not_live")
    assert run(settings(live_units=[])) == Decision("capture", "unit_not_live")


@pytest.mark.parametrize("environment", NOT_PRODUCTION)
def test_live_in_the_row_but_not_production_is_still_captured(environment):
    assert run(settings(), environment=environment) == Decision("capture", "not_production")


@pytest.mark.parametrize("environment", ["production", *NOT_PRODUCTION])
def test_test_sends_reach_test_recipients_in_any_environment(environment):
    rules = settings(live_units=[], test_sends=True)
    assert run(rules, environment, addresses=[TEAM_PHONE]) == Decision("send", "test_recipient")
    assert run(rules, environment, addresses=[TEAM_EMAIL]).action == "send"


def test_a_test_recipient_is_recognised_however_it_is_written():
    rules = settings(live_units=[], test_sends=True)
    for written in ["(412) 555-0150", "412.555.0150", "14125550150", "TEAM.Test@Example.com "]:
        assert run(rules, "portfolio-pr-401", addresses=[written]).action == "send", written


def test_test_sends_off_capture_a_test_recipient_outside_production():
    rules = settings(test_sends=False)
    assert run(rules, "development", addresses=[TEAM_PHONE]) == Decision(
        "capture", "not_production"
    )


def test_test_sends_capture_anyone_else_outside_production():
    rules = settings(test_sends=True)
    assert run(rules, "development", addresses=[PROSPECT]) == Decision("capture", "not_production")


def test_an_event_is_a_test_send_only_when_every_guest_is_a_test_recipient():
    rules = settings(test_sends=True)
    assert run(rules, "development", addresses=[TEAM_EMAIL, TEAM_EMAIL]).action == "send"
    assert run(rules, "development", addresses=[TEAM_EMAIL, "alex@example.com"]).action == "capture"


def test_a_notice_with_no_address_is_never_a_test_send():
    assert run(settings(test_sends=True), "development", addresses=[]).action == "capture"
    assert not guard.is_test_send([], TESTERS)
    assert not guard.is_test_send(["not a number"], TESTERS)


def test_outside_production_only_a_test_send_can_ever_go_out():
    """Whatever the settings row says, outside production the only send is (b)."""
    for mode, units, test_sends, environment, address in product(
        ["off", "capture", "live"],
        [[], [UNIT], ["320-04"]],
        [False, True],
        NOT_PRODUCTION,
        [PROSPECT, TEAM_PHONE, TEAM_EMAIL],
    ):
        decision = run(settings(mode, units, test_sends), environment, addresses=[address])
        if decision.action == "send":
            assert (mode, test_sends, decision.reason) == ("live", True, "test_recipient")
            assert address in TESTERS


def test_test_recipients_come_from_the_environment_at_call_time(monkeypatch):
    monkeypatch.setenv("SHOWING_TEST_PHONES", "(412) 555-0150, 412-555-0151,, not-a-number")
    monkeypatch.setenv("SHOWING_TEST_EMAILS", "Team.Test@Example.com, nobody")
    assert read_test_recipients() == frozenset(
        {"+14125550150", "+14125550151", "team.test@example.com"}
    )
    monkeypatch.delenv("SHOWING_TEST_PHONES")
    monkeypatch.delenv("SHOWING_TEST_EMAILS")
    assert read_test_recipients() == frozenset()
