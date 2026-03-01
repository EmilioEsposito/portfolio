"""
Unit tests for the cross-unit group SMS gate in openphone_tools.

These are pure unit tests — no API calls, no live marker.

⚠️  SMS SAFETY: All external SMS tests must mock the send call. NEVER send
real SMS to external contacts (tenants, vendors, etc.) from tests. See
CLAUDE.md "SMS test safety" for details.
"""

from api.src.sernia_ai.tools.openphone_tools import (
    _filter_tenants_by_property_unit,
    _get_contact_unit,
)


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
    return _make_contact(
        [{"name": "Property", "value": prop}, {"name": "Unit #", "value": unit}],
        first=first, last="T", phone=phone,
    )


def _internal(first: str, prop: str, unit: str) -> dict:
    return _make_contact(
        [{"name": "Property", "value": prop}, {"name": "Unit #", "value": unit}],
        first=first, last="I", company="Sernia Capital LLC",
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
