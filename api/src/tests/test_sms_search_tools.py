"""
Unit tests for SMS search tools (search_sms_history, get_contact_sms_history).

All database queries and OpenPhone API calls are mocked.
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic_ai import RunContext

from api.src.open_phone.models import OpenPhoneEvent
from api.src.sernia_ai.tools.db_search_tools import (
    search_sms_history,
    get_contact_sms_history,
    _resolve_contact_phones,
    _build_contact_map,
    _enrich_phone,
    _format_sms_event,
    _parse_date,
    _build_date_filters,
)


# ---------------------------------------------------------------------------
# Fixtures / Helpers
# ---------------------------------------------------------------------------

# Realistic OpenPhone contact data
FAKE_CONTACTS = [
    {
        "id": "ct_001",
        "defaultFields": {
            "firstName": "John",
            "lastName": "Doe",
            "company": "Peppino Bldg A Unit 203",
            "phoneNumbers": [{"value": "+14155550100"}],
        },
    },
    {
        "id": "ct_002",
        "defaultFields": {
            "firstName": "Maria",
            "lastName": "Garcia",
            "company": "Peppino Bldg B Unit 105",
            "phoneNumbers": [
                {"value": "+14155550200"},
                {"value": "+14155550201"},  # secondary number
            ],
        },
    },
    {
        "id": "ct_003",
        "defaultFields": {
            "firstName": "Sernia",
            "lastName": "Capital",
            "company": "Sernia Capital LLC",
            "phoneNumbers": [{"value": "+14155559999"}],
        },
    },
    {
        "id": "ct_004",
        "defaultFields": {
            "firstName": "Bob",
            "lastName": "The Plumber",
            "company": "Quick Fix Plumbing",
            "phoneNumbers": [{"value": "+14155550300"}],
        },
    },
]


def _make_sms_event(
    id: int,
    from_number: str,
    to_number: str,
    text: str,
    ts: datetime | None = None,
    conversation_id: str = "conv_001",
) -> OpenPhoneEvent:
    """Create a fake OpenPhoneEvent for testing."""
    evt = OpenPhoneEvent()
    evt.id = id
    evt.event_type = "message.received"
    evt.event_id = f"evt_{id:04d}"
    evt.from_number = from_number
    evt.to_number = to_number
    evt.message_text = text
    evt.conversation_id = conversation_id
    evt.event_timestamp = ts or datetime(2025, 6, 15, 14, 30 + id, tzinfo=timezone.utc)
    evt.created_at = evt.event_timestamp
    evt.event_data = {}
    evt.user_id = None
    evt.phone_number_id = None
    return evt


# A realistic SMS thread about a maintenance issue
FAKE_THREAD = [
    _make_sms_event(1, "+14155550100", "+14155559999", "Hey, is maintenance coming today?",
                    datetime(2025, 6, 15, 14, 30, tzinfo=timezone.utc)),
    _make_sms_event(2, "+14155559999", "+14155550100", "Yes, the plumber is scheduled for 3pm",
                    datetime(2025, 6, 15, 14, 32, tzinfo=timezone.utc)),
    _make_sms_event(3, "+14155550100", "+14155559999", "Great, the leak has gotten worse since yesterday",
                    datetime(2025, 6, 15, 14, 33, tzinfo=timezone.utc)),
    _make_sms_event(4, "+14155559999", "+14155550100", "I'll let them know to prioritize it",
                    datetime(2025, 6, 15, 14, 35, tzinfo=timezone.utc)),
    _make_sms_event(5, "+14155550100", "+14155559999", "Thanks! The water damage is spreading to the hallway",
                    datetime(2025, 6, 15, 14, 40, tzinfo=timezone.utc)),
    _make_sms_event(6, "+14155559999", "+14155550100", "I'm sending emergency maintenance right now",
                    datetime(2025, 6, 15, 14, 42, tzinfo=timezone.utc)),
    _make_sms_event(7, "+14155550100", "+14155559999", "They just arrived, thank you!",
                    datetime(2025, 6, 15, 15, 10, tzinfo=timezone.utc)),
    _make_sms_event(8, "+14155559999", "+14155550100", "Glad to hear it. Let me know if the leak is fully fixed",
                    datetime(2025, 6, 15, 15, 15, tzinfo=timezone.utc)),
]

# Second conversation thread — different contact
FAKE_THREAD_2 = [
    _make_sms_event(20, "+14155550200", "+14155559999", "Hi, my rent payment bounced, what do I do?",
                    datetime(2025, 6, 10, 9, 0, tzinfo=timezone.utc), conversation_id="conv_002"),
    _make_sms_event(21, "+14155559999", "+14155550200", "Please re-submit via the portal. No late fee this time.",
                    datetime(2025, 6, 10, 9, 5, tzinfo=timezone.utc), conversation_id="conv_002"),
]


def _make_ctx() -> RunContext:
    """Build a RunContext mock with a mock db session."""
    ctx = MagicMock(spec=RunContext)
    ctx.deps = MagicMock()
    ctx.deps.db_session = AsyncMock()
    return ctx


def _mock_execute_returns(session_mock: AsyncMock, rows: list) -> None:
    """Configure the session mock to return rows from session.execute(...).scalars().all()."""
    result_mock = MagicMock()
    result_mock.scalars.return_value.all.return_value = rows
    session_mock.execute = AsyncMock(return_value=result_mock)


def _mock_execute_sequence(session_mock: AsyncMock, results_sequence: list[list]) -> None:
    """Configure the session mock to return different rows on successive calls."""
    mocks = []
    for rows in results_sequence:
        result_mock = MagicMock()
        result_mock.scalars.return_value.all.return_value = rows
        mocks.append(result_mock)
    session_mock.execute = AsyncMock(side_effect=mocks)


CONTACT_PATCH = "api.src.sernia_ai.tools.db_search_tools._get_contact_map"
RESOLVE_PATCH = "api.src.sernia_ai.tools.db_search_tools._resolve_contact_phones"


# ---------------------------------------------------------------------------
# Helper Tests
# ---------------------------------------------------------------------------


class TestHelpers:
    """Tests for shared SMS helper functions."""

    def test_build_contact_map(self):
        cmap = _build_contact_map(FAKE_CONTACTS)
        assert cmap["+14155550100"] == "John Doe (Peppino Bldg A Unit 203)"
        assert cmap["+14155550200"] == "Maria Garcia (Peppino Bldg B Unit 105)"
        assert cmap["+14155550201"] == "Maria Garcia (Peppino Bldg B Unit 105)"  # secondary number
        assert cmap["+14155559999"] == "Sernia Capital (Sernia Capital LLC)"

    def test_enrich_phone_known(self):
        cmap = _build_contact_map(FAKE_CONTACTS)
        assert _enrich_phone("+14155550100", cmap) == "John Doe (Peppino Bldg A Unit 203)"

    def test_enrich_phone_unknown(self):
        assert _enrich_phone("+19999999999", {}) == "+19999999999"

    def test_enrich_phone_none(self):
        assert _enrich_phone(None, {}) == "Unknown"

    def test_format_sms_event(self):
        cmap = _build_contact_map(FAKE_CONTACTS)
        evt = FAKE_THREAD[2]  # "the leak has gotten worse"
        formatted = _format_sms_event(evt, cmap, is_match=True)
        assert "John Doe" in formatted
        assert "leak has gotten worse" in formatted
        assert "<-- MATCH" in formatted

    def test_format_sms_event_no_match_marker(self):
        cmap = _build_contact_map(FAKE_CONTACTS)
        formatted = _format_sms_event(FAKE_THREAD[0], cmap, is_match=False)
        assert "<-- MATCH" not in formatted

    def test_parse_date_valid(self):
        dt = _parse_date("2025-06-15")
        assert dt == datetime(2025, 6, 15)

    def test_parse_date_invalid(self):
        assert _parse_date("not-a-date") is None
        assert _parse_date(None) is None
        assert _parse_date("") is None

    def test_build_date_filters_both(self):
        filters = _build_date_filters("2025-06-01", "2025-06-30")
        assert len(filters) == 2

    def test_build_date_filters_none(self):
        filters = _build_date_filters(None, None)
        assert len(filters) == 0


class TestResolveContactPhones:
    """Tests for _resolve_contact_phones."""

    @pytest.mark.asyncio
    async def test_fuzzy_match_by_unit_number(self):
        mock_client = AsyncMock()
        mock_client.aclose = AsyncMock()

        with patch(
            "api.src.sernia_ai.tools.openphone_tools._get_all_contacts",
            new_callable=AsyncMock,
            return_value=FAKE_CONTACTS,
        ), patch(
            "api.src.sernia_ai.tools.openphone_tools._build_openphone_client",
            return_value=mock_client,
        ):
            name, phones = await _resolve_contact_phones("Unit 203")

        assert "John Doe" in name
        assert "+14155550100" in phones

    @pytest.mark.asyncio
    async def test_fuzzy_match_by_first_name(self):
        mock_client = AsyncMock()
        mock_client.aclose = AsyncMock()

        with patch(
            "api.src.sernia_ai.tools.openphone_tools._get_all_contacts",
            new_callable=AsyncMock,
            return_value=FAKE_CONTACTS,
        ), patch(
            "api.src.sernia_ai.tools.openphone_tools._build_openphone_client",
            return_value=mock_client,
        ):
            name, phones = await _resolve_contact_phones("Maria")

        assert "Maria Garcia" in name
        # Should include both phone numbers
        assert "+14155550200" in phones
        assert "+14155550201" in phones

    @pytest.mark.asyncio
    async def test_no_match_raises(self):
        mock_client = AsyncMock()
        mock_client.aclose = AsyncMock()

        with patch(
            "api.src.sernia_ai.tools.openphone_tools._get_all_contacts",
            new_callable=AsyncMock,
            return_value=[],  # empty contacts — nothing to match
        ), patch(
            "api.src.sernia_ai.tools.openphone_tools._build_openphone_client",
            return_value=mock_client,
        ):
            with pytest.raises(ValueError, match="No contact found"):
                await _resolve_contact_phones("zzzznonexistent")


# ---------------------------------------------------------------------------
# search_sms_history Tests
# ---------------------------------------------------------------------------


class TestSearchSmsHistory:
    """Tests for the search_sms_history tool."""

    @pytest.mark.asyncio
    async def test_keyword_search_returns_matches_with_context(self):
        """Searching for 'leak' should return the match plus surrounding messages."""
        ctx = _make_ctx()
        session = ctx.deps.db_session
        contact_map = _build_contact_map(FAKE_CONTACTS)

        # First execute: keyword search returns the match
        # Second execute: context window fetch returns the full thread
        _mock_execute_sequence(session, [
            [FAKE_THREAD[2]],  # "the leak has gotten worse"
            FAKE_THREAD,       # full conversation for context
        ])

        with patch(CONTACT_PATCH, new_callable=AsyncMock, return_value=contact_map):
            result = await search_sms_history(ctx, query="leak")

        assert "Match 1 of 1" in result
        assert "leak has gotten worse" in result
        assert "<-- MATCH" in result
        # Context messages should also appear
        assert "maintenance coming today" in result
        assert "prioritize it" in result

    @pytest.mark.asyncio
    async def test_keyword_search_with_contact_filter(self):
        """Searching with contact_name should resolve the contact first."""
        ctx = _make_ctx()
        session = ctx.deps.db_session
        contact_map = _build_contact_map(FAKE_CONTACTS)

        _mock_execute_sequence(session, [
            [FAKE_THREAD[2]],
            FAKE_THREAD,
        ])

        with patch(CONTACT_PATCH, new_callable=AsyncMock, return_value=contact_map), \
             patch(RESOLVE_PATCH, new_callable=AsyncMock, return_value=("John Doe (Peppino Bldg A Unit 203)", ["+14155550100"])):
            result = await search_sms_history(ctx, query="leak", contact_name="Unit 203")

        assert "John Doe" in result
        assert "leak" in result

    @pytest.mark.asyncio
    async def test_no_results(self):
        """Should return a friendly message when no matches found."""
        ctx = _make_ctx()
        _mock_execute_returns(ctx.deps.db_session, [])

        with patch(CONTACT_PATCH, new_callable=AsyncMock, return_value={}):
            result = await search_sms_history(ctx, query="xyznonexistent")

        assert "No SMS messages found" in result
        assert "xyznonexistent" in result

    @pytest.mark.asyncio
    async def test_no_results_with_contact(self):
        """Should mention the contact in the 'not found' message."""
        ctx = _make_ctx()
        _mock_execute_returns(ctx.deps.db_session, [])

        with patch(CONTACT_PATCH, new_callable=AsyncMock, return_value={}), \
             patch(RESOLVE_PATCH, new_callable=AsyncMock, return_value=("John Doe", ["+14155550100"])):
            result = await search_sms_history(ctx, query="xyznonexistent", contact_name="John")

        assert "No SMS messages found" in result

    @pytest.mark.asyncio
    async def test_contact_not_found(self):
        """Should return error if contact_name doesn't match anyone."""
        ctx = _make_ctx()

        with patch(RESOLVE_PATCH, new_callable=AsyncMock, side_effect=ValueError("No contact found matching 'zzz'")):
            result = await search_sms_history(ctx, query="leak", contact_name="zzz")

        assert "No contact found" in result

    @pytest.mark.asyncio
    async def test_date_filters_passed(self):
        """Date filters should be accepted without error."""
        ctx = _make_ctx()
        _mock_execute_returns(ctx.deps.db_session, [])

        with patch(CONTACT_PATCH, new_callable=AsyncMock, return_value={}):
            result = await search_sms_history(
                ctx, query="rent", after="2025-06-01", before="2025-06-30"
            )

        assert "No SMS messages found" in result

    @pytest.mark.asyncio
    async def test_error_handling(self):
        """Database errors should be caught and return a friendly message."""
        ctx = _make_ctx()
        ctx.deps.db_session.execute = AsyncMock(side_effect=RuntimeError("DB connection lost"))

        result = await search_sms_history(ctx, query="test")
        assert "Error searching SMS history" in result


# ---------------------------------------------------------------------------
# get_contact_sms_history Tests
# ---------------------------------------------------------------------------


class TestGetContactSmsHistory:
    """Tests for the get_contact_sms_history tool."""

    @pytest.mark.asyncio
    async def test_returns_chronological_history(self):
        """Should return messages in chronological order (oldest first)."""
        ctx = _make_ctx()
        session = ctx.deps.db_session
        contact_map = _build_contact_map(FAKE_CONTACTS)

        # DB returns newest first (DESC), tool should reverse for display
        _mock_execute_returns(session, list(reversed(FAKE_THREAD)))

        with patch(CONTACT_PATCH, new_callable=AsyncMock, return_value=contact_map), \
             patch(RESOLVE_PATCH, new_callable=AsyncMock, return_value=("John Doe (Peppino Bldg A Unit 203)", ["+14155550100"])):
            result = await get_contact_sms_history(ctx, contact_name="John")

        assert "SMS history with John Doe" in result
        assert "8 messages" in result
        # Messages should appear in chronological order
        lines = result.split("\n")
        # Find lines with timestamps and verify order
        ts_lines = [l for l in lines if l.startswith("[")]
        assert len(ts_lines) == 8
        # First message should be the earliest
        assert "maintenance coming today" in ts_lines[0]
        # Last message should be the latest
        assert "leak is fully fixed" in ts_lines[-1]

    @pytest.mark.asyncio
    async def test_contact_not_found(self):
        """Should return friendly error if contact can't be resolved."""
        ctx = _make_ctx()

        with patch(RESOLVE_PATCH, new_callable=AsyncMock, side_effect=ValueError("No contact found matching 'zzz'")):
            result = await get_contact_sms_history(ctx, contact_name="zzz")

        assert "No contact found" in result

    @pytest.mark.asyncio
    async def test_no_messages_for_contact(self):
        """Should mention the contact name in empty results."""
        ctx = _make_ctx()
        _mock_execute_returns(ctx.deps.db_session, [])

        with patch(CONTACT_PATCH, new_callable=AsyncMock, return_value={}), \
             patch(RESOLVE_PATCH, new_callable=AsyncMock, return_value=("Bob The Plumber", ["+14155550300"])):
            result = await get_contact_sms_history(ctx, contact_name="Bob")

        assert "No SMS messages found for Bob The Plumber" in result

    @pytest.mark.asyncio
    async def test_no_messages_with_date_filter(self):
        """Should mention date range in empty results."""
        ctx = _make_ctx()
        _mock_execute_returns(ctx.deps.db_session, [])

        with patch(CONTACT_PATCH, new_callable=AsyncMock, return_value={}), \
             patch(RESOLVE_PATCH, new_callable=AsyncMock, return_value=("John Doe", ["+14155550100"])):
            result = await get_contact_sms_history(
                ctx, contact_name="John", after="2025-07-01"
            )

        assert "No SMS messages found for John Doe" in result
        assert "after 2025-07-01" in result

    @pytest.mark.asyncio
    async def test_date_filter_both(self):
        """Should mention both dates in empty results."""
        ctx = _make_ctx()
        _mock_execute_returns(ctx.deps.db_session, [])

        with patch(CONTACT_PATCH, new_callable=AsyncMock, return_value={}), \
             patch(RESOLVE_PATCH, new_callable=AsyncMock, return_value=("John Doe", ["+14155550100"])):
            result = await get_contact_sms_history(
                ctx, contact_name="John", after="2025-06-01", before="2025-06-30"
            )

        assert "between 2025-06-01 and 2025-06-30" in result

    @pytest.mark.asyncio
    async def test_error_handling(self):
        """Database errors should be caught and return a friendly message."""
        ctx = _make_ctx()
        ctx.deps.db_session.execute = AsyncMock(side_effect=RuntimeError("DB timeout"))

        with patch(RESOLVE_PATCH, new_callable=AsyncMock, return_value=("John Doe", ["+14155550100"])):
            result = await get_contact_sms_history(ctx, contact_name="John")

        assert "Error fetching SMS history" in result


# ---------------------------------------------------------------------------
# Smoke Tests
# ---------------------------------------------------------------------------


class TestSmoke:
    """Verify the tools are properly registered on the toolset."""

    def test_tools_registered_on_toolset(self):
        from api.src.sernia_ai.tools.db_search_tools import db_search_toolset
        # The toolset should have all three tools
        # We check by trying to access the tool definitions
        # FunctionToolset stores tools internally - just verify import works
        assert db_search_toolset is not None

    def test_agent_imports_with_sms_tools(self):
        """The agent should still import cleanly with the new tools."""
        from api.src.sernia_ai.agent import sernia_agent
        assert sernia_agent is not None
