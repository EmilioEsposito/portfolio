"""
Live integration tests for the Quo (OpenPhone) toolset.

These tests hit the real OpenPhone API. They require OPEN_PHONE_API_KEY
to be set and will be skipped otherwise.

Run with:
    pytest -m live api/src/tests/test_openphone_tools.py -v -s
"""

import os

import httpx
import pytest
import pytest_asyncio
from dotenv import find_dotenv, load_dotenv

load_dotenv(find_dotenv(".env"), override=False)

from api.src.sernia_ai.tools.openphone_tools import (
    _find_contact_by_phone,
    _get_all_contacts,
    _invalidate_contact_cache,
)
from api.src.utils.fuzzy_json import fuzzy_filter

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        not os.environ.get("OPEN_PHONE_API_KEY"),
        reason="OPEN_PHONE_API_KEY not set",
    ),
]


@pytest_asyncio.fixture
async def quo_client():
    """Async httpx client configured for the OpenPhone API."""
    _invalidate_contact_cache()
    async with httpx.AsyncClient(
        base_url="https://api.openphone.com",
        headers={"Authorization": os.environ["OPEN_PHONE_API_KEY"]},
        timeout=30,
    ) as client:
        yield client


# ---- Cached contact loading ----


@pytest.mark.asyncio
async def test_get_all_contacts_loads_all_pages(quo_client: httpx.AsyncClient):
    """Cache should load ALL contacts across pages (>50)."""
    contacts = await _get_all_contacts(quo_client)
    print(f"\nLoaded {len(contacts)} contacts")
    assert len(contacts) > 50, "Expected >50 contacts (pagination test)"


@pytest.mark.asyncio
async def test_cache_reuses_on_second_call(quo_client: httpx.AsyncClient):
    """Second call should return cached data without hitting the API."""
    first = await _get_all_contacts(quo_client)
    second = await _get_all_contacts(quo_client)
    assert first is second, "Expected same list object (cache hit)"


# ---- Generic fuzzy search (via fuzzy_json) ----


@pytest.mark.asyncio
async def test_search_exact_name(quo_client: httpx.AsyncClient):
    """Exact first name should return a match."""
    contacts = await _get_all_contacts(quo_client)
    for c in contacts:
        name = c.get("defaultFields", {}).get("firstName")
        if name and len(name) > 2:
            results = fuzzy_filter(contacts, name)
            print(f"\nSearch '{name}' → {len(results)} results")
            assert len(results) > 0
            best = results[0][0]
            assert best["defaultFields"].get("firstName") == name
            return
    pytest.skip("No contacts with first names found")


@pytest.mark.asyncio
async def test_search_typo_tolerance(quo_client: httpx.AsyncClient):
    """A query with a typo should still return the correct contact."""
    contacts = await _get_all_contacts(quo_client)
    for c in contacts:
        first = (c.get("defaultFields", {}).get("firstName") or "").strip()
        if len(first) >= 5:
            # Double a letter to create a typo: "Emilio" → "Emmilio"
            typo = first[:2] + first[1] + first[2:]
            results = fuzzy_filter(contacts, typo)
            names = [c["defaultFields"].get("firstName") for c, _ in results[:5]]
            print(f"\nSearch '{typo}' (typo of '{first}') → {names}")
            assert first in names, f"Expected '{first}' in results for typo '{typo}'"
            return
    pytest.skip("No contacts with long enough first names")


@pytest.mark.asyncio
async def test_search_by_phone_digits(quo_client: httpx.AsyncClient):
    """Searching by partial phone digits should match."""
    contacts = await _get_all_contacts(quo_client)
    for c in contacts:
        phones = c.get("defaultFields", {}).get("phoneNumbers", [])
        if phones and phones[0].get("value"):
            phone = phones[0]["value"]
            digits = "".join(ch for ch in phone if ch.isdigit())[-7:]
            results = fuzzy_filter(contacts, digits)
            print(f"\nSearch '{digits}' → {len(results)} results")
            assert len(results) > 0, f"Expected match for digits '{digits}'"
            return
    pytest.skip("No contacts with phone numbers")


@pytest.mark.asyncio
async def test_search_by_company(quo_client: httpx.AsyncClient):
    """Searching by company name should return matching contacts."""
    contacts = await _get_all_contacts(quo_client)
    results = fuzzy_filter(contacts, "Sernia Capital", top_n=10)
    print(f"\nSearch 'Sernia Capital' → {len(results)} results")
    for contact, score in results[:5]:
        df = contact.get("defaultFields", {})
        name = f"{df.get('firstName', '')} {df.get('lastName', '')}".strip()
        print(f"  {name} (company={df.get('company')}, score={score})")
    assert len(results) > 0


@pytest.mark.asyncio
async def test_search_no_results(quo_client: httpx.AsyncClient):
    """A nonsense query should return no results."""
    contacts = await _get_all_contacts(quo_client)
    results = fuzzy_filter(contacts, "zzxqwplmk")
    assert len(results) == 0


# ---- fuzzy_filter on arbitrary JSON (unit tests, no API) ----


def test_fuzzy_filter_nested_objects():
    """Should match strings nested deep in objects."""
    items = [
        {"name": "Alice", "meta": {"tags": ["engineering", "python"]}},
        {"name": "Bob", "meta": {"tags": ["marketing"]}},
        {"name": "Charlie", "meta": {"tags": ["engineering", "rust"]}},
    ]
    results = fuzzy_filter(items, "python")
    assert len(results) == 1
    assert results[0][0]["name"] == "Alice"


def test_fuzzy_filter_returns_original_json():
    """Returned items should be the original dicts, unmodified."""
    items = [{"id": 1, "title": "Hello world"}, {"id": 2, "title": "Goodbye"}]
    results = fuzzy_filter(items, "hello")
    assert len(results) == 1
    assert results[0][0] is items[0]  # same object reference


def test_fuzzy_filter_top_n():
    """Should respect top_n limit."""
    items = [{"name": f"test_{i}"} for i in range(20)]
    results = fuzzy_filter(items, "test", top_n=3)
    assert len(results) == 3


# ---- _find_contact_by_phone (uses cache) ----


@pytest.mark.asyncio
async def test_find_contact_known_number(quo_client: httpx.AsyncClient):
    """Look up a known contact by E.164 phone number."""
    contacts = await _get_all_contacts(quo_client)
    target_phone = None
    target_name = None
    for c in contacts:
        df = c.get("defaultFields", {})
        phones = df.get("phoneNumbers", [])
        if phones and phones[0].get("value"):
            target_phone = phones[0]["value"]
            target_name = f"{df.get('firstName', '')} {df.get('lastName', '')}".strip()
            break

    assert target_phone, "No contacts with phone numbers found"
    contact = await _find_contact_by_phone(quo_client, target_phone)
    assert contact is not None
    found_name = (
        f"{contact['defaultFields'].get('firstName', '')} "
        f"{contact['defaultFields'].get('lastName', '')}"
    ).strip()
    assert found_name == target_name


@pytest.mark.asyncio
async def test_find_contact_nonexistent_number(quo_client: httpx.AsyncClient):
    """A phone number not in contacts should return None."""
    contact = await _find_contact_by_phone(quo_client, "+19999999999")
    assert contact is None


# ---- send_message API contract ----


@pytest.mark.asyncio
async def test_send_message_api_contract_single(quo_client: httpx.AsyncClient):
    """Verify the POST /v1/messages payload shape with a single recipient (dry-run)."""
    resp = await quo_client.post(
        "/v1/messages",
        json={
            "content": "pytest contract test — should fail",
            "from": "INVALID_PHONE_ID",
            "to": ["+10000000000"],
        },
    )
    assert resp.status_code in (400, 404, 422)


@pytest.mark.asyncio
async def test_send_message_api_contract_group(quo_client: httpx.AsyncClient):
    """Verify the POST /v1/messages payload accepts multiple recipients (dry-run)."""
    resp = await quo_client.post(
        "/v1/messages",
        json={
            "content": "pytest group test — should fail",
            "from": "INVALID_PHONE_ID",
            "to": ["+10000000000", "+10000000001"],
        },
    )
    assert resp.status_code in (400, 404, 422)


# ---- Internal vs external contact classification ----


@pytest.mark.asyncio
async def test_internal_contacts_have_sernia_company(quo_client: httpx.AsyncClient):
    """Contacts with company='Sernia Capital LLC' should be classifiable as internal."""
    from api.src.sernia_ai.config import QUO_INTERNAL_COMPANY

    contacts = await _get_all_contacts(quo_client)
    internal = [
        c for c in contacts
        if (c.get("defaultFields", {}).get("company") or "") == QUO_INTERNAL_COMPANY
    ]
    print(f"\nInternal contacts ({QUO_INTERNAL_COMPANY}): {len(internal)}")
    for c in internal[:5]:
        df = c.get("defaultFields", {})
        name = f"{df.get('firstName', '')} {df.get('lastName', '')}".strip()
        print(f"  {name}")
    assert len(internal) > 0, "Expected at least one internal contact"
