"""
Tests for Google email tools, specifically read_email_thread.

Unit tests mock the Gmail API. Live tests hit the real Gmail API and require
Google service account credentials.

Run unit tests:
    pytest api/src/tests/test_google_tools.py -v -s

Run live tests:
    pytest -m live api/src/tests/test_google_tools.py -v -s
"""

import os
from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from dotenv import find_dotenv, load_dotenv

load_dotenv(find_dotenv(".env"), override=False)

from api.src.sernia_ai.tools.google_tools import (
    _html_to_markdown,
    _read_email,
    _strip_quoted_replies,
    read_email_thread,
    search_emails,
    google_toolset,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

@dataclass
class _FakeDeps:
    user_email: str = "emilio@serniacapital.com"
    conversation_id: str = "test-conv-123"
    user_identifier: str = "test"
    user_name: str = "Test User"
    modality: str = "web_chat"


class _FakeRunContext:
    """Minimal RunContext stand-in for unit tests."""
    def __init__(self, user_email: str = "emilio@serniacapital.com"):
        self.deps = _FakeDeps(user_email=user_email)


def _make_gmail_message(
    msg_id: str,
    thread_id: str,
    from_addr: str,
    to_addr: str,
    subject: str,
    date: str,
    body_text: str = "",
    body_html: str = "",
) -> dict:
    """Build a realistic Gmail API message dict."""
    headers = [
        {"name": "From", "value": from_addr},
        {"name": "To", "value": to_addr},
        {"name": "Subject", "value": subject},
        {"name": "Date", "value": date},
    ]

    parts = []
    if body_text:
        import base64
        parts.append({
            "mimeType": "text/plain",
            "body": {"data": base64.urlsafe_b64encode(body_text.encode()).decode()},
        })
    if body_html:
        import base64
        parts.append({
            "mimeType": "text/html",
            "body": {"data": base64.urlsafe_b64encode(body_html.encode()).decode()},
        })

    payload = {"headers": headers}
    if len(parts) == 1:
        payload.update(parts[0])
    elif parts:
        payload["mimeType"] = "multipart/alternative"
        payload["parts"] = parts

    return {
        "id": msg_id,
        "threadId": thread_id,
        "payload": payload,
    }


# ---------------------------------------------------------------------------
# Unit Tests (mocked Gmail API)
# ---------------------------------------------------------------------------


class TestReadEmailThreadMock:
    """Unit tests for read_email_thread with mocked Gmail API."""

    @pytest.mark.asyncio
    async def test_returns_all_messages_chronologically(self):
        """Thread with 3 messages should return all 3 in order."""
        thread_id = "thread_abc"
        thread_data = {
            "messages": [
                _make_gmail_message(
                    msg_id="msg1", thread_id=thread_id,
                    from_addr="lead@zillow.com", to_addr="all@serniacapital.com",
                    subject="Inquiry about 123 Main St", date="Mon, 01 Jan 2026 10:00:00 +0000",
                    body_text="Hi, I'm interested in the apartment.",
                ),
                _make_gmail_message(
                    msg_id="msg2", thread_id=thread_id,
                    from_addr="all@serniacapital.com", to_addr="lead@zillow.com",
                    subject="Re: Inquiry about 123 Main St", date="Mon, 01 Jan 2026 11:00:00 +0000",
                    body_text="Thanks for reaching out! When would you like to tour?",
                ),
                _make_gmail_message(
                    msg_id="msg3", thread_id=thread_id,
                    from_addr="lead@zillow.com", to_addr="all@serniacapital.com",
                    subject="Re: Inquiry about 123 Main St", date="Mon, 01 Jan 2026 12:00:00 +0000",
                    body_text="How about Saturday at 2pm?",
                ),
            ]
        }

        mock_service = MagicMock()
        mock_service.users().threads().get().execute.return_value = thread_data

        with patch("api.src.sernia_ai.tools.google_tools.get_delegated_credentials"), \
             patch("api.src.sernia_ai.tools.google_tools.get_gmail_service", return_value=mock_service):
            ctx = _FakeRunContext()
            result = await read_email_thread(ctx, thread_id=thread_id)

        # All 3 messages present
        assert "Message 1/3" in result
        assert "Message 2/3" in result
        assert "Message 3/3" in result

        # Content preserved
        assert "interested in the apartment" in result
        assert "When would you like to tour" in result
        assert "Saturday at 2pm" in result

        # From headers preserved
        assert "lead@zillow.com" in result
        assert "all@serniacapital.com" in result

    @pytest.mark.asyncio
    async def test_empty_thread_returns_message(self):
        """Thread with no messages returns a friendly message."""
        thread_data = {"messages": []}

        mock_service = MagicMock()
        mock_service.users().threads().get().execute.return_value = thread_data

        with patch("api.src.sernia_ai.tools.google_tools.get_delegated_credentials"), \
             patch("api.src.sernia_ai.tools.google_tools.get_gmail_service", return_value=mock_service):
            ctx = _FakeRunContext()
            result = await read_email_thread(ctx, thread_id="thread_empty")

        assert "no messages" in result.lower()

    @pytest.mark.asyncio
    async def test_html_body_converted_to_markdown(self):
        """HTML-only messages should be converted to markdown."""
        thread_data = {
            "messages": [
                _make_gmail_message(
                    msg_id="msg1", thread_id="thread_html",
                    from_addr="lead@zillow.com", to_addr="all@serniacapital.com",
                    subject="HTML test", date="Mon, 01 Jan 2026 10:00:00 +0000",
                    body_html="<p>Hello <strong>world</strong></p>",
                ),
            ]
        }

        mock_service = MagicMock()
        mock_service.users().threads().get().execute.return_value = thread_data

        with patch("api.src.sernia_ai.tools.google_tools.get_delegated_credentials"), \
             patch("api.src.sernia_ai.tools.google_tools.get_gmail_service", return_value=mock_service):
            ctx = _FakeRunContext()
            result = await read_email_thread(ctx, thread_id="thread_html")

        # Should contain markdown-converted content, not raw HTML
        assert "<p>" not in result
        assert "world" in result

    @pytest.mark.asyncio
    async def test_long_message_body_truncated(self):
        """Individual messages over 3000 chars should be truncated."""
        long_body = "x" * 5000
        thread_data = {
            "messages": [
                _make_gmail_message(
                    msg_id="msg1", thread_id="thread_long",
                    from_addr="a@b.com", to_addr="c@d.com",
                    subject="Long email", date="Mon, 01 Jan 2026 10:00:00 +0000",
                    body_text=long_body,
                ),
            ]
        }

        mock_service = MagicMock()
        mock_service.users().threads().get().execute.return_value = thread_data

        with patch("api.src.sernia_ai.tools.google_tools.get_delegated_credentials"), \
             patch("api.src.sernia_ai.tools.google_tools.get_gmail_service", return_value=mock_service):
            ctx = _FakeRunContext()
            result = await read_email_thread(ctx, thread_id="thread_long")

        assert "[truncated]" in result
        # The body shouldn't contain the full 5000 chars
        assert len(result) < 5000

    @pytest.mark.asyncio
    async def test_total_output_truncated_for_large_threads(self):
        """Threads with many messages should be capped at 15000 chars total."""
        messages = []
        for i in range(20):
            messages.append(
                _make_gmail_message(
                    msg_id=f"msg{i}", thread_id="thread_huge",
                    from_addr="a@b.com", to_addr="c@d.com",
                    subject=f"Message {i}", date=f"Mon, 01 Jan 2026 {10+i}:00:00 +0000",
                    body_text="y" * 2000,
                )
            )
        thread_data = {"messages": messages}

        mock_service = MagicMock()
        mock_service.users().threads().get().execute.return_value = thread_data

        with patch("api.src.sernia_ai.tools.google_tools.get_delegated_credentials"), \
             patch("api.src.sernia_ai.tools.google_tools.get_gmail_service", return_value=mock_service):
            ctx = _FakeRunContext()
            result = await read_email_thread(ctx, thread_id="thread_huge")

        assert len(result) <= 15100  # 15000 + truncation message
        assert "THREAD TRUNCATED" in result

    @pytest.mark.asyncio
    async def test_user_inbox_email_passed_to_credentials(self):
        """user_inbox_email should be forwarded to get_delegated_credentials."""
        thread_data = {
            "messages": [
                _make_gmail_message(
                    msg_id="msg1", thread_id="thread_inbox",
                    from_addr="a@b.com", to_addr="c@d.com",
                    subject="Test", date="Mon, 01 Jan 2026 10:00:00 +0000",
                    body_text="test",
                ),
            ]
        }

        mock_service = MagicMock()
        mock_service.users().threads().get().execute.return_value = thread_data

        mock_get_creds = MagicMock()
        with patch("api.src.sernia_ai.tools.google_tools.get_delegated_credentials", mock_get_creds), \
             patch("api.src.sernia_ai.tools.google_tools.get_gmail_service", return_value=mock_service):
            ctx = _FakeRunContext(user_email="emilio@serniacapital.com")
            await read_email_thread(ctx, thread_id="thread_inbox", user_inbox_email="all@serniacapital.com")

        # Should use the explicit user_inbox_email, not ctx.deps.user_email
        mock_get_creds.assert_called_once()
        assert mock_get_creds.call_args[1]["user_email"] == "all@serniacapital.com"

    @pytest.mark.asyncio
    async def test_quoted_replies_stripped_in_thread(self):
        """Replies that re-quote the previous message should have the quote stripped."""
        thread_id = "thread_quotes"
        reply_body = (
            "Yes Thursday at 1:20 works!\n"
            "\n"
            "On Mon, Mar 2, 2026 at 3:14 PM Lead <lead@example.com> wrote:\n"
            "\n"
            "> Can I tour Thursday at 1:20pm?\n"
            "> I saw the listing on Zillow.\n"
            "> Looking forward to it.\n"
        )
        thread_data = {
            "messages": [
                _make_gmail_message(
                    msg_id="msg1", thread_id=thread_id,
                    from_addr="lead@example.com", to_addr="all@serniacapital.com",
                    subject="Tour request", date="Mon, 01 Jan 2026 10:00:00 +0000",
                    body_text="Can I tour Thursday at 1:20pm?\nI saw the listing on Zillow.\nLooking forward to it.",
                ),
                _make_gmail_message(
                    msg_id="msg2", thread_id=thread_id,
                    from_addr="all@serniacapital.com", to_addr="lead@example.com",
                    subject="Re: Tour request", date="Mon, 01 Jan 2026 11:00:00 +0000",
                    body_text=reply_body,
                ),
            ]
        }

        mock_service = MagicMock()
        mock_service.users().threads().get().execute.return_value = thread_data

        with patch("api.src.sernia_ai.tools.google_tools.get_delegated_credentials"), \
             patch("api.src.sernia_ai.tools.google_tools.get_gmail_service", return_value=mock_service):
            ctx = _FakeRunContext()
            result = await read_email_thread(ctx, thread_id=thread_id)

        # New reply text preserved
        assert "Yes Thursday at 1:20 works!" in result
        # Quoted reply stripped
        assert "[...quoted reply trimmed...]" in result
        # Attribution line stripped
        assert "On Mon, Mar 2, 2026" not in result
        # Original message (msg1) still present in full
        assert "Can I tour Thursday at 1:20pm?" in result

    @pytest.mark.asyncio
    async def test_single_message_thread(self):
        """Thread with 1 message (new lead, no replies yet)."""
        thread_data = {
            "messages": [
                _make_gmail_message(
                    msg_id="msg1", thread_id="thread_single",
                    from_addr="lead@zillow.com", to_addr="all@serniacapital.com",
                    subject="Lead inquiry", date="Mon, 01 Jan 2026 10:00:00 +0000",
                    body_text="I'm interested in the unit.",
                ),
            ]
        }

        mock_service = MagicMock()
        mock_service.users().threads().get().execute.return_value = thread_data

        with patch("api.src.sernia_ai.tools.google_tools.get_delegated_credentials"), \
             patch("api.src.sernia_ai.tools.google_tools.get_gmail_service", return_value=mock_service):
            ctx = _FakeRunContext()
            result = await read_email_thread(ctx, thread_id="thread_single")

        assert "Message 1/1" in result
        assert "interested in the unit" in result


# ---------------------------------------------------------------------------
# Unit Tests: _strip_quoted_replies
# ---------------------------------------------------------------------------


class TestStripQuotedReplies:
    def test_strips_long_quoted_block(self):
        """3+ consecutive '>' lines should be replaced with trimmed marker."""
        text = (
            "Thanks for your interest!\n"
            "\n"
            "On Mon, Mar 2, 2026 at 10:15 AM Someone wrote:\n"
            "\n"
            "> Previous message line 1\n"
            "> Previous message line 2\n"
            "> Previous message line 3\n"
            "> Previous message line 4\n"
        )
        result = _strip_quoted_replies(text)
        assert "Thanks for your interest!" in result
        assert "[...quoted reply trimmed...]" in result
        assert "Previous message" not in result

    def test_strips_attribution_line(self):
        """The 'On ... wrote:' line before a quote block should also be removed."""
        text = (
            "Great, see you then!\n"
            "\n"
            "On Wed, Mar 4, 2026 at 7:34 AM Jane <jane@example.com> wrote:\n"
            "\n"
            "> Is there any way we could do 1:40?\n"
            "> I have a workout class nearby.\n"
            "> Thanks!\n"
        )
        result = _strip_quoted_replies(text)
        assert "see you then" in result
        assert "On Wed, Mar 4" not in result
        assert "wrote:" not in result
        assert "[...quoted reply trimmed...]" in result

    def test_strips_multiline_attribution(self):
        """Attribution that wraps across two lines should be fully removed."""
        text = (
            "Sounds good.\n"
            "\n"
            "On Mon, Mar 2, 2026 at 3:14 PM Samantha Jurczyk <\n"
            "long-address@convo.zillow.com> wrote:\n"
            "\n"
            "> Can I tour Thursday?\n"
            "> At 1:20pm?\n"
            "> Thanks\n"
        )
        result = _strip_quoted_replies(text)
        assert "Sounds good" in result
        assert "On Mon, Mar 2" not in result
        assert "long-address" not in result
        assert "[...quoted reply trimmed...]" in result

    def test_preserves_short_quotes(self):
        """1-2 '>' lines should be kept (could be inline quotes)."""
        text = (
            "I agree with your point:\n"
            "> We should meet at 2pm\n"
            "That works for me.\n"
        )
        result = _strip_quoted_replies(text)
        assert "> We should meet at 2pm" in result
        assert "That works for me" in result
        assert "trimmed" not in result

    def test_preserves_text_without_quotes(self):
        """Text with no '>' lines should be unchanged."""
        text = "Hello, I'm interested in the apartment.\nPlease let me know."
        result = _strip_quoted_replies(text)
        assert result == text

    def test_multiple_quote_blocks(self):
        """Multiple separate quote blocks in one message should all be stripped."""
        text = (
            "Response to first point.\n"
            "\n"
            "On Mon wrote:\n"
            "\n"
            "> line 1\n"
            "> line 2\n"
            "> line 3\n"
            "\n"
            "Response to second point.\n"
            "\n"
            "On Tue wrote:\n"
            "\n"
            "> line a\n"
            "> line b\n"
            "> line c\n"
        )
        result = _strip_quoted_replies(text)
        assert result.count("[...quoted reply trimmed...]") == 2
        assert "Response to first point" in result
        assert "Response to second point" in result
        assert "line 1" not in result
        assert "line a" not in result

    def test_empty_string(self):
        assert _strip_quoted_replies("") == ""

    def test_only_quotes(self):
        """Message that is entirely a quote block."""
        text = "> line 1\n> line 2\n> line 3\n> line 4\n"
        result = _strip_quoted_replies(text)
        assert "[...quoted reply trimmed...]" in result
        assert "line 1" not in result


# ---------------------------------------------------------------------------
# Live Tests (real Gmail API)
# ---------------------------------------------------------------------------

# Real thread: "Samantha is requesting information about 659 Maryland Ave #3"
# 9-message Zillow lead thread between Samantha Jurczyk and Sernia Capital
# Thread IDs are mailbox-specific — same conversation has different IDs per inbox
_SAMANTHA_THREAD_ID_EMILIO = "19caf1e244d20176"  # emilio@serniacapital.com
_SAMANTHA_THREAD_ID_ALL = "19caf1e113d3f75d"  # all@serniacapital.com

_live = [
    pytest.mark.live,
    pytest.mark.skipif(
        not os.environ.get("GOOGLE_SERVICE_ACCOUNT_CREDENTIALS"),
        reason="GOOGLE_SERVICE_ACCOUNT_CREDENTIALS not set",
    ),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("marker", _live)
async def _skip():
    """Placeholder to apply markers."""


class TestReadEmailThreadLive:
    pytestmark = _live

    @pytest.mark.asyncio
    async def test_reads_full_zillow_thread(self):
        """Read the real Samantha/659 Maryland thread and verify structure."""
        ctx = _FakeRunContext(user_email="emilio@serniacapital.com")
        result = await read_email_thread(ctx, thread_id=_SAMANTHA_THREAD_ID_EMILIO)

        # Should have multiple messages
        assert "Message 1/" in result
        # Known participants
        assert "zillow.com" in result.lower()
        assert "serniacapital.com" in result.lower()
        # Known content from the thread
        assert "659 Maryland" in result
        assert "Samantha" in result

        print(f"\n--- Thread output ({len(result)} chars) ---")
        print(result[:3000])

    @pytest.mark.asyncio
    async def test_reads_thread_from_shared_inbox(self):
        """Read the same thread from all@serniacapital.com (shared mailbox).

        Thread IDs are mailbox-specific, so we use the all@ thread ID here.
        """
        ctx = _FakeRunContext(user_email="emilio@serniacapital.com")
        result = await read_email_thread(
            ctx, thread_id=_SAMANTHA_THREAD_ID_ALL,
            user_inbox_email="all@serniacapital.com",
        )

        # Should still have thread content
        assert "Message 1/" in result
        assert "659 Maryland" in result

        print(f"\n--- Shared inbox thread ({len(result)} chars) ---")
        print(result[:2000])

    @pytest.mark.asyncio
    async def test_thread_message_count_matches_expected(self):
        """The Samantha thread should have 9 messages."""
        ctx = _FakeRunContext(user_email="emilio@serniacapital.com")
        result = await read_email_thread(ctx, thread_id=_SAMANTHA_THREAD_ID_EMILIO)

        # Thread has 9 messages
        assert "Message 9/9" in result or "Message 1/9" in result

    @pytest.mark.asyncio
    async def test_search_then_read_thread_flow(self):
        """End-to-end: search for the email, extract thread ID, read thread."""
        # Step 1: Search
        ctx = _FakeRunContext(user_email="emilio@serniacapital.com")
        search_result = await search_emails(
            ctx, query="from:zillow.com Samantha 659 Maryland", max_results=3
        )
        assert "659 Maryland" in search_result

        # Extract thread ID from search results
        assert _SAMANTHA_THREAD_ID_EMILIO in search_result, (
            f"Expected thread ID {_SAMANTHA_THREAD_ID_EMILIO} in search results"
        )

        # Step 2: Read full thread
        thread_result = await read_email_thread(ctx, thread_id=_SAMANTHA_THREAD_ID_EMILIO)
        assert "Message 1/" in thread_result
        assert "Samantha" in thread_result

        print("\n--- Search → Read Thread flow verified ---")
