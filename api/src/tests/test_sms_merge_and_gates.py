"""
Unit tests for SMS history merging and send gate logic.

Tests:
- _merge_sms_into_history: dedup logic for combining DB and Quo SMS histories
- _extract_text_contents: helper for text extraction from ModelMessage lists
- _is_internal_contact: internal/external contact discrimination (quo_tools)

⚠️  SMS SAFETY: All SMS tests mock the send call. NEVER send
real SMS to external contacts from tests. See CLAUDE.md.
"""

import pytest
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)

from api.src.sernia_ai.triggers.ai_sms_event_trigger import (
    _extract_text_contents,
    _merge_sms_into_history,
)
from api.src.sernia_ai.tools.quo_tools import _is_internal_contact


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _user(text: str) -> ModelRequest:
    return ModelRequest(parts=[UserPromptPart(content=text)])


def _assistant(text: str) -> ModelResponse:
    return ModelResponse(parts=[TextPart(content=text)])


def _make_contact(company: str | None = None) -> dict:
    fields: dict = {"firstName": "Test", "lastName": "User"}
    if company is not None:
        fields["company"] = company
    return {"defaultFields": fields}


# ===========================================================================
# _extract_text_contents
# ===========================================================================


class TestExtractTextContents:
    def test_extracts_user_prompt_text(self):
        msgs = [_user("Hello"), _user("World")]
        assert _extract_text_contents(msgs) == {"Hello", "World"}

    def test_extracts_model_response_text(self):
        msgs = [_assistant("Reply one"), _assistant("Reply two")]
        assert _extract_text_contents(msgs) == {"Reply one", "Reply two"}

    def test_extracts_mixed_messages(self):
        msgs = [_user("Question"), _assistant("Answer")]
        assert _extract_text_contents(msgs) == {"Question", "Answer"}

    def test_strips_whitespace(self):
        msgs = [_user("  padded  "), _assistant("  also padded  ")]
        assert _extract_text_contents(msgs) == {"padded", "also padded"}

    def test_empty_list(self):
        assert _extract_text_contents([]) == set()

    def test_skips_tool_call_parts(self):
        """Tool calls and returns don't have plain text to extract."""
        msg = ModelRequest(parts=[
            UserPromptPart(content="visible"),
            ToolReturnPart(
                tool_name="some_tool",
                content="tool output",
                tool_call_id="tc1",
            ),
        ])
        result = _extract_text_contents([msg])
        assert result == {"visible"}

    def test_deduplicates(self):
        msgs = [_user("same"), _user("same"), _assistant("same")]
        assert _extract_text_contents(msgs) == {"same"}


# ===========================================================================
# _merge_sms_into_history
# ===========================================================================


class TestMergeSmsIntoHistory:
    def test_empty_sms_returns_db_history(self):
        db = [_user("Hi"), _assistant("Hello")]
        assert _merge_sms_into_history(db, []) == db

    def test_empty_db_returns_sms_thread(self):
        sms = [_user("Hi"), _assistant("Hello")]
        assert _merge_sms_into_history([], sms) == sms

    def test_both_empty(self):
        assert _merge_sms_into_history([], []) == []

    def test_no_missing_messages_returns_db(self):
        """When all SMS texts already exist in DB, return DB history unchanged."""
        db = [_user("Hello"), _assistant("Hi there")]
        sms = [_user("Hello"), _assistant("Hi there")]
        result = _merge_sms_into_history(db, sms)
        assert result == db

    def test_prepends_missing_sms_messages(self):
        """SMS messages not in DB are prepended."""
        db = [_user("Second"), _assistant("Reply")]
        sms = [_user("First"), _assistant("Earlier reply"), _user("Second")]
        result = _merge_sms_into_history(db, sms)
        # Missing "First" and "Earlier reply" prepended, then DB history
        assert len(result) == 4
        assert result[0].parts[0].content == "First"
        assert result[1].parts[0].content == "Earlier reply"
        assert result[2].parts[0].content == "Second"
        assert result[3].parts[0].content == "Reply"

    def test_preserves_db_tool_context(self):
        """DB history with tool calls is preserved intact — not deduplicated."""
        tool_msg = ModelRequest(parts=[
            ToolReturnPart(
                tool_name="search_contacts",
                content="[{...}]",
                tool_call_id="tc1",
            ),
        ])
        db = [_user("Find Anna"), tool_msg, _assistant("Found her")]
        sms = [_user("Find Anna"), _assistant("Found her")]
        result = _merge_sms_into_history(db, sms)
        # All SMS text already in DB — return DB unchanged (with tool context)
        assert result == db
        assert len(result) == 3

    def test_whitespace_dedup(self):
        """Whitespace differences don't cause duplicates."""
        db = [_user("  Hello  ")]
        sms = [_user("Hello")]
        result = _merge_sms_into_history(db, sms)
        # "Hello" matches " Hello " after strip — no missing messages
        assert result == db

    def test_only_text_missing_from_db(self):
        """Only messages whose text is absent from DB get prepended."""
        db = [_user("Msg A"), _assistant("Reply A")]
        sms = [
            _user("Msg Z"),       # missing — prepend
            _user("Msg A"),       # in DB — skip
            _assistant("Reply A"),  # in DB — skip
            _assistant("Reply Z"),  # missing — prepend
        ]
        result = _merge_sms_into_history(db, sms)
        assert len(result) == 4
        assert result[0].parts[0].content == "Msg Z"
        assert result[1].parts[0].content == "Reply Z"
        assert result[2].parts[0].content == "Msg A"
        assert result[3].parts[0].content == "Reply A"

    def test_context_seed_not_in_sms_thread(self):
        """Hidden context seeds in DB are preserved and don't affect SMS dedup."""
        seed_context = _user("[Context — not visible to SMS recipient: some context]")
        seed_response = _assistant("Is the faucet fixed?")
        db = [seed_context, seed_response]
        sms = [_assistant("Is the faucet fixed?"), _user("Yes all good")]
        result = _merge_sms_into_history(db, sms)
        # "Is the faucet fixed?" already in DB — not prepended
        # "Yes all good" is missing — prepended
        assert len(result) == 3
        assert result[0].parts[0].content == "Yes all good"
        assert result[1].parts[0].content == "[Context — not visible to SMS recipient: some context]"
        assert result[2].parts[0].content == "Is the faucet fixed?"


# ===========================================================================
# _is_internal_contact (quo_tools — module-level)
# ===========================================================================


class TestIsInternalContact:
    def test_sernia_capital_is_internal(self):
        assert _is_internal_contact(_make_contact("Sernia Capital LLC")) is True

    def test_external_company(self):
        assert _is_internal_contact(_make_contact("Some Vendor")) is False

    def test_empty_company(self):
        assert _is_internal_contact(_make_contact("")) is False

    def test_none_company(self):
        assert _is_internal_contact(_make_contact(None)) is False

    def test_no_company_field(self):
        assert _is_internal_contact({"defaultFields": {"firstName": "Test"}}) is False

    def test_no_default_fields(self):
        assert _is_internal_contact({}) is False

    def test_case_sensitive(self):
        """Company name match is exact (case-sensitive)."""
        assert _is_internal_contact(_make_contact("sernia capital llc")) is False
        assert _is_internal_contact(_make_contact("SERNIA CAPITAL LLC")) is False

    def test_whitespace_not_trimmed(self):
        """Whitespace around company name is not trimmed."""
        assert _is_internal_contact(_make_contact(" Sernia Capital LLC ")) is False
