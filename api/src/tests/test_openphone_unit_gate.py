"""
Unit tests for the cross-unit group SMS gate in quo_tools.

These are pure unit tests — no API calls, no live marker.

⚠️  SMS SAFETY: All external SMS tests must mock the send call. NEVER send
real SMS to external contacts (tenants, vendors, etc.) from tests. See
CLAUDE.md "SMS test safety" for details.
"""

from datetime import date, timedelta

from api.src.sernia_ai.tools.quo_tools import (
    _filter_tenants_by_property_unit,
    _get_contact_unit,
    _has_active_lease,
)

# A lease window that comfortably brackets "today" so tests are date-stable.
_ACTIVE_START = (date.today() - timedelta(days=30)).isoformat()
_ACTIVE_END = (date.today() + timedelta(days=30)).isoformat()
_ACTIVE_LEASE_FIELDS = [
    {"name": "Lease Start Date", "value": _ACTIVE_START},
    {"name": "Lease End Date", "value": _ACTIVE_END},
]


def _make_contact(
    custom_fields: list[dict] | None = None,
    *,
    first: str = "Test",
    last: str = "User",
    company: str | None = None,
    phone: str | None = None,
) -> dict:
    """Build a minimal contact dict for unit tests."""
    default_fields: dict = {"firstName": first, "lastName": last}
    if company is not None:
        default_fields["company"] = company
    if phone is not None:
        default_fields["phoneNumbers"] = [{"value": phone}]
    contact: dict = {"defaultFields": default_fields}
    if custom_fields is not None:
        contact["customFields"] = custom_fields
    return contact


class TestGetContactUnit:
    def test_extracts_property_and_unit(self):
        contact = _make_contact([
            {"name": "Property", "value": "320"},
            {"name": "Unit #", "value": "02"},
        ])
        assert _get_contact_unit(contact) == ("320", "02")

    def test_returns_none_when_property_missing(self):
        contact = _make_contact([{"name": "Unit #", "value": "02"}])
        assert _get_contact_unit(contact) is None

    def test_returns_none_when_unit_missing(self):
        contact = _make_contact([{"name": "Property", "value": "320"}])
        assert _get_contact_unit(contact) is None

    def test_returns_none_for_empty_custom_fields(self):
        contact = _make_contact([])
        assert _get_contact_unit(contact) is None

    def test_returns_none_when_no_custom_fields_key(self):
        contact = {"defaultFields": {"firstName": "Vendor"}}
        assert _get_contact_unit(contact) is None

    def test_strips_whitespace(self):
        contact = _make_contact([
            {"name": "Property", "value": " 320 "},
            {"name": "Unit #", "value": " 02 "},
        ])
        assert _get_contact_unit(contact) == ("320", "02")

    def test_returns_none_for_empty_value(self):
        contact = _make_contact([
            {"name": "Property", "value": "320"},
            {"name": "Unit #", "value": ""},
        ])
        assert _get_contact_unit(contact) is None


# -- Helpers for filter tests --

def _tenant(first: str, prop: str, unit: str, phone: str = "+10000000000") -> dict:
    # Active-lease tenant by default — the filter defaults to active_only.
    return _make_contact(
        [
            {"name": "Property", "value": prop},
            {"name": "Unit #", "value": unit},
            *_ACTIVE_LEASE_FIELDS,
        ],
        first=first, last="T", phone=phone,
    )


def _internal(first: str, prop: str, unit: str) -> dict:
    return _make_contact(
        [
            {"name": "Property", "value": prop},
            {"name": "Unit #", "value": unit},
            *_ACTIVE_LEASE_FIELDS,
        ],
        first=first, last="I", company="Sernia Capital LLC",
    )


def _lead(first: str, prop: str, unit: str, phone: str = "+10000000000") -> dict:
    """A lead/prospect — Property/Unit set but no lease dates."""
    return _make_contact(
        [{"name": "Property", "value": prop}, {"name": "Unit #", "value": unit}],
        first=first, last="L", phone=phone,
    )


def _future_tenant(first: str, prop: str, unit: str, phone: str = "+10000000000") -> dict:
    """A signed-but-not-started tenant — lease starts in the future."""
    return _make_contact(
        [
            {"name": "Property", "value": prop},
            {"name": "Unit #", "value": unit},
            {"name": "Lease Start Date", "value": (date.today() + timedelta(days=15)).isoformat()},
            {"name": "Lease End Date", "value": (date.today() + timedelta(days=380)).isoformat()},
        ],
        first=first, last="F", phone=phone,
    )


class TestFilterTenantsByPropertyUnit:
    def test_single_property_all_units(self):
        contacts = [
            _tenant("Alice", "320", "02"),
            _tenant("Bob", "320", "04"),
            _tenant("Carol", "400", "01"),
        ]
        result = _filter_tenants_by_property_unit(contacts, ["320"], None)
        assert set(result.keys()) == {("320", "02"), ("320", "04")}
        assert len(result[("320", "02")]) == 1
        assert len(result[("320", "04")]) == 1

    def test_property_with_specific_units(self):
        contacts = [
            _tenant("Alice", "320", "02"),
            _tenant("Bob", "320", "04"),
            _tenant("Carol", "320", "06"),
        ]
        result = _filter_tenants_by_property_unit(contacts, ["320"], ["02", "04"])
        assert set(result.keys()) == {("320", "02"), ("320", "04")}

    def test_multiple_properties(self):
        contacts = [
            _tenant("Alice", "320", "02"),
            _tenant("Bob", "400", "01"),
            _tenant("Carol", "500", "01"),
        ]
        result = _filter_tenants_by_property_unit(contacts, ["320", "400"], None)
        assert set(result.keys()) == {("320", "02"), ("400", "01")}

    def test_skips_contacts_without_unit_fields(self):
        contacts = [
            _tenant("Alice", "320", "02"),
            _make_contact(first="Vendor", last="V"),  # no custom fields
        ]
        result = _filter_tenants_by_property_unit(contacts, ["320"], None)
        assert set(result.keys()) == {("320", "02")}

    def test_skips_internal_contacts(self):
        contacts = [
            _tenant("Alice", "320", "02"),
            _internal("Emilio", "320", "02"),
        ]
        result = _filter_tenants_by_property_unit(contacts, ["320"], None)
        assert len(result[("320", "02")]) == 1
        assert result[("320", "02")][0]["defaultFields"]["firstName"] == "Alice"

    def test_returns_empty_when_no_matches(self):
        contacts = [
            _tenant("Alice", "320", "02"),
        ]
        result = _filter_tenants_by_property_unit(contacts, ["999"], None)
        assert result == {}

    def test_groups_roommates_together(self):
        contacts = [
            _tenant("Alice", "320", "02", "+11111111111"),
            _tenant("Bob", "320", "02", "+12222222222"),
        ]
        result = _filter_tenants_by_property_unit(contacts, ["320"], None)
        assert len(result[("320", "02")]) == 2


class TestHasActiveLease:
    def test_active_when_today_within_window(self):
        contact = _make_contact(_ACTIVE_LEASE_FIELDS)
        assert _has_active_lease(contact) is True

    def test_inactive_when_no_lease_dates(self):
        # A lead/prospect — no lease custom fields.
        contact = _make_contact([{"name": "Property", "value": "320"}])
        assert _has_active_lease(contact) is False

    def test_inactive_when_lease_not_started(self):
        contact = _make_contact([
            {"name": "Lease Start Date", "value": (date.today() + timedelta(days=10)).isoformat()},
            {"name": "Lease End Date", "value": (date.today() + timedelta(days=375)).isoformat()},
        ])
        assert _has_active_lease(contact) is False

    def test_inactive_when_lease_ended(self):
        contact = _make_contact([
            {"name": "Lease Start Date", "value": (date.today() - timedelta(days=400)).isoformat()},
            {"name": "Lease End Date", "value": (date.today() - timedelta(days=10)).isoformat()},
        ])
        assert _has_active_lease(contact) is False

    def test_active_on_boundary_dates(self):
        today = date.today().isoformat()
        contact = _make_contact([
            {"name": "Lease Start Date", "value": today},
            {"name": "Lease End Date", "value": today},
        ])
        assert _has_active_lease(contact) is True

    def test_handles_full_iso_timestamp(self):
        # Quo 'date' fields can carry a time component / offset.
        contact = _make_contact([
            {"name": "Lease Start Date", "value": f"{(date.today() - timedelta(days=1)).isoformat()}T16:00:00.000+0000"},
            {"name": "Lease End Date", "value": f"{(date.today() + timedelta(days=1)).isoformat()}T16:00:00.000+0000"},
        ])
        assert _has_active_lease(contact) is True

    def test_inactive_when_dates_unparseable(self):
        contact = _make_contact([
            {"name": "Lease Start Date", "value": "not-a-date"},
            {"name": "Lease End Date", "value": "also-bad"},
        ])
        assert _has_active_lease(contact) is False


class TestActiveLeaseFiltering:
    """The bug fix: mass_text must default to active-lease tenants only."""

    def test_excludes_leads_by_default(self):
        contacts = [
            _tenant("Aidan", "659", "02"),
            _lead("Mary", "659", "02"),
        ]
        result = _filter_tenants_by_property_unit(contacts, ["659"], ["02"])
        names = {c["defaultFields"]["firstName"] for c in result[("659", "02")]}
        assert names == {"Aidan"}

    def test_excludes_future_tenants_by_default(self):
        contacts = [
            _tenant("Aidan", "659", "02"),
            _future_tenant("Incoming", "659", "02"),
        ]
        result = _filter_tenants_by_property_unit(contacts, ["659"], ["02"])
        names = {c["defaultFields"]["firstName"] for c in result[("659", "02")]}
        assert names == {"Aidan"}

    def test_include_inactive_keeps_everyone(self):
        contacts = [
            _tenant("Aidan", "659", "02"),
            _lead("Mary", "659", "02"),
            _future_tenant("Incoming", "659", "02"),
        ]
        result = _filter_tenants_by_property_unit(
            contacts, ["659"], ["02"], active_only=False
        )
        names = {c["defaultFields"]["firstName"] for c in result[("659", "02")]}
        assert names == {"Aidan", "Mary", "Incoming"}

    def test_unit_with_only_inactive_contacts_drops_out(self):
        contacts = [
            _tenant("Aidan", "659", "02"),
            _lead("Mary", "659", "04"),
            _future_tenant("Incoming", "659", "04"),
        ]
        result = _filter_tenants_by_property_unit(contacts, ["659"], None)
        assert set(result.keys()) == {("659", "02")}

    def test_reproduces_reported_bug_scenario(self):
        # Two units, each with current tenants + leads + future tenants.
        # Only the current (active-lease) tenants should be messaged.
        contacts = [
            # 659-02 current tenants
            _tenant("Aidan Kreiley", "659", "02", "+1659020001"),
            _tenant("Adeline Farrington", "659", "02", "+1659020002"),
            # 659-02 noise
            _lead("Mary Allerton", "659", "02", "+1659020003"),
            _future_tenant("Elena Coupas", "659", "02", "+1659020004"),
            # 659-04 current tenants
            _tenant("Alfie Ong", "659", "04", "+1659040001"),
            _tenant("Angela Ledger", "659", "04", "+1659040002"),
            # 659-04 noise
            _lead("Ava Reisman", "659", "04", "+1659040005"),
            _future_tenant("Trevor Moreci", "659", "04", "+1659040006"),
        ]
        result = _filter_tenants_by_property_unit(contacts, ["659"], ["02", "04"])
        total = sum(len(v) for v in result.values())
        assert total == 4
        assert len(result[("659", "02")]) == 2
        assert len(result[("659", "04")]) == 2
