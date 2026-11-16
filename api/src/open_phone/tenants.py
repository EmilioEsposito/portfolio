"""Who lives where, read from Quo (formerly OpenPhone) contacts' custom fields.

The team's Quo contacts carry the custom fields "Property", "Unit #",
"Lease Start Date" and "Lease End Date". These rules read them, for the Sernia
AI's tenant tools (``sernia_ai/tools/quo_tools.py``) and for the showing
bookings' resident lookup (``showing_bookings/residents.py``), so both decide
the same way who is a current tenant of a unit.

Pure functions over contact dicts as Quo's API returns them: no I/O, and
importing this module has no side effects.
"""

from collections import defaultdict
from datetime import date

from api.src.sernia_ai.config import QUO_INTERNAL_COMPANY


def get_contact_unit(contact: dict) -> tuple[str, str] | None:
    """Extract (property, unit) from a Quo contact's custom fields.

    Returns None if either field is missing (non-tenant contact).
    """
    prop = unit = None
    for field in contact.get("customFields", []):
        if field.get("name") == "Property":
            prop = (field.get("value") or "").strip()
        elif field.get("name") == "Unit #":
            unit = (field.get("value") or "").strip()
    if prop and unit:
        return (prop, unit)
    return None


def is_internal_contact(contact: dict) -> bool:
    """Check if a contact belongs to the internal company."""
    company = contact.get("defaultFields", {}).get("company") or ""
    return company == QUO_INTERNAL_COMPANY


def parse_lease_date(value) -> date | None:
    """Parse a Quo 'date' custom field value to a ``date``.

    Quo stores lease dates as ISO strings (e.g. ``2025-03-30T16:00:00.000+0000``
    or ``2025-03-30``). We only care about the calendar day, so we read the
    leading ``YYYY-MM-DD``. Returns None for missing/unparseable values.
    """
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except (ValueError, TypeError):
        return None


def has_active_lease(contact: dict, today: date | None = None) -> bool:
    """True only when the contact has a *currently active* lease.

    Reads the ``Lease Start Date`` / ``Lease End Date`` custom fields and
    returns True when ``start <= today <= end``. Contacts that are missing
    either date or whose dates don't parse — leads, prospects, and anyone
    whose lease hasn't started yet or has already ended (future / past
    tenants) — are treated as NOT active.
    """
    today = today or date.today()
    start = end = None
    for field in contact.get("customFields", []):
        name = field.get("name")
        if name == "Lease Start Date":
            start = parse_lease_date(field.get("value"))
        elif name == "Lease End Date":
            end = parse_lease_date(field.get("value"))
    if start is None or end is None:
        return False
    return start <= today <= end


def filter_tenants_by_property_unit(
    contacts: list[dict],
    properties: list[str],
    units: list[str] | None,
    *,
    active_only: bool = True,
    today: date | None = None,
) -> dict[tuple[str, str], list[dict]]:
    """Group tenant contacts by (property, unit), filtered by selectors.

    Returns dict mapping (property, unit) -> list of matching contacts.
    Skips contacts without Property/Unit # fields and internal contacts.

    When ``active_only`` is True (the default), only contacts with a
    lease active on ``today`` (default: the current date) are included —
    leads, prospects, and future / past tenants are excluded. This is the
    safe default for building-wide notices so we never text someone who
    isn't actually a current tenant. Pass ``active_only=False`` to include
    every matching contact regardless of lease status.
    """
    prop_set = {p.strip() for p in properties}
    unit_set = {u.strip() for u in units} if units is not None else None
    today = today or date.today()

    grouped: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for contact in contacts:
        cu = get_contact_unit(contact)
        if cu is None:
            continue
        if is_internal_contact(contact):
            continue
        if active_only and not has_active_lease(contact, today):
            continue
        prop, unit = cu
        if prop not in prop_set:
            continue
        if unit_set is not None and unit not in unit_set:
            continue
        grouped[(prop, unit)].append(contact)

    return dict(grouped)
