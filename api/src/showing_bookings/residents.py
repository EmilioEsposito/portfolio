"""Who lives in a unit: the residents a new showing's text goes to.

The team's Quo contacts carry "Property", "Unit #", "Lease Start Date" and
"Lease End Date". A unit's residents are the contacts whose Property and
Unit # match the listing's unit key (``320-03`` is Property ``320``, Unit #
``03``) and whose lease is active on the day of the showing, never an
internal contact. The field rules are the Sernia AI tenant tools' own
(``open_phone/tenants.py``), so both agree on who is a current tenant.

Where the contacts come from (``contact_source``):

- production: ``quo_contacts.QuoContacts``, the team's contacts through the
  shared contact store, cached for five minutes;
- everywhere else: ``fakes.StandInContacts`` with made-up residents on
  555-01xx numbers for every unit, the way the sample calendar stands in for
  Google, so no real resident's number is ever stored in a preview or a
  local database. Tests install their own.

``find`` never raises for a lookup that goes wrong: it answers ``Found`` with
a ``problem`` the team sees on the showing, and the booking goes ahead with
no resident text. No residents and no problem means the unit is vacant.

Resident texts go out only in the daytime, 8 AM to 9 PM in the showing rules'
time zone (``next_daytime``); the outbox applies it when one is due, and
writes the text again then (``text_for``), from the showing and the wording as
they are when it goes.
"""

import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Protocol
from zoneinfo import ZoneInfo

import logfire

from api.src.open_phone.tenants import (
    filter_tenants_by_property_unit,
    get_contact_unit,
    has_active_lease,
    is_internal_contact,
    parse_lease_date,
)
from api.src.showing_bookings import wording
from api.src.showing_bookings.fakes import StandInContacts
from api.src.showing_bookings.models import Showing
from api.src.showing_bookings.quo_contacts import LiveQuoInTests, QuoContacts
from api.src.showing_bookings.schema import normalize_us_mobile
from api.src.showing_bookings.settings import current_environment

NEW_SHOWING_KIND = "resident_new_showing"
CANCELLED_KIND = "resident_cancelled"
TEXT_KINDS = frozenset({NEW_SHOWING_KIND, CANCELLED_KIND})
# One notice per showing for all of a unit's residents: one group text.
RECIPIENT_KEY = "residents"
# Quo's most recipients in one message (the Sernia AI's GROUP_SMS_MAX_RECIPIENTS).
MAX_IN_ONE_TEXT = 10

DAY_STARTS = time(8, 0)
DAY_ENDS = time(21, 0)

# Why a showing's residents could not be told, as ``showings.resident_attention``.
PROBLEMS = {
    "lookup_failed": "Could not read who lives here: tell the resident yourself",
    "lease_dates_missing": (
        "A contact at this unit has no lease dates: check who lives here and tell them yourself"
    ),
    "unit_key_unreadable": "The unit key does not read as property-unit: tell the resident yourself",
    "unit_spelled_differently": (
        "A current tenant's contact spells this unit differently: fix the contact, "
        "then tell the resident yourself"
    ),
    "resident_without_mobile": "A current tenant has no mobile on their contact: tell them yourself",
    "too_many_residents": "More residents than one group text takes: tell them yourself",
}

# Contacts that name a unit without living there: a Lead carries the unit it
# asked about, and none of these has lease dates (the quo-contact skill's roles).
NOT_RESIDENT_ROLES = frozenset({"lead", "prior tenant", "vendor", "partner"})

SHOWING_CANCELLED_NOTE = "Not sent: the showing was cancelled."
TOO_LATE_NOTE = "Not sent: the showing's time had passed before it was daytime."
NEVER_TOLD_NOTE = (
    "Not sent: the residents' text about this showing never went out, "
    "so there is nothing to cancel."
)


def text_for(kind: str, showing: Showing, timezone: str) -> str:
    """The words of a resident text of ``kind`` about ``showing`` alone, with its date
    and time in ``timezone``, as it is queued. It is written again when it goes,
    with the unit's other showings (``digests.py``)."""
    visit = wording.Visit(
        unit=showing.unit,
        address=showing.address,
        starts_at=showing.starts_at,
        ends_at=showing.ends_at,
        timezone=timezone,
    )
    if kind == NEW_SHOWING_KIND:
        return wording.resident_new_showing_text(visit)
    if kind == CANCELLED_KIND:
        return wording.resident_text(upcoming=[], cancelled=[visit])
    raise ValueError(f"not a resident text: {kind}")


class ContactSource(Protocol):
    async def contacts(self, unit: str) -> list[dict]:
        """Contacts as Quo's API returns them, including every one at ``unit``.

        May return more; the shared rules pick the unit's residents.
        """
        ...


@dataclass(frozen=True)
class Found:
    """A unit's residents on one day: their mobiles in E.164, or why they are unknown."""

    mobiles: tuple[str, ...] = ()
    # A key of ``PROBLEMS``; when set, no resident text is queued.
    problem: str | None = None


_SAMPLES = StandInContacts(samples=True)


def contact_source() -> ContactSource:
    """Quo's contacts in production; made-up residents everywhere else. Tests replace it."""
    if current_environment() == "production":
        return QuoContacts()
    return _SAMPLES


def split_unit(unit: str) -> tuple[str, str] | None:
    """``("320", "03")`` for ``320-03``: the property before the last hyphen."""
    prop, hyphen, number = unit.strip().rpartition("-")
    if not hyphen or not prop.strip() or not number.strip():
        return None
    return prop.strip(), number.strip()


def _loose(value: str) -> str:
    """A unit or property spelled loosely: no case, spaces, "#" or "unit", no leading zeros."""
    text = re.sub(r"(?i)\bunit\b|[#\s.]", "", value).casefold()
    return text.lstrip("0") or text


def _not_a_resident(contact: dict) -> bool:
    """Staff, or a role that names a unit without living there (``NOT_RESIDENT_ROLES``)."""
    role = (contact.get("defaultFields", {}).get("role") or "").strip().casefold()
    return is_internal_contact(contact) or role in NOT_RESIDENT_ROLES


def _lacks_lease_dates(contact: dict, prop: str, number: str) -> bool:
    """A contact that could be a resident, naming exactly this unit, without a readable
    lease start or end date. Dates that read but are not current (a past or future
    tenant) are not missing. A Lead, Prior Tenant, Vendor or Partner has no lease
    dates to miss."""
    if get_contact_unit(contact) != (prop, number) or _not_a_resident(contact):
        return False
    dates = {field.get("name"): field.get("value") for field in contact.get("customFields", [])}
    return any(
        parse_lease_date(dates.get(name)) is None for name in ("Lease Start Date", "Lease End Date")
    )


def residents_in(contacts: list[dict], unit: str, day: date) -> Found:
    """The residents of ``unit`` on ``day``, by the shared tenant rules. Pure."""
    parts = split_unit(unit)
    if parts is None:
        return Found(problem="unit_key_unreadable")
    prop, number = parts
    grouped = filter_tenants_by_property_unit(contacts, [prop], [number], today=day)
    found = grouped.get((prop, number), [])
    if not found:
        # Vacant, unless a contact at this unit has no readable lease dates: the
        # unit may well be lived in, so the team checks rather than no one told.
        if any(_lacks_lease_dates(contact, prop, number) for contact in contacts):
            return Found(problem="lease_dates_missing")
        # Or unless a current tenant's contact names this unit another way
        # ("3" for "03"): then the lookup is wrong, not the unit empty.
        for contact in contacts:
            other = get_contact_unit(contact)
            if (
                other is not None
                and other != (prop, number)
                and (_loose(other[0]), _loose(other[1])) == (_loose(prop), _loose(number))
                and not is_internal_contact(contact)
                and has_active_lease(contact, day)
            ):
                return Found(problem="unit_spelled_differently")
        return Found()
    mobiles: list[str] = []
    for contact in found:
        numbers = contact.get("defaultFields", {}).get("phoneNumbers") or []
        first = normalize_us_mobile((numbers[0].get("value") or "") if numbers else "")
        if first is None:
            return Found(problem="resident_without_mobile")
        if first not in mobiles:
            mobiles.append(first)
    if len(mobiles) > MAX_IN_ONE_TEXT:
        return Found(problem="too_many_residents")
    return Found(mobiles=tuple(sorted(mobiles)))


async def find(unit: str, day: date, *, source: ContactSource | None = None) -> Found:
    """The residents of ``unit`` on ``day``. Never raises for a failed lookup."""
    try:
        contacts = await (source or contact_source()).contacts(unit)
    except LiveQuoInTests:
        raise
    except Exception as exc:
        logfire.warn("showing resident lookup failed", unit=unit, error=type(exc).__name__)
        return Found(problem="lookup_failed")
    found = residents_in(contacts, unit, day)
    if found.problem:
        logfire.warn("showing residents unknown", unit=unit, problem=found.problem)
    return found


def next_daytime(now: datetime, timezone: str) -> datetime | None:
    """None from 8 AM up to 9 PM in ``timezone``; otherwise the next 8 AM there."""
    zone = ZoneInfo(timezone)
    local = now.astimezone(zone)
    if DAY_STARTS <= local.time() < DAY_ENDS:
        return None
    day = local.date() if local.time() < DAY_STARTS else local.date() + timedelta(days=1)
    return datetime.combine(day, DAY_STARTS, zone)
