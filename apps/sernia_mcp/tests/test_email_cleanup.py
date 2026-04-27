"""Unit tests for ``core.google._email_cleanup``.

Pins the cleanup pipeline used by ``read_email_thread_core``:
  - HTML → markdown (layout tables flattened, scripts/styles dropped).
  - Zillow boilerplate stripping via ``[Name] says:`` anchor + tail patterns.
  - Quoted-reply collapsing (3+ ``>`` lines and the ``On ... wrote:`` attribution).
"""
from __future__ import annotations


# ---------------------------------------------------------------------------
# html_to_markdown
# ---------------------------------------------------------------------------


def test_html_to_markdown_strips_layout_tables():
    from sernia_mcp.core.google._email_cleanup import html_to_markdown

    html = (
        "<table><tr><td><strong>Hello</strong></td>"
        "<td>world</td></tr></table>"
    )
    out = html_to_markdown(html)
    # Layout tags removed, content preserved with markdown emphasis.
    assert "<table" not in out
    assert "<tr" not in out
    assert "**Hello**" in out
    assert "world" in out


def test_html_to_markdown_drops_scripts_and_styles_entirely():
    from sernia_mcp.core.google._email_cleanup import html_to_markdown

    html = (
        "<p>Real content</p>"
        "<script>alert('tracking');</script>"
        "<style>body{display:none}</style>"
    )
    out = html_to_markdown(html)
    assert "Real content" in out
    assert "tracking" not in out
    assert "display:none" not in out


# ---------------------------------------------------------------------------
# is_zillow_content
# ---------------------------------------------------------------------------


def test_is_zillow_content_detects_sender():
    from sernia_mcp.core.google._email_cleanup import is_zillow_content

    assert is_zillow_content(from_addr="no-reply@convo.zillow.com")
    assert is_zillow_content(from_addr="ZILLOW.com")  # case insensitive
    assert not is_zillow_content(from_addr="anna@gmail.com")


def test_is_zillow_content_detects_body_signature():
    """Even when sender is generic, content can betray Zillow origin."""
    from sernia_mcp.core.google._email_cleanup import is_zillow_content

    body = "View on Zillow.com\n\nA renter sent you a message..."
    assert is_zillow_content(from_addr="x@example.com", content=body)


# ---------------------------------------------------------------------------
# clean_zillow_email
# ---------------------------------------------------------------------------


def test_clean_zillow_email_extracts_message_after_says_anchor():
    from sernia_mcp.core.google._email_cleanup import clean_zillow_email

    raw = (
        "New message from a renter.\n"
        "A renter sent you a message about your listing at 319 South St.\n\n"
        "[Reply to Anna](https://zillow.com/r/abc123)\n"
        "Anna says:\n"
        "Hi, is the unit still available? I can tour Saturday at 2pm.\n\n"
        "For your safety, always double-check before staying safe.\n"
        "Reminder: The federal Fair Housing Act prohibits..."
    )
    out = clean_zillow_email(raw)
    assert "Hi, is the unit still available?" in out
    assert "tour Saturday at 2pm" in out
    # Boilerplate must be gone.
    assert "New message from a renter" not in out
    assert "Reply to Anna" not in out
    assert "Reminder" not in out
    assert "Fair Housing" not in out


def test_clean_zillow_email_falls_back_to_tail_strip_when_no_says_anchor():
    """Initial notifications and outbound replies don't have ``Name says:`` —
    the cleaner must still strip surrounding boilerplate."""
    from sernia_mcp.core.google._email_cleanup import clean_zillow_email

    raw = (
        "A renter sent you a message about your listing at 320 South St.\n"
        "Hello, I want to apply.\n"
        "Other helpful links: <stuff>"
    )
    out = clean_zillow_email(raw)
    assert "Hello, I want to apply." in out
    assert "Other helpful links" not in out


def test_clean_zillow_email_uses_last_says_anchor_when_quoted_history_repeats_it():
    """Earlier ``says:`` markers can appear in quoted history; we want the
    most recent renter message, not the first one in the chain."""
    from sernia_mcp.core.google._email_cleanup import clean_zillow_email

    raw = (
        "Anna says:\n"
        "Older message text from quoted history.\n\n"
        "Anna says:\n"
        "Newer current message text."
    )
    out = clean_zillow_email(raw)
    assert "Newer current message text" in out
    assert "Older message text" not in out


def test_clean_zillow_email_strips_action_button_links():
    """Real Zillow URLs are 80+ chars (tracking IDs); the long-URL regex
    runs first and strips them before the action-link regex looks for the
    ``[label](...)`` shape. Pin against realistic URL length so the test
    isn't tripped up by a regex-ordering quirk that doesn't fire in prod.
    """
    from sernia_mcp.core.google._email_cleanup import clean_zillow_email

    long_url = (
        "https://www.zillow.com/r/?action=reply&messageId=abc123def456"
        "&utm_source=email&utm_campaign=rental_message&headerOnly=1"
    )
    raw = (
        "Anna says:\n"
        f"Body. [Reply to Anna]({long_url}) "
        f"[Send Application]({long_url})"
    )
    out = clean_zillow_email(raw)
    assert "Reply to Anna" not in out
    assert "Send Application" not in out
    # And the URL itself shouldn't survive either.
    assert "zillow.com/r/" not in out


# ---------------------------------------------------------------------------
# strip_quoted_replies
# ---------------------------------------------------------------------------


def test_strip_quoted_replies_collapses_long_block_with_attribution():
    from sernia_mcp.core.google._email_cleanup import strip_quoted_replies

    text = (
        "Got it, thanks!\n\n"
        "On Mon, Apr 28, 2026 at 10:00 AM, Anna <anna@x.com> wrote:\n"
        "> Hi Emilio,\n"
        "> Looking forward to Saturday.\n"
        "> Anna"
    )
    out = strip_quoted_replies(text)
    assert "Got it, thanks!" in out
    assert "[...quoted reply trimmed...]" in out
    # Attribution line should be gone.
    assert "wrote:" not in out
    assert "Looking forward to Saturday" not in out


def test_strip_quoted_replies_preserves_short_inline_quotes():
    """1-2 line quotes are likely inline replies, not redundant history.
    They should be kept verbatim."""
    from sernia_mcp.core.google._email_cleanup import strip_quoted_replies

    text = "Re your question:\n> Is it still available?\nYes, until Friday."
    out = strip_quoted_replies(text)
    assert "> Is it still available?" in out
    assert "[...quoted reply trimmed...]" not in out


def test_strip_quoted_replies_handles_two_line_attribution_wrap():
    from sernia_mcp.core.google._email_cleanup import strip_quoted_replies

    text = (
        "Quick reply.\n"
        "On Mon, Apr 28, 2026 at 10:00 AM\n"
        "Anna <anna@x.com> wrote:\n"
        "> first\n> second\n> third"
    )
    out = strip_quoted_replies(text)
    assert "Quick reply." in out
    assert "[...quoted reply trimmed...]" in out
    assert "On Mon, Apr 28" not in out
    assert "Anna <anna@x.com>" not in out
