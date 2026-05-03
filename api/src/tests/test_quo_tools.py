"""
Live integration tests for the Quo (OpenPhone) toolset.

These tests hit the real OpenPhone API. They require OPEN_PHONE_API_KEY
to be set and will be skipped otherwise.

Run with:
    pytest -m live api/src/tests/test_quo_tools.py -v -s

⚠️  SMS SAFETY: NEVER add live tests that send real SMS to external contacts
(tenants, vendors, etc.). External SMS tests must ALWAYS mock the send call.
Sending real messages to tenants from tests risks confusion, lease violations,
and privacy issues. Only send_sms to internal contacts may be tested live against real
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
    get_call_details_impl,
    get_thread_messages_impl,
    list_active_threads_impl,
    search_contacts_impl,
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
    assert "Thread with" in result or "No messages or calls found" in result


@pytest.mark.asyncio
async def test_get_thread_messages_impl_no_messages(quo_client: httpx.AsyncClient):
    """A fake phone number should return 'no messages or calls found'."""
    result = await get_thread_messages_impl(quo_client, "+19999999999", max_results=5)
    assert "No messages or calls found" in result


# ---- Call surfacing in conversations ----
#
# The "Lead 659 Benjamin" thread is a stable example of a conversation that
# contains both a call and an SMS. If that contact is renamed/deleted in the
# future, replace BENJAMIN_PHONE with another known call-bearing thread.
BENJAMIN_PHONE = "+14842802433"


@pytest.mark.asyncio
async def test_get_thread_messages_impl_includes_calls_with_id(
    quo_client: httpx.AsyncClient,
):
    """Thread retrieval should interleave calls with messages and surface the Call ID
    so the agent can chain to ``getCallTranscript_v1``."""
    result = await get_thread_messages_impl(quo_client, BENJAMIN_PHONE, max_results=20)
    _save_fixture("quo_thread_with_call_benjamin.md", result)
    print(f"\nBenjamin thread:\n{result}")
    assert "CALL" in result, "Expected a CALL line in the thread"
    assert "Call ID AC" in result, "Expected a Call ID surfaced in the output"


@pytest_asyncio.fixture
async def benjamin_call_id(quo_client: httpx.AsyncClient) -> str:
    """Fetch Benjamin's most recent OpenPhone call ID at test time.

    The call ID is derived dynamically rather than hardcoded so:
    1. The test stays robust if the specific call is archived or replaced.
    2. No ``AC<32 hex>`` literal appears in source — GitHub's secret scanner
       cannot distinguish OpenPhone call IDs from Twilio Account SIDs (same
       regex), and would block pushes containing a hardcoded one.
    """
    from api.src.sernia_ai.config import QUO_SHARED_EXTERNAL_PHONE_ID

    resp = await quo_client.get(
        "/v1/calls",
        params={
            "phoneNumberId": QUO_SHARED_EXTERNAL_PHONE_ID,
            "participants": BENJAMIN_PHONE,
            "maxResults": "1",
        },
    )
    resp.raise_for_status()
    calls = resp.json().get("data", [])
    if not calls:
        pytest.skip(f"No calls found for {BENJAMIN_PHONE} — cannot test call details")
    return calls[0]["id"]


@pytest.mark.asyncio
async def test_get_call_details_impl_returns_summary_and_transcript(
    quo_client: httpx.AsyncClient,
    benjamin_call_id: str,
):
    """The merged tool should produce markdown with both ## Summary and
    ## Transcript sections, and surface the call ID at the top."""
    result = await get_call_details_impl(quo_client, benjamin_call_id)
    _save_fixture("quo_call_details_benjamin.md", result)
    assert f"# Call {benjamin_call_id}" in result
    assert "## Summary" in result
    assert "## Transcript" in result
    # Speaker attribution should pull in the contact name we have for the caller.
    assert "Lead 659 Benjamin" in result
    # Team turns should be tagged.
    assert "(team)" in result


@pytest.mark.asyncio
async def test_get_call_details_impl_truncates_when_limit_set(
    quo_client: httpx.AsyncClient,
    benjamin_call_id: str,
):
    """When transcript_max_chars is exceeded, the truncation marker must be
    appended and the output should reference the parameter so the caller
    knows how to extend."""
    result = await get_call_details_impl(
        quo_client, benjamin_call_id, transcript_max_chars=300,
    )
    assert "transcript truncated at 300 chars" in result
    assert "transcript_max_chars" in result


@pytest.mark.asyncio
async def test_get_call_details_impl_unknown_call_returns_friendly(
    quo_client: httpx.AsyncClient,
):
    """A bogus call ID should not crash — return a friendly not-found string."""
    result = await get_call_details_impl(quo_client, "ACnotarealcallid")
    assert "No call found" in result


@pytest.mark.asyncio
async def test_list_active_threads_impl_surfaces_call_id_when_call_is_latest(
    quo_client: httpx.AsyncClient,
):
    """When a thread's most recent activity is a call (not an SMS), the snippet
    should show the Call ID so the agent can fetch a transcript."""
    result = await list_active_threads_impl(quo_client, max_results=50)
    _save_fixture("quo_active_threads.md", result)
    if "No active" in result:
        pytest.skip("No active threads — cannot test call snippet")
    # We don't assert that a call snippet is always present (depends on inbox state),
    # but if one exists, it must include the Call ID.
    for line in result.splitlines():
        if line.strip().startswith("Snippet: Call"):
            assert "Call ID AC" in line, f"Call snippet missing Call ID: {line}"


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
    if "No messages or calls found" in msg_result:
        pytest.skip("No messages or calls for extracted phone number")

    phone_slug = target.replace("+", "").replace("-", "")
    _save_fixture(f"quo_thread_messages_{phone_slug}.md", msg_result)

    timestamps = re.findall(r"\[(\d{4}-\d{2}-\d{2}T[\d:.Z+-]+)\]", msg_result)
    if len(timestamps) >= 2:
        assert timestamps == sorted(timestamps), (
            f"Messages not in chronological order: {timestamps}"
        )


# ---- Live SMS length tests ----
# These send real SMS to an internal number (+14123703550) to test delivery
# at various message lengths. Run manually and check phone for receipt.


INTERNAL_TEST_NUMBER = "+14123703550"
SERNIA_AI_PHONE_ID = "PNWvNqsFFy"  # Sernia AI line (internal only)


@pytest.mark.asyncio
async def test_send_sms_short_160_chars(quo_client: httpx.AsyncClient):
    """Send a short SMS (<=160 chars, single segment). Should always deliver."""
    msg = "[QUO TEST 1/5] Short SMS (160 chars). " + "A" * (160 - 38)
    assert len(msg) == 160
    resp = await quo_client.post(
        "/v1/messages",
        json={"content": msg, "from": SERNIA_AI_PHONE_ID, "to": [INTERNAL_TEST_NUMBER]},
    )
    print(f"\n160-char SMS: HTTP {resp.status_code} | body: {resp.text[:300]}")
    _save_fixture("sms_length_160.json", resp.text)
    assert resp.status_code in (200, 201, 202)


@pytest.mark.asyncio
async def test_send_sms_medium_320_chars(quo_client: httpx.AsyncClient):
    """Send a medium SMS (~320 chars, 2-3 segments)."""
    msg = "[QUO TEST 2/5] Medium SMS (320 chars). " + "B" * (320 - 39)
    assert len(msg) == 320
    resp = await quo_client.post(
        "/v1/messages",
        json={"content": msg, "from": SERNIA_AI_PHONE_ID, "to": [INTERNAL_TEST_NUMBER]},
    )
    print(f"\n320-char SMS: HTTP {resp.status_code} | body: {resp.text[:300]}")
    _save_fixture("sms_length_320.json", resp.text)
    assert resp.status_code in (200, 201, 202)


@pytest.mark.asyncio
async def test_send_sms_long_700_chars(quo_client: httpx.AsyncClient):
    """Send a long SMS (~700 chars, similar to the failing scheduled check).

    The scheduled check message that wasn't delivered was 704 chars with emojis.
    """
    prefix = (
        "[QUO TEST 3/5] Long SMS (700 chars) - reproducing scheduled check length. "
        "This message is approximately the same length as the morning inbox check "
        "that was accepted (HTTP 202) but never delivered. "
    )
    msg = prefix + "C" * (700 - len(prefix))
    resp = await quo_client.post(
        "/v1/messages",
        json={"content": msg, "from": SERNIA_AI_PHONE_ID, "to": [INTERNAL_TEST_NUMBER]},
    )
    print(f"\n700-char SMS: HTTP {resp.status_code} | body: {resp.text[:300]}")
    _save_fixture("sms_length_700.json", resp.text)
    assert resp.status_code in (200, 201, 202)


@pytest.mark.asyncio
async def test_send_sms_long_with_emojis_700_chars(quo_client: httpx.AsyncClient):
    """Send a long SMS with emojis (~700 chars) matching the exact pattern that failed.

    Emojis use multi-byte encoding (UCS-2) which can reduce the per-segment
    limit from 160 to 70 chars, potentially causing carrier rejection.
    """
    msg = (
        "[QUO TEST 4/5] Long SMS with emojis (like the real message).\n\n"
        "\U0001f4e7 Zillow: No new leads in the last 36 hours.\n\n"
        "\U0001f4e7 Email: One unread email from marketing — looks like spam. "
        "No action needed.\n\n"
        "\U0001f4ac SMS: 5 active threads. Key notes:\n"
        "- 659-03 Hailey Trainor: Active showing coordination. Last message "
        "from team confirmed Sunday showing was canceled, Tuesday 6pm showing "
        "is still on. Nothing awaiting a reply.\n"
        "- Lead 659-03 Johnson: Thread active but no messages visible.\n"
        "- Other threads (Chris Cafardi, RQM Main Office) are older and appear "
        "dormant.\n\n"
        "All clear overall — nothing urgent needs attention right now!"
    )
    print(f"\nEmoji SMS length: {len(msg)} chars")
    resp = await quo_client.post(
        "/v1/messages",
        json={"content": msg, "from": SERNIA_AI_PHONE_ID, "to": [INTERNAL_TEST_NUMBER]},
    )
    print(f"Emoji SMS: HTTP {resp.status_code} | body: {resp.text[:300]}")
    _save_fixture("sms_length_emoji_700.json", resp.text)
    assert resp.status_code in (200, 201, 202)


@pytest.mark.asyncio
async def test_send_sms_auto_split_725_chars(quo_client: httpx.AsyncClient):
    """Send a ~725 char message using split_sms, verifying auto-split delivery.

    Messages over SMS_SPLIT_THRESHOLD (500) are split at sentence boundaries
    and sent as separate API calls. This test verifies both parts deliver.
    """
    from api.src.sernia_ai.tools.quo_tools import split_sms

    msg = (
        "[QUO TEST 5/5] Auto-split SMS test (~725 chars). "
        "This message tests the split_sms function end-to-end. "
        "It should be split into two parts at a sentence boundary. "
        "The first part should be under 500 chars. "
        "The second part should contain the remainder. "
        "Here is some filler to reach the target length. "
        "Sernia Capital manages rental properties in Pittsburgh. "
        "The team uses AI to handle tenant communications. "
        "Maintenance requests come in via SMS and get routed to ClickUp. "
        "Zillow leads are processed automatically when emails arrive. "
        "The scheduled check runs every 3 hours during business hours. "
        "All external messages require human approval before sending. "
        "This is the end of the test message for auto-split verification."
    )
    print(f"\nOriginal message length: {len(msg)} chars")
    assert 700 <= len(msg) <= 750, f"Message should be 700-750 chars, got {len(msg)}"

    chunks = split_sms(msg)
    print(f"Split into {len(chunks)} chunks: {[len(c) for c in chunks]}")
    assert len(chunks) == 2, f"Expected 2 chunks, got {len(chunks)}"
    assert all(len(c) <= 500 for c in chunks), "All chunks should be <= 500 chars"

    for i, chunk in enumerate(chunks):
        resp = await quo_client.post(
            "/v1/messages",
            json={"content": chunk, "from": SERNIA_AI_PHONE_ID, "to": [INTERNAL_TEST_NUMBER]},
        )
        print(f"  Part {i + 1}/{len(chunks)} ({len(chunk)} chars): HTTP {resp.status_code}")
        _save_fixture(f"sms_split_part{i + 1}.json", resp.text)
        assert resp.status_code in (200, 201, 202), f"Part {i + 1} failed: {resp.text}"
