"""Whether a notice really goes out, decided by one pure function.

Outside production a notice is sent only when every address it reaches is on
the test list, and only in test mode or when a person approved it. That holds
whatever the settings row says, because a preview's database is a copy of
production's, and it is decided first, so nothing after it can send more.

In production:

- A notice a person approved for sending (``captured.py``) goes, in any mode.
- test: goes when every address it reaches is on the test list.
- capture: never goes.
- live: goes when the showing's unit is live (ticked, or all units).

Everything that does not go is captured: rendered and kept on its row, with no
provider called.

Everything the decision depends on is an argument, so each branch is tested
without a database, a clock or environment variables.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

from api.src.showing_bookings.schema import normalize_email, normalize_us_mobile
from api.src.showing_bookings.settings import ShowingBookingSettings, unit_is_live

Action = Literal["capture", "send"]
Reason = Literal[
    "send_mode_capture",
    "live_unit",
    "all_units_live",
    "test_recipient",
    "approved",
    "unit_not_live",
    "not_test_recipient",
    "not_production",
]

PRODUCTION = "production"


@dataclass(frozen=True)
class Decision:
    action: Action
    # Saved on the notice as ``guard_reason``, so the team can see why.
    reason: Reason


def _normalized(address: str) -> str | None:
    return normalize_us_mobile(address) if "@" not in address else normalize_email(address)


def is_test_send(addresses: Iterable[str], test_recipients: frozenset[str]) -> bool:
    """True when there is at least one address and every one is a test recipient."""
    normalized = [_normalized(address) for address in addresses]
    return bool(normalized) and all(
        address is not None and address in test_recipients for address in normalized
    )


def may_reach(
    addresses: Iterable[str], *, environment: str, test_recipients: frozenset[str]
) -> bool:
    """Whether a message may reach ``addresses`` at all in ``environment``:
    always in production, elsewhere only when every address is on the test list.

    The first rule of ``decide``, and the whole rule for the lead agent's emails
    (``zillow_leads.handle.captures_lead_email``), which have no send mode: a
    preview's lead threads are copies of real people's.
    """
    return environment == PRODUCTION or is_test_send(addresses, test_recipients)


def decide(
    *,
    settings: ShowingBookingSettings,
    environment: str,
    unit: str,
    addresses: Iterable[str],
    test_recipients: frozenset[str],
    approved: bool = False,
) -> Decision:
    """Capture or send one notice.

    ``addresses`` is everyone the notice would reach: the phone number of a
    text, or every guest of a calendar event, so an invite that also names
    someone outside the test list is never a test send. ``approved``: a person
    sent this captured notice by hand.
    """
    addresses = list(addresses)
    to_test_list = is_test_send(addresses, test_recipients)
    mode = settings.send_mode
    if not may_reach(addresses, environment=environment, test_recipients=test_recipients):
        # Nobody but the test list, ever; the reason only says why.
        if mode == "live" or approved:
            return Decision("capture", "not_production")
        return Decision("capture", "not_test_recipient" if mode == "test" else "send_mode_capture")
    if approved:
        return Decision("send", "approved" if environment == PRODUCTION else "test_recipient")
    if mode == "test":
        if to_test_list:
            return Decision("send", "test_recipient")
        return Decision("capture", "not_test_recipient")
    if mode == "live":
        if environment != PRODUCTION:
            return Decision("capture", "not_production")
        if unit_is_live(settings, unit):
            return Decision("send", "all_units_live" if settings.live_all_units else "live_unit")
        return Decision("capture", "unit_not_live")
    return Decision("capture", "send_mode_capture")


def would_send(
    *,
    settings: ShowingBookingSettings,
    environment: str,
    unit: str,
    addresses: Iterable[str],
    test_recipients: frozenset[str],
    approved: bool = False,
) -> bool:
    """Whether a notice for ``unit`` reaching ``addresses`` would really be sent now:
    exactly when ``decide`` answers send. For a notice that lists several units'
    showings (the host's morning text), so it lists only what the guard lets through."""
    return (
        decide(
            settings=settings,
            environment=environment,
            unit=unit,
            addresses=addresses,
            test_recipients=test_recipients,
            approved=approved,
        ).action
        == "send"
    )
