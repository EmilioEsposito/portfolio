"""
Live integration tests for the Quo (OpenPhone) toolset.

These tests hit the real OpenPhone API. They require OPEN_PHONE_API_KEY
to be set and will be skipped otherwise.

Run with:
    pytest -m live api/src/tests/test_quo_tools.py -v -s

⚠️  SMS SAFETY: NEVER add live tests that send real SMS to external contacts
(tenants, vendors, etc.). External SMS tests must ALWAYS mock the send call.
Sending real messages to tenants from tests risks confusion, lease violations,
and privacy issues. Only send_internal_sms may be tested live against real
internal team numbers. If a dedicated test phone number is provided in the
future, it will be explicitly configured here.
"""

import json
import os
import re
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from dotenv import find_dotenv, load_dotenv

load_dotenv(find_dotenv(".env"), override=False)

from api.src.open_phone.service import (
    find_contact_by_phone,
    get_all_contacts,
    invalidate_contact_cache,
)
from api.src.sernia_ai.tools.quo_tools import (
    search_contacts_impl,
    list_active_threads_impl,
    get_thread_messages_impl,
)
from api.src.utils.fuzzy_json import fuzzy_filter

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        not os.environ.get("OPEN_PHONE_API_KEY"),
        reason="OPEN_PHONE_API_KEY not set",
    ),
]

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _save_fixture(filename: str, content: str) -> None:
    """Save tool output to the gitignored fixtures directory for inspection."""
    FIXTURES_DIR.mkdir(exist_ok=True)
    # Pretty-print JSON files
    if filename.endswith(".json"):
        try:
            content = json.dumps(json.loads(content), indent=4, ensure_ascii=False)
        except (json.JSONDecodeError, TypeError):
            pass
    (FIXTURES_DIR / filename).write_text(content, encoding="utf-8")


@pytest_asyncio.fixture
async def quo_client():
    """Async httpx client configured for the OpenPhone API."""
    invalidate_contact_cache()
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
    contacts = await get_all_contacts(quo_client)
    print(f"\nLoaded {len(contacts)} contacts")
    assert len(contacts) > 50, "Expected >50 contacts (pagination test)"


@pytest.mark.asyncio
async def test_cache_reuses_on_second_call(quo_client: httpx.AsyncClient):
    """Second call should return cached data without hitting the API."""
    first = await get_all_contacts(quo_client)
    second = await get_all_contacts(quo_client)
    assert first is second, "Expected same list object (cache hit)"


# ---- Generic fuzzy search (via fuzzy_json) ----


@pytest.mark.asyncio
async def test_search_exact_name(quo_client: httpx.AsyncClient):
    """Exact first name should return a match."""
    contacts = await get_all_contacts(quo_client)
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
    contacts = await get_all_contacts(quo_client)
    for c in contacts:
        first = (c.get("defaultFields", {}).get("firstName") or "").strip()
        # Skip names with spaces/numbers (e.g. "Lead 659-03 Murong") — not real names
        if len(first) >= 5 and " " not in first and first.isalpha():
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
    contacts = await get_all_contacts(quo_client)
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
    contacts = await get_all_contacts(quo_client)
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
    contacts = await get_all_contacts(quo_client)
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


# ---- find_contact_by_phone (uses cache) ----


@pytest.mark.asyncio
async def test_find_contact_known_number(quo_client: httpx.AsyncClient):
    """Look up a known contact by E.164 phone number."""
    contacts = await get_all_contacts(quo_client)
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
    contact = await find_contact_by_phone(target_phone, quo_client)
    assert contact is not None
    found_name = (
        f"{contact['defaultFields'].get('firstName', '')} "
        f"{contact['defaultFields'].get('lastName', '')}"
    ).strip()
    assert found_name == target_name


@pytest.mark.asyncio
async def test_find_contact_nonexistent_number(quo_client: httpx.AsyncClient):
    """A phone number not in contacts should return None."""
    contact = await find_contact_by_phone("+19999999999", quo_client)
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

    contacts = await get_all_contacts(quo_client)
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


# ---- Custom retrieval tools (search_contacts, get_recent_threads, get_thread_messages) ----


@pytest.mark.asyncio
async def test_search_contacts_impl_returns_json(quo_client: httpx.AsyncClient):
    """search_contacts_impl should return a JSON string with matching contacts."""
    result = await search_contacts_impl(quo_client, "Sernia")
    _save_fixture("quo_search_contacts.json", result)
    print(f"\nsearch_contacts_impl('Sernia') → saved to fixtures/quo_search_contacts.json")
    assert isinstance(result, str)
    parsed = json.loads(result)
    assert len(parsed) > 0, "Expected at least one match for 'Sernia'"


@pytest.mark.asyncio
async def test_search_contacts_impl_no_results(quo_client: httpx.AsyncClient):
    """Nonsense query should return zero matches."""
    result = await search_contacts_impl(quo_client, "zzxqwplmk99999")
    parsed = json.loads(result)
    assert parsed["total_matches"] == 0
    assert parsed["results"] == []


@pytest.mark.asyncio
async def test_list_active_threads_impl_returns_threads(quo_client: httpx.AsyncClient):
    """list_active_threads_impl should return enriched thread listing."""
    result = await list_active_threads_impl(quo_client)
    _save_fixture("quo_active_threads.md", result)
    print(f"\nlist_active_threads_impl() → saved to fixtures/quo_active_threads.md")
    assert isinstance(result, str)
    assert "Active threads" in result or "No active" in result


@pytest.mark.asyncio
async def test_list_active_threads_impl_enriches_contact_names(quo_client: httpx.AsyncClient):
    """Thread listing should show contact names, not just raw phone numbers."""
    result = await list_active_threads_impl(quo_client)
    if "No active" in result:
        pytest.skip("No active threads to test enrichment")
    # At least one thread should have a name (not just a phone number)
    assert "(" in result, "Expected enriched names with phone in parentheses"


@pytest.mark.asyncio
async def test_get_thread_messages_impl_with_known_contact(quo_client: httpx.AsyncClient):
    """get_thread_messages_impl should return messages for a known contact."""
    contacts = await get_all_contacts(quo_client)
    target_phone = None
    for c in contacts:
        phones = c.get("defaultFields", {}).get("phoneNumbers", [])
        company = c.get("defaultFields", {}).get("company", "")
        if phones and phones[0].get("value") and company != "Sernia Capital LLC":
            target_phone = phones[0]["value"]
            break

    if not target_phone:
        pytest.skip("No external contacts with phone numbers found")

    result = await get_thread_messages_impl(quo_client, target_phone, max_results=5)
    # Sanitize phone from filename
    phone_slug = target_phone.replace("+", "").replace("-", "")
    _save_fixture(f"quo_thread_messages_{phone_slug}.md", result)
    print(f"\nget_thread_messages_impl('{target_phone}') → saved to fixtures/quo_thread_messages_{phone_slug}.md")
    assert isinstance(result, str)
    assert "SMS thread with" in result or "No messages found" in result


@pytest.mark.asyncio
async def test_get_thread_messages_impl_no_messages(quo_client: httpx.AsyncClient):
    """A fake phone number should return 'no messages found'."""
    result = await get_thread_messages_impl(quo_client, "+19999999999", max_results=5)
    assert "No messages found" in result


@pytest.mark.asyncio
async def test_get_thread_messages_impl_chronological_order(quo_client: httpx.AsyncClient):
    """Messages should be returned in chronological order (oldest first)."""
    result = await list_active_threads_impl(quo_client, max_results=1)
    if "No active" in result:
        pytest.skip("No active threads to test message order")

    phones = re.findall(r"\+\d{10,15}", result)
    if not phones:
        pytest.skip("Could not extract phone number from thread listing")

    target = phones[0]
    msg_result = await get_thread_messages_impl(quo_client, target, max_results=10)
    if "No messages found" in msg_result:
        pytest.skip("No messages for extracted phone number")

    phone_slug = target.replace("+", "").replace("-", "")
    _save_fixture(f"quo_thread_messages_{phone_slug}.md", msg_result)

    timestamps = re.findall(r"\[(\d{4}-\d{2}-\d{2}T[\d:.Z+-]+)\]", msg_result)
    if len(timestamps) >= 2:
        assert timestamps == sorted(timestamps), (
            f"Messages not in chronological order: {timestamps}"
        )
