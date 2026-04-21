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
    _sanitize_tool_calls,
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


# ===========================================================================
# _sanitize_tool_calls
# ===========================================================================


def _tool_call(name: str, call_id: str) -> ModelResponse:
    return ModelResponse(parts=[ToolCallPart(tool_name=name, args='{}', tool_call_id=call_id)])


def _tool_return(name: str, call_id: str, content: str = "ok") -> ModelRequest:
    return ModelRequest(parts=[ToolReturnPart(tool_name=name, content=content, tool_call_id=call_id)])


class TestSanitizeToolCalls:
    def test_empty_messages(self):
        assert _sanitize_tool_calls([]) == []

    def test_no_tool_calls_unchanged(self):
        msgs = [_user("hello"), _assistant("hi")]
        assert _sanitize_tool_calls(msgs) == msgs

    def test_removes_trailing_tool_call(self):
        """Trailing ToolCallPart without return should be removed."""
        msgs = [
            _user("do something"),
            _tool_call("send_sms", "tc1"),
        ]
        result = _sanitize_tool_calls(msgs)
        # Trailing tool call removed
        assert len(result) == 1
        assert isinstance(result[0], ModelRequest)

    def test_removes_orphaned_tool_return(self):
        """ToolReturnPart without matching ToolCallPart should be removed."""
        msgs = [
            _tool_return("old_tool", "toolu_orphan"),
            _user("hello"),
            _assistant("hi"),
        ]
        result = _sanitize_tool_calls(msgs)
        # Orphaned return removed
        assert len(result) == 2
        assert isinstance(result[0], ModelRequest)
        assert isinstance(result[0].parts[0], UserPromptPart)

    def test_keeps_valid_tool_call_return_pair(self):
        """Valid ToolCallPart + ToolReturnPart pairs are preserved."""
        msgs = [
            _user("search"),
            _tool_call("search_contacts", "tc1"),
            _tool_return("search_contacts", "tc1"),
            _assistant("found it"),
        ]
        result = _sanitize_tool_calls(msgs)
        assert len(result) == 4

    def test_removes_anthropic_style_orphan_from_trimmed_history(self):
        """Simulates history trimming cutting off the ToolCallPart.

        This is the exact bug that caused issues 81/82 — history trimming
        removes the beginning of the conversation which contains the
        ToolCallPart, leaving an orphaned ToolReturnPart.
        """
        msgs = [
            # ToolCallPart was in trimmed portion — only return remains
            _tool_return("send_sms", "toolu_012dE58Mgx5qBuz7yjcZCkpk"),
            _user("thanks for sending that"),
            _assistant("you're welcome"),
        ]
        result = _sanitize_tool_calls(msgs)
        # Orphaned return removed
        assert len(result) == 2
        assert isinstance(result[0].parts[0], UserPromptPart)
        assert result[0].parts[0].content == "thanks for sending that"

    def test_mixed_valid_and_orphaned_returns(self):
        """Some returns are valid (have matching calls), some are orphaned."""
        msgs = [
            # Orphaned return (no matching call)
            _tool_return("old_tool", "toolu_orphan"),
            _user("search"),
            _tool_call("search_contacts", "tc1"),
            _tool_return("search_contacts", "tc1"),
            _assistant("found it"),
        ]
        result = _sanitize_tool_calls(msgs)
        # Orphaned return removed, valid pair kept
        assert len(result) == 4
        # Check the return that remains is the valid one
        returns = [
            p for msg in result if isinstance(msg, ModelRequest)
            for p in msg.parts if isinstance(p, ToolReturnPart)
        ]
        assert len(returns) == 1
        assert returns[0].tool_call_id == "tc1"

    def test_request_removed_if_only_orphaned_returns(self):
        """ModelRequest with only orphaned ToolReturnParts is removed entirely."""
        msgs = [
            ModelRequest(parts=[
                ToolReturnPart(tool_name="tool_a", content="stale", tool_call_id="toolu_1"),
                ToolReturnPart(tool_name="tool_b", content="stale", tool_call_id="toolu_2"),
            ]),
            _user("hello"),
            _assistant("hi"),
        ]
        result = _sanitize_tool_calls(msgs)
        # First request entirely removed
        assert len(result) == 2
        assert isinstance(result[0].parts[0], UserPromptPart)

    def test_request_keeps_non_orphan_parts(self):
        """ModelRequest with both orphaned returns and valid parts keeps valid parts."""
        msgs = [
            ModelRequest(parts=[
                ToolReturnPart(tool_name="old_tool", content="stale", tool_call_id="toolu_orphan"),
                UserPromptPart(content="hello"),
            ]),
            _assistant("hi"),
        ]
        result = _sanitize_tool_calls(msgs)
        assert len(result) == 2
        # UserPromptPart preserved
        assert isinstance(result[0], ModelRequest)
        assert len(result[0].parts) == 1
        assert isinstance(result[0].parts[0], UserPromptPart)

    def test_handles_both_orphan_types(self):
        """Both orphaned returns AND trailing calls are handled."""
        msgs = [
            # Orphaned return
            _tool_return("old_tool", "toolu_orphan"),
            _user("do something"),
            # Trailing tool call without return
            _tool_call("send_sms", "tc1"),
        ]
        result = _sanitize_tool_calls(msgs)
        # Both removed
        assert len(result) == 1
        assert isinstance(result[0].parts[0], UserPromptPart)
        assert result[0].parts[0].content == "do something"
