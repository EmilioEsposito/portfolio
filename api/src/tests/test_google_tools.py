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
    _clean_zillow_email,
    _html_to_markdown,
    _is_zillow_content,
    _read_email,
    _strip_quoted_replies,
    _summarize_if_long,
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
    async def test_long_message_body_summarized(self):
        """Individual messages over 3000 chars should be summarized (or truncated as fallback)."""
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

        # Mock summarizer to verify it's called for oversized content
        async def fake_summarize(content, max_chars):
            if len(content) > max_chars:
                return content[:max_chars] + "\n...[SUMMARIZED]"
            return content

        with patch("api.src.sernia_ai.tools.google_tools.get_delegated_credentials"), \
             patch("api.src.sernia_ai.tools.google_tools.get_gmail_service", return_value=mock_service), \
             patch("api.src.sernia_ai.tools.google_tools._summarize_if_long", side_effect=fake_summarize):
            ctx = _FakeRunContext()
            result = await read_email_thread(ctx, thread_id="thread_long")

        assert "SUMMARIZED" in result
        # The body shouldn't contain the full 5000 chars
        assert len(result) < 5000

    @pytest.mark.asyncio
    async def test_total_output_capped_for_large_threads(self):
        """Threads with many messages should be capped at ~15000 chars (summarized or truncated)."""
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

        async def fake_summarize(content, max_chars):
            if len(content) > max_chars:
                return content[:max_chars] + "\n...[SUMMARIZED]"
            return content

        with patch("api.src.sernia_ai.tools.google_tools.get_delegated_credentials"), \
             patch("api.src.sernia_ai.tools.google_tools.get_gmail_service", return_value=mock_service), \
             patch("api.src.sernia_ai.tools.google_tools._summarize_if_long", side_effect=fake_summarize):
            ctx = _FakeRunContext()
            result = await read_email_thread(ctx, thread_id="thread_huge")

        assert len(result) <= 15100  # 15000 + summarized/truncation message
        assert "SUMMARIZED" in result

    @pytest.mark.asyncio
    async def test_user_email_account_passed_to_credentials(self):
        """user_email_account should be forwarded to get_delegated_credentials."""
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
            await read_email_thread(ctx, thread_id="thread_inbox", user_email_account="all@serniacapital.com")

        # Should use the explicit user_email_account, not ctx.deps.user_email
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
# Unit Tests: _clean_zillow_email
# ---------------------------------------------------------------------------


class TestCleanZillowEmail:
    def test_strips_new_message_boilerplate(self):
        """'New message from a renter' and listing sentence should be removed."""
        content = (
            "What about when using AC?\n"
            "New message from a renter. "
            "A renter sent you a message about your listing at "
            "659 Maryland Ave #4, Shadyside, PA 15232. "
            "You can reply on Zillow or directly to this email.\n"
            "Nelson Chang says: What about when using AC?"
        )
        result = _clean_zillow_email(content)
        assert "What about when using AC?" in result
        assert "New message from a renter" not in result
        assert "A renter sent you a message" not in result
        assert "You can reply on Zillow" not in result

    def test_extracts_from_says_discarding_suggested_replies(self):
        """Suggested reply button text above 'says:' should be discarded."""
        content = (
            "We're here!\n"
            "Okay see you soon!\n"
            "Sorry I'm running a little late, almost there!\n"
            "Reply on Zillow\n"
            "For your safety, always double-check requests.\n"
            "Nelson Chang says:\n"
            "We're here!"
        )
        result = _clean_zillow_email(content)
        assert result == "We're here!"
        assert "Okay see you soon" not in result
        assert "running a little late" not in result

    def test_strips_safety_disclaimer(self):
        content = (
            "Can I schedule a tour?\n\n"
            "For your safety, always double-check requests for information in "
            "messages and be vigilant of scams. Do not send payment or share "
            "personal financial information with the other party.\n"
            "Learn about staying safe.\n"
        )
        result = _clean_zillow_email(content)
        assert "schedule a tour" in result
        assert "For your safety" not in result
        assert "Learn about staying safe" not in result

    def test_strips_yes_no_buttons(self):
        content = "Are you interested?\nYes\nNo\nReply on Zillow\n"
        result = _clean_zillow_email(content)
        assert "Are you interested?" in result
        # Yes/No/Reply on Zillow removed
        assert result.strip().replace("\n", "").startswith("Are you interested?")

    def test_strips_long_tracking_urls(self):
        long_url = "https://www.zillow.com/rental-manager/inbox/conversations/" + "a" * 100
        content = f"Hello!\n{long_url}\nGoodbye!"
        result = _clean_zillow_email(content)
        assert "Hello!" in result
        assert "Goodbye!" in result
        assert long_url not in result

    def test_strips_fair_housing_boilerplate(self):
        content = (
            "I'd like to tour.\n\n"
            "The basics of Fair Housing: The Fair Housing Act prohibits housing "
            "discrimination on the basis of race, color, national origin, sex "
            "(including sexual orientation and gender identity), familial status, "
            "disability, and religion."
        )
        result = _clean_zillow_email(content)
        assert "I'd like to tour." in result
        assert "Fair Housing" not in result

    def test_strips_utm_lines(self):
        content = (
            "Great message.\n"
            "utm_source=email&utm_campaign=unified&utm_content=stuff\n"
            "Real text."
        )
        result = _clean_zillow_email(content)
        assert "Great message." in result
        assert "Real text." in result
        assert "utm_" not in result

    def test_preserves_non_zillow_content(self):
        content = "Hi Emilio,\n\nRent check will be late this month.\n\nThanks, Nelson"
        result = _clean_zillow_email(content)
        assert result == content

    def test_empty_string(self):
        assert _clean_zillow_email("") == ""


# ---------------------------------------------------------------------------
# Unit Tests: _is_zillow_content
# ---------------------------------------------------------------------------


class TestIsZillowContent:
    def test_zillow_from_address(self):
        assert _is_zillow_content(from_addr="lead@convo.zillow.com")

    def test_zillow_in_content(self):
        assert _is_zillow_content(content="A renter from zillow.com sent a message")

    def test_non_zillow(self):
        assert not _is_zillow_content(from_addr="tenant@gmail.com", content="Plain email")


# ---------------------------------------------------------------------------
# Unit Tests: _summarize_if_long
# ---------------------------------------------------------------------------


class TestSummarizeIfLong:
    @pytest.mark.asyncio
    async def test_short_content_returned_unchanged(self):
        """Content under the limit should be returned as-is."""
        content = "Short email content."
        result = await _summarize_if_long(content, 5000)
        assert result == content

    @pytest.mark.asyncio
    async def test_long_content_summarized(self):
        """Content over the limit should be summarized by the LLM."""
        long_content = "Important detail. " * 500  # ~9000 chars

        with patch("api.src.sernia_ai.tools.google_tools._email_summarizer") as mock_agent:
            mock_result = MagicMock()
            mock_result.output = "Summary of important details."
            mock_agent.run = AsyncMock(return_value=mock_result)

            result = await _summarize_if_long(long_content, 3000)

        assert "Summarized" in result
        assert "Summary of important details" in result

    @pytest.mark.asyncio
    async def test_fallback_to_truncation_on_error(self):
        """If LLM fails, should fall back to hard truncation."""
        long_content = "x" * 6000

        with patch("api.src.sernia_ai.tools.google_tools._email_summarizer") as mock_agent:
            mock_agent.run = AsyncMock(side_effect=RuntimeError("LLM unavailable"))

            result = await _summarize_if_long(long_content, 5000)

        assert len(result) <= 5100  # 5000 + truncation message
        assert "TRUNCATED" in result


# ---------------------------------------------------------------------------
# Unit Tests: read_email_thread with message IDs
# ---------------------------------------------------------------------------


class TestReadEmailThreadMessageIds:
    @pytest.mark.asyncio
    async def test_output_includes_message_ids(self):
        """Each message in thread output should include its Gmail message ID."""
        thread_data = {
            "messages": [
                _make_gmail_message(
                    msg_id="abc123", thread_id="thread1",
                    from_addr="a@b.com", to_addr="c@d.com",
                    subject="Test", date="Mon, 01 Jan 2026 10:00:00 +0000",
                    body_text="Hello",
                ),
            ]
        }
        thread_data["messages"][0]["internalDate"] = "1000000"

        mock_service = MagicMock()
        mock_service.users().threads().get().execute.return_value = thread_data

        with patch("api.src.sernia_ai.tools.google_tools.get_delegated_credentials"), \
             patch("api.src.sernia_ai.tools.google_tools.get_gmail_service", return_value=mock_service):
            ctx = _FakeRunContext()
            result = await read_email_thread(ctx, thread_id="thread1")

        assert "(ID: abc123)" in result

    @pytest.mark.asyncio
    async def test_zillow_content_cleaned_in_thread(self):
        """Zillow boilerplate should be stripped from thread messages."""
        zillow_body = (
            "What about when using AC?\n"
            "New message from a renter. "
            "A renter sent you a message about your listing at "
            "659 Maryland Ave #4, Shadyside, PA 15232. "
            "You can reply on Zillow or directly to this email."
        )
        thread_data = {
            "messages": [
                _make_gmail_message(
                    msg_id="z1", thread_id="thread_z",
                    from_addr="lead@convo.zillow.com",
                    to_addr="emilio@serniacapital.com",
                    subject="Inquiry about 659 Maryland Ave",
                    date="Mon, 13 Apr 2026 10:00:00 +0000",
                    body_text=zillow_body,
                ),
            ]
        }
        thread_data["messages"][0]["internalDate"] = "1000000"

        mock_service = MagicMock()
        mock_service.users().threads().get().execute.return_value = thread_data

        with patch("api.src.sernia_ai.tools.google_tools.get_delegated_credentials"), \
             patch("api.src.sernia_ai.tools.google_tools.get_gmail_service", return_value=mock_service):
            ctx = _FakeRunContext()
            result = await read_email_thread(ctx, thread_id="thread_z")

        assert "What about when using AC?" in result
        assert "New message from a renter" not in result
        assert "You can reply on Zillow" not in result


# ---------------------------------------------------------------------------
# Live Tests (real Gmail API)
# ---------------------------------------------------------------------------

# Real thread: "Samantha is requesting information about 659 Maryland Ave #3"
# 9-message Zillow lead thread between Samantha Jurczyk and Sernia Capital
# Thread IDs are mailbox-specific — same conversation has different IDs per inbox
_SAMANTHA_THREAD_ID_EMILIO = "19caf1e244d20176"  # emilio@serniacapital.com
_SAMANTHA_THREAD_ID_ALL = "19caf1e113d3f75d"  # all@serniacapital.com

# Real thread: "Nelson Chang is requesting information about 659 Maryland Ave #4"
# Zillow lead thread — used to verify Zillow cleanup, message IDs, and
# that the reply from all@serniacapital.com is visible.
_NELSON_THREAD_ID_EMILIO = "19d749e8a5b36ccf"  # emilio@serniacapital.com

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
            user_email_account="all@serniacapital.com",
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


# python -m pytest -m live api/src/tests/test_google_tools.py::TestNelsonChangThreadLive -v -s
class TestNelsonChangThreadLive:
    """Live tests for the Nelson Chang / 659 Maryland Ave #4 Zillow thread.

    This thread was the original bug report — the reply from all@serniacapital.com
    was missing because the thread was truncated. These tests verify that Zillow
    cleanup + summarization keep the thread complete, and that message IDs are
    present for daisy-chaining with send_email.
    """
    pytestmark = _live

    @pytest.mark.asyncio
    async def test_reads_nelson_thread_with_all_replies(self):
        """Read the Nelson Chang thread and verify the reply from all@ is visible."""
        ctx = _FakeRunContext(user_email="emilio@serniacapital.com")
        result = await read_email_thread(ctx, thread_id=_NELSON_THREAD_ID_EMILIO)

        # Should have messages
        assert "Message 1/" in result
        # Known participants
        assert "Nelson Chang" in result or "nelson" in result.lower()
        assert "659 Maryland" in result

        # The reply from all@serniacapital.com should be present
        # (this was the bug — it was truncated away before)
        assert "serniacapital.com" in result.lower()

        print(f"\n--- Nelson Chang thread ({len(result)} chars) ---")
        print(result)

    @pytest.mark.asyncio
    async def test_zillow_boilerplate_stripped(self):
        """Zillow boilerplate should be cleaned from Nelson Chang thread messages."""
        ctx = _FakeRunContext(user_email="emilio@serniacapital.com")
        result = await read_email_thread(ctx, thread_id=_NELSON_THREAD_ID_EMILIO)

        # Zillow boilerplate should NOT appear
        assert "New message from a renter" not in result
        assert "Reply on Zillow" not in result
        assert "For your safety" not in result

        print(f"\n--- Zillow cleanup verified ({len(result)} chars) ---")

    @pytest.mark.asyncio
    async def test_message_ids_present(self):
        """Each message should include its Gmail message ID for reply chaining."""
        ctx = _FakeRunContext(user_email="emilio@serniacapital.com")
        result = await read_email_thread(ctx, thread_id=_NELSON_THREAD_ID_EMILIO)

        # Message IDs should be in the format "(ID: <hex>)"
        import re
        id_matches = re.findall(r"\(ID: ([a-f0-9]+)\)", result)
        assert len(id_matches) >= 2, (
            f"Expected at least 2 message IDs, found {len(id_matches)}: {id_matches}"
        )

        print(f"\n--- Found {len(id_matches)} message IDs: {id_matches} ---")


# python -m pytest -m live api/src/tests/test_google_tools.py::TestSendHtmlEmailLive -v -s
class TestSendHtmlEmailLive:
    """Live test for HTML-bodied send_email.

    Sends one real email through the Gmail service layer with a multipart/
    alternative body (intro + table with hyperlinked cells + signature).
    Recipient is Emilio's personal address.
    """
    pytestmark = _live

    @pytest.mark.asyncio
    async def test_sends_html_email_with_table_and_links(self):
        from datetime import datetime

        from api.src.google.common.service_account_auth import get_delegated_credentials
        from api.src.google.gmail.service import send_email as _send_email

        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        subject = f"[Sernia AI test] HTML email — {timestamp}"

        plain_body = (
            f"Hi Emilio,\n\n"
            f"This is a test of the new HTML-bodied send_email tool. The HTML "
            f"version of this message includes a formatted vacancy status table "
            f"with hyperlinked listing columns.\n\n"
            f"Property        Unit   Status            Listing\n"
            f"320 S Mathilda  02     Available         https://www.zillow.com/homedetails/320-S-Mathilda/\n"
            f"324 S Mathilda  04     Tour scheduled    https://www.zillow.com/homedetails/324-S-Mathilda/\n"
            f"659 Maryland    03     Available         https://drive.google.com/drive/folders/photos\n\n"
            f"Best,\n"
            f"Sernia AI Intern\n"
            f"Sernia Capital LLC\n"
            f"emilio@serniacapital.com | (412) 910-1989"
        )

        html_body = """\
<!DOCTYPE html>
<html>
<body style="font-family: -apple-system, Segoe UI, Roboto, Helvetica, Arial, sans-serif; color: #1f2937; max-width: 640px; line-height: 1.5;">
  <p>Hi Emilio,</p>
  <p>
    This is a test of the new <strong>HTML-bodied <code>send_email</code></strong> tool.
    Below is a sample vacancy status table with <em>hyperlinked listing</em> columns —
    confirming that <code>multipart/alternative</code> with rich formatting renders cleanly
    in Gmail and Apple Mail.
  </p>

  <table style="border-collapse: collapse; width: 100%; margin: 16px 0; font-size: 14px;">
    <thead>
      <tr style="background: #f3f4f6; text-align: left;">
        <th style="border: 1px solid #d1d5db; padding: 8px;">Property</th>
        <th style="border: 1px solid #d1d5db; padding: 8px;">Unit</th>
        <th style="border: 1px solid #d1d5db; padding: 8px;">Status</th>
        <th style="border: 1px solid #d1d5db; padding: 8px;">Listing</th>
      </tr>
    </thead>
    <tbody>
      <tr>
        <td style="border: 1px solid #d1d5db; padding: 8px;">320 S Mathilda</td>
        <td style="border: 1px solid #d1d5db; padding: 8px;">02</td>
        <td style="border: 1px solid #d1d5db; padding: 8px; color: #059669;"><strong>Available</strong></td>
        <td style="border: 1px solid #d1d5db; padding: 8px;">
          <a href="https://www.zillow.com/homedetails/320-S-Mathilda/" style="color: #2563eb;">View on Zillow</a>
        </td>
      </tr>
      <tr style="background: #fafafa;">
        <td style="border: 1px solid #d1d5db; padding: 8px;">324 S Mathilda</td>
        <td style="border: 1px solid #d1d5db; padding: 8px;">04</td>
        <td style="border: 1px solid #d1d5db; padding: 8px; color: #b45309;">Tour scheduled</td>
        <td style="border: 1px solid #d1d5db; padding: 8px;">
          <a href="https://www.zillow.com/homedetails/324-S-Mathilda/" style="color: #2563eb;">View on Zillow</a>
        </td>
      </tr>
      <tr>
        <td style="border: 1px solid #d1d5db; padding: 8px;">659 Maryland</td>
        <td style="border: 1px solid #d1d5db; padding: 8px;">03</td>
        <td style="border: 1px solid #d1d5db; padding: 8px; color: #059669;"><strong>Available</strong></td>
        <td style="border: 1px solid #d1d5db; padding: 8px;">
          <a href="https://drive.google.com/drive/folders/photos" style="color: #2563eb;">Photos &amp; floorplan</a>
        </td>
      </tr>
    </tbody>
  </table>

  <p>Three things to verify on your end:</p>
  <ol>
    <li>The table renders with borders and the header row shaded.</li>
    <li>The four <em>Listing</em> cells are clickable hyperlinks.</li>
    <li>Status badges are color-coded (green = available, amber = tour scheduled).</li>
  </ol>

  <p style="margin-top: 32px;">Best,</p>
  <p style="margin: 0;"><strong>Sernia AI Intern</strong></p>
  <p style="margin: 0; color: #6b7280; font-size: 13px;">Sernia Capital LLC</p>
  <p style="margin: 0; color: #6b7280; font-size: 13px;">
    <a href="mailto:emilio@serniacapital.com" style="color: #2563eb;">emilio@serniacapital.com</a>
    &nbsp;|&nbsp; (412) 910-1989
  </p>
</body>
</html>
"""

        credentials = get_delegated_credentials(
            user_email="emilio@serniacapital.com",
            scopes=["https://mail.google.com"],
        )

        result = await _send_email(
            to="espo412@gmail.com",
            subject=subject,
            message_text=plain_body,
            message_html=html_body,
            sender="Sernia AI Intern <emilio@serniacapital.com>",
            credentials=credentials,
        )

        assert result.get("id"), f"No message ID returned: {result}"
        print(f"\n--- HTML email sent: id={result['id']} thread={result.get('threadId')} ---")
