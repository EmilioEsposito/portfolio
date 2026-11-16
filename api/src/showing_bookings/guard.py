"""Whether a notice really goes out, decided by one pure function.

A notice is sent only when the send mode is "live" and either

(a) this is production and the showing's unit is one of the live units, or
(b) test sends are switched on and every address the notice reaches is a
    test recipient.

Send mode "off" skips the notice. Everything else is captured: rendered and
kept on its row, with no provider called. Outside production only (b) can
send, whatever the settings row says, because a preview's database is a copy
of production's.

Everything the decision depends on is an argument, so each branch is tested
without a database, a clock or environment variables.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

from api.src.showing_bookings.schema import normalize_email, normalize_us_mobile
from api.src.showing_bookings.settings import ShowingBookingSettings

Action = Literal["skip", "capture", "send"]
Reason = Literal[
    "send_mode_off",
    "send_mode_capture",
    "live_unit",
    "test_recipient",
    "unit_not_live",
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


def decide(
    *,
    settings: ShowingBookingSettings,
    environment: str,
    unit: str,
    addresses: Iterable[str],
    test_recipients: frozenset[str],
) -> Decision:
    """Skip, capture or send one notice.

    ``addresses`` is everyone the notice would reach: the phone number of a
    text, or every guest of a calendar event, so an invite that also names
    someone outside the test list is never a test send.
    """
    if settings.send_mode == "off":
        return Decision("skip", "send_mode_off")
    if settings.send_mode == "capture":
        return Decision("capture", "send_mode_capture")
    production = environment == PRODUCTION
    live_units = {live.casefold() for live in settings.live_units}
    if production and unit.strip().casefold() in live_units:
        return Decision("send", "live_unit")
    if settings.test_sends_enabled and is_test_send(addresses, test_recipients):
        return Decision("send", "test_recipient")
    return Decision("capture", "unit_not_live" if production else "not_production")


def would_send(
    *,
    settings: ShowingBookingSettings,
    environment: str,
    unit: str,
    addresses: Iterable[str],
    test_recipients: frozenset[str],
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
        ).action
        == "send"
    )
