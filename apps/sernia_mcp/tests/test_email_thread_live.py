"""Live parity tests for ``read_email_thread_core`` against real Gmail threads.

Mirrors the assertions in ``api/src/tests/test_google_tools.py``
(``TestReadEmailThreadLive``, ``TestNelsonChangThreadLive``) so that any
divergence between sernia_ai's reference implementation and the MCP
server's vendored copy of the cleanup pipeline gets caught here.

Hits the real Google API. Requires:
  - ``GOOGLE_SERVICE_ACCOUNT_CREDENTIALS`` in env (base64 service-account JSON
    with domain-wide delegation enabled in Workspace admin).
  - The two real test threads still existing in the Sernia Gmail account.

Run:

    cd apps/sernia_mcp
    uv run pytest -m live tests/test_email_thread_live.py -v -s

Skipped by default (the ``live`` marker is excluded by ``pyproject.toml``'s
default ``addopts`` — see also `tests/conftest.py` and CLAUDE.md).
"""
from __future__ import annotations

import os

import pytest

# Real thread IDs — mirror api/src/tests/test_google_tools.py constants.
# These are mailbox-specific; thread IDs differ between emilio@ and all@
# even when the same logical conversation is involved.
_SAMANTHA_THREAD_ID_EMILIO = "19caf1e244d20176"
_SAMANTHA_THREAD_ID_ALL = "19caf1e113d3f75d"
_NELSON_THREAD_ID_EMILIO = "19d749e8a5b36ccf"

_LIVE = pytest.mark.live


@_LIVE
@pytest.mark.asyncio
async def test_samantha_thread_read_via_emilio_mailbox():
    """Same assertions as sernia_ai's TestReadEmailThreadLive::test_reads_full_zillow_thread.

    Confirms: thread loads, has multiple messages, contains known
    participants and content from the Samantha / 659 Maryland conversation.
    """
    if not os.environ.get("GOOGLE_SERVICE_ACCOUNT_CREDENTIALS"):
        pytest.skip("GOOGLE_SERVICE_ACCOUNT_CREDENTIALS not set")

    from sernia_mcp.core.google.gmail import read_email_thread_core

    result = await read_email_thread_core(
        _SAMANTHA_THREAD_ID_EMILIO, user_email="emilio@serniacapital.com"
    )

    # Structural invariants
    assert "Message 1/" in result, "expected at least one message header"
    assert "(ID: " in result, "expected per-message Gmail message IDs"

    # Content invariants — mirror sernia_ai
    assert "zillow.com" in result.lower(), "Zillow sender expected"
    assert "serniacapital.com" in result.lower(), "Sernia recipient expected"
    assert "659 Maryland" in result, "thread topic expected"
    assert "Samantha" in result, "renter name expected"

    # Cleanup invariants — these are the ones that broke when we previously
    # shipped read_email_thread without the cleanup pipeline.
    assert "New message from a renter" not in result, (
        "Zillow boilerplate header should be stripped"
    )
    assert "Reminder: The federal" not in result, (
        "Fair Housing reminder block should be stripped"
    )
    assert "Other helpful links" not in result, (
        "Zillow tail-link section should be stripped"
    )

    print(f"\n--- Samantha thread output ({len(result)} chars) ---")
    print(result[:3000])


@_LIVE
@pytest.mark.asyncio
async def test_samantha_thread_read_via_shared_mailbox():
    """Same thread, different mailbox — Gmail thread IDs are mailbox-scoped."""
    if not os.environ.get("GOOGLE_SERVICE_ACCOUNT_CREDENTIALS"):
        pytest.skip("GOOGLE_SERVICE_ACCOUNT_CREDENTIALS not set")

    from sernia_mcp.core.google.gmail import read_email_thread_core

    result = await read_email_thread_core(
        _SAMANTHA_THREAD_ID_ALL, user_email="all@serniacapital.com"
    )

    assert "Message 1/" in result
    assert "659 Maryland" in result

    print(f"\n--- Shared inbox thread ({len(result)} chars) ---")
    print(result[:2000])


@_LIVE
@pytest.mark.asyncio
async def test_samantha_thread_message_count_stable():
    """The Samantha thread had 9 messages when sernia_ai pinned it — same expectation here.

    Loose match to allow for new replies; primary assertion is that the
    "Message N/M" header reaches at least 9.
    """
    if not os.environ.get("GOOGLE_SERVICE_ACCOUNT_CREDENTIALS"):
        pytest.skip("GOOGLE_SERVICE_ACCOUNT_CREDENTIALS not set")

    from sernia_mcp.core.google.gmail import read_email_thread_core

    result = await read_email_thread_core(
        _SAMANTHA_THREAD_ID_EMILIO, user_email="emilio@serniacapital.com"
    )

    # Find the highest "Message N/M" we rendered.
    import re

    match = re.search(r"Message \d+/(\d+)", result)
    assert match is not None, "expected at least one Message N/M header"
    total = int(match.group(1))
    assert total >= 9, f"expected ≥ 9 messages in thread; saw {total}"


@_LIVE
@pytest.mark.asyncio
async def test_nelson_thread_includes_all_replies():
    """Nelson Chang / 659 Maryland #4 — the thread that exposed the original
    truncation bug in sernia_ai (the all@ reply was missing because the
    thread was being truncated). MCP must keep the full thread visible.
    """
    if not os.environ.get("GOOGLE_SERVICE_ACCOUNT_CREDENTIALS"):
        pytest.skip("GOOGLE_SERVICE_ACCOUNT_CREDENTIALS not set")

    from sernia_mcp.core.google.gmail import read_email_thread_core

    result = await read_email_thread_core(
        _NELSON_THREAD_ID_EMILIO, user_email="emilio@serniacapital.com"
    )

    assert "Message 1/" in result
    # Sernia_ai checks for "Nelson Chang" or lowercased — same here.
    assert "Nelson Chang" in result or "nelson" in result.lower()
    assert "659 Maryland" in result

    # Each rendered message header should expose its Gmail message ID for
    # daisy-chaining replies (matches sernia_ai's "Message ID:" format —
    # we use "(ID: ...)" inline instead, but the value must appear).
    import re

    ids = re.findall(r"\(ID: ([0-9a-f]{16})\)", result)
    assert len(ids) >= 2, f"expected ≥ 2 per-message Gmail IDs; saw {ids}"

    print(f"\n--- Nelson thread output ({len(result)} chars) ---")
    print(result[:3000])
