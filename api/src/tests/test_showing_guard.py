"""Every branch of the guard that decides whether a notice really goes out. No database.

Outside production a notice leaves only for the test list. In production one a
person approved always goes; otherwise test mode sends to the test list,
capture mode sends nothing, and live mode sends for the units that are live.
Everything else is captured. Also how a settings row saved before test mode
reads, and which units may be booked in each mode.
"""

from itertools import product

import pytest
from pydantic import ValidationError

from api.src.showing_bookings import guard
from api.src.showing_bookings.guard import Decision, decide
from api.src.showing_bookings.settings import (
    ShowingBookingSettings,
    read_test_recipients,
    unit_is_live,
    unit_open,
)

UNIT = "320-03"
OTHER_UNIT = "320-04"
PROSPECT = "+14125550117"
TEAM_PHONE = "+14125550150"
TEAM_EMAIL = "team.test@example.com"
TESTERS = frozenset({TEAM_PHONE, TEAM_EMAIL})
MODES = ["test", "capture", "live"]
NOT_PRODUCTION = ["", "development", "portfolio-pr-401", "Production", "production-copy"]
# Who a notice reaches, and whether everyone in it is on the test list.
REACHES = [
    ([TEAM_PHONE], True),
    ([TEAM_EMAIL], True),
    ([TEAM_EMAIL, TEAM_PHONE], True),
    ([TEAM_EMAIL, "alex@example.com"], False),
    ([TEAM_PHONE, PROSPECT], False),
    ([PROSPECT], False),
    ([], False),
]


def settings(mode="live", live_units=(UNIT,), all_units=False) -> ShowingBookingSettings:
    return ShowingBookingSettings(
        send_mode=mode, live_units=list(live_units), live_all_units=all_units
    )


def run(
    rules, environment="production", unit=UNIT, addresses=(PROSPECT,), approved=False
) -> Decision:
    return decide(
        settings=rules,
        environment=environment,
        unit=unit,
        addresses=list(addresses),
        test_recipients=TESTERS,
        approved=approved,
    )


def test_every_combination():
    """The whole rule, written another way and compared with the guard's answer."""
    cases = 0
    for mode, (units, all_units), environment, (addresses, on_list), approved in product(
        MODES,
        [([], False), ([UNIT], False), ([OTHER_UNIT], False), ([], True), ([OTHER_UNIT], True)],
        ["production", *NOT_PRODUCTION],
        REACHES,
        [False, True],
    ):
        cases += 1
        production = environment == "production"
        live = all_units or UNIT in units
        sends = (
            (approved and (production or on_list))
            or (mode == "test" and on_list)
            or (mode == "live" and production and live)
        )
        decision = run(settings(mode, units, all_units), environment, UNIT, addresses, approved)
        where = (mode, units, all_units, environment, addresses, approved)
        assert decision.action == ("send" if sends else "capture"), where
        if not production and decision.action == "send":
            assert on_list, where
    assert cases == 3 * 5 * 6 * 7 * 2


@pytest.mark.parametrize("environment", NOT_PRODUCTION)
def test_outside_production_nothing_leaves_but_to_the_test_list(environment):
    """Whatever the settings row says, and whoever approved it."""
    for mode, units, all_units, (addresses, on_list), approved in product(
        MODES, [[], [UNIT]], [False, True], REACHES, [False, True]
    ):
        decision = run(settings(mode, units, all_units), environment, UNIT, addresses, approved)
        if decision.action == "send":
            assert on_list and (mode == "test" or approved)
            assert decision.reason == "test_recipient"


def test_test_mode_sends_to_the_test_list_and_captures_the_rest():
    for environment in ["production", *NOT_PRODUCTION]:
        rules = settings("test", [UNIT], all_units=True)
        assert run(rules, environment, addresses=[TEAM_PHONE]) == Decision("send", "test_recipient")
        assert run(rules, environment, addresses=[TEAM_EMAIL]).action == "send"
        assert run(rules, environment, addresses=[PROSPECT]) == Decision(
            "capture", "not_test_recipient"
        )


def test_capture_captures_even_a_live_unit_or_a_test_recipient():
    for environment in ["production", *NOT_PRODUCTION]:
        rules = settings("capture", [UNIT], all_units=True)
        assert run(rules, environment) == Decision("capture", "send_mode_capture")
        assert run(rules, environment, addresses=[TEAM_PHONE]) == Decision(
            "capture", "send_mode_capture"
        )


def test_live_sends_for_a_live_unit_in_production():
    assert run(settings()) == Decision("send", "live_unit")


def test_live_units_match_without_regard_to_case():
    assert run(settings(live_units=["12-a"]), unit="12-A") == Decision("send", "live_unit")


def test_live_captures_a_unit_that_is_not_live_in_production():
    assert run(settings(live_units=[OTHER_UNIT])) == Decision("capture", "unit_not_live")
    assert run(settings(live_units=[])) == Decision("capture", "unit_not_live")


def test_all_units_sends_for_every_unit_in_production():
    rules = settings(live_units=[], all_units=True)
    assert run(rules) == Decision("send", "all_units_live")
    assert run(rules, unit="a unit added later") == Decision("send", "all_units_live")


def test_live_has_no_test_sends():
    assert run(settings(live_units=[]), addresses=[TEAM_PHONE]) == Decision(
        "capture", "unit_not_live"
    )
    assert run(settings(), "development", addresses=[TEAM_PHONE]) == Decision(
        "capture", "not_production"
    )


@pytest.mark.parametrize("environment", NOT_PRODUCTION)
def test_live_in_the_row_but_not_production_is_still_captured(environment):
    assert run(settings(), environment=environment) == Decision("capture", "not_production")
    assert run(settings(all_units=True), environment=environment) == Decision(
        "capture", "not_production"
    )


def test_an_approved_notice_goes_in_production_in_any_mode():
    for mode, units, all_units in product(MODES, [[], [UNIT], [OTHER_UNIT]], [False, True]):
        rules = settings(mode, units, all_units)
        assert run(rules, approved=True) == Decision("send", "approved")


@pytest.mark.parametrize("environment", NOT_PRODUCTION)
def test_an_approved_notice_outside_production_goes_only_to_the_test_list(environment):
    for mode in MODES:
        rules = settings(mode, [UNIT], all_units=True)
        assert run(rules, environment, approved=True) == Decision("capture", "not_production")
        assert run(rules, environment, addresses=[TEAM_PHONE, PROSPECT], approved=True) == Decision(
            "capture", "not_production"
        )
        assert run(rules, environment, addresses=[TEAM_PHONE], approved=True) == Decision(
            "send", "test_recipient"
        )


def test_a_test_recipient_is_recognised_however_it_is_written():
    rules = settings("test", [])
    for written in ["(412) 555-0150", "412.555.0150", "14125550150", "TEAM.Test@Example.com "]:
        assert run(rules, "portfolio-pr-401", addresses=[written]).action == "send", written


def test_an_event_is_a_test_send_only_when_every_guest_is_a_test_recipient():
    rules = settings("test", [])
    assert run(rules, "development", addresses=[TEAM_EMAIL, TEAM_EMAIL]).action == "send"
    assert run(rules, "development", addresses=[TEAM_EMAIL, "alex@example.com"]).action == "capture"


def test_a_notice_with_no_address_is_never_a_test_send():
    assert run(settings("test"), "development", addresses=[]).action == "capture"
    assert run(settings("test"), addresses=[], approved=False).action == "capture"
    assert run(settings("capture"), "development", addresses=[], approved=True).action == "capture"
    assert not guard.is_test_send([], TESTERS)
    assert not guard.is_test_send(["not a number"], TESTERS)


def test_test_recipients_come_from_the_environment_at_call_time(monkeypatch):
    monkeypatch.setenv("SHOWING_TEST_PHONES", "(412) 555-0150, 412-555-0151,, not-a-number")
    monkeypatch.setenv("SHOWING_TEST_EMAILS", "Team.Test@Example.com, nobody")
    assert read_test_recipients() == frozenset(
        {"+14125550150", "+14125550151", "team.test@example.com"}
    )
    monkeypatch.delenv("SHOWING_TEST_PHONES")
    monkeypatch.delenv("SHOWING_TEST_EMAILS")
    assert read_test_recipients() == frozenset()


def test_would_send_is_exactly_decide_answering_send():
    """The helper the host's morning text uses adds no rule of its own."""
    for mode, live, all_units, environment, unit, address, approved in product(
        MODES,
        ((), (UNIT,)),
        (False, True),
        ("production", "development"),
        (UNIT, "412-01"),
        (PROSPECT, TEAM_PHONE),
        (False, True),
    ):
        rules = settings(mode=mode, live_units=live, all_units=all_units)
        expected = run(rules, environment, unit, [address], approved)
        assert guard.would_send(
            settings=rules,
            environment=environment,
            unit=unit,
            addresses=[address],
            test_recipients=TESTERS,
            approved=approved,
        ) == (expected.action == "send")


# Settings rows saved before test mode


@pytest.mark.parametrize(
    ("mode", "units", "test_sends", "reads_as"),
    [
        ("off", [], False, "capture"),
        ("off", [UNIT], True, "capture"),
        ("capture", [], False, "capture"),
        ("capture", [], True, "capture"),
        ("capture", [UNIT], True, "capture"),
        ("live", [], True, "test"),
        ("live", [UNIT], True, "live"),
        ("live", [UNIT], False, "live"),
        ("live", [], False, "live"),
    ],
)
def test_an_old_row_reads_as_what_it_meant(mode, units, test_sends, reads_as):
    row = {"send_mode": mode, "live_units": units, "test_sends_enabled": test_sends}

    read = ShowingBookingSettings.model_validate(row)

    assert (read.send_mode, read.live_units, read.live_all_units) == (reads_as, units, False)
    assert "test_sends_enabled" not in read.model_dump()
    # The row itself is left as it was.
    assert row == {"send_mode": mode, "live_units": units, "test_sends_enabled": test_sends}


def test_productions_row_today_sends_nothing_by_itself():
    today = ShowingBookingSettings.model_validate(
        {
            "send_mode": "capture",
            "live_units": [],
            "test_sends_enabled": False,
            "lead_agent_uses_open_times": True,
            "public_booking_enabled": True,
            "public_limits": {
                "upcoming_per_mobile": 2,
                "upcoming_per_email": 2,
                "bookings_per_ip_per_day": 6,
                "bookings_per_day": 50,
            },
        }
    )

    assert (today.send_mode, today.live_units, today.live_all_units) == ("capture", [], False)
    assert today.lead_agent_uses_open_times and today.public_booking_enabled
    for addresses, _ in REACHES:
        assert run(today, addresses=addresses).action == "capture"
    assert unit_open(today, UNIT)


def test_the_defaults_capture_with_nothing_live():
    defaults = ShowingBookingSettings()
    assert (defaults.send_mode, defaults.live_units, defaults.live_all_units) == (
        "capture",
        [],
        False,
    )


@pytest.mark.parametrize(
    "row",
    [
        {"send_mode": "loud"},
        {"live_all_units": "yes"},
        {"live_units": [" "]},
        {"surprise": True},
        ["not", "a", "row"],
    ],
)
def test_a_row_that_does_not_read_is_refused(row):
    with pytest.raises(ValidationError):
        ShowingBookingSettings.model_validate(row)


# Which units may be booked


def test_only_live_mode_closes_a_unit():
    for mode in ("test", "capture"):
        assert unit_open(settings(mode, []), UNIT)
        assert unit_open(settings(mode, [OTHER_UNIT]), UNIT)
    assert unit_open(settings("live", [UNIT]), UNIT)
    assert unit_open(settings("live", ["12-a"]), " 12-A ")
    assert not unit_open(settings("live", [UNIT]), OTHER_UNIT)
    assert not unit_open(settings("live", []), UNIT)
    assert unit_open(settings("live", [], all_units=True), "a unit added later")


def test_a_unit_is_live_by_its_tick_or_by_all_units():
    assert unit_is_live(settings("capture", ["12-a"]), "12-A")
    assert not unit_is_live(settings("live", ["12-a"]), UNIT)
    assert unit_is_live(settings("live", [], all_units=True), UNIT)
