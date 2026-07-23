"""Unit tests for Quo thread/timestamp rendering — no API keys, no network.

These lock in the fix for the "year-old thread read as a new report" bug: the
Quo read tools now annotate every activity timestamp with its relative age and
flag long-dormant threads as STALE, so the agent never has to diff raw ISO
dates against "today" (the exact-one-year collision it has failed on before).
"""

from datetime import UTC, datetime

import httpx
import pytest

from api.src.open_phone.service import invalidate_contact_cache
from api.src.sernia_ai.tools.quo_tools import (
    STALE_THREAD_DAYS,
    _format_group_activity_line,
    _relative_age,
    _render_group_thread_from_db,
    _render_thread,
    _ts,
    list_active_threads_impl,
)

# A fixed "now" so age math is deterministic. This is exactly one year and a
# few hours after James's real July 23, 2025 leak text from the bug report.
NOW = datetime(2026, 7, 23, 13, 0, 0, tzinfo=UTC)
YEAR_AGO = "2025-07-23T08:58:00Z"


# --------------------------------------------------------------------------- #
# _relative_age
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("created", "expected"),
    [
        (YEAR_AGO, "1 year ago"),  # the exact collision from the bug
        ("2024-07-23T08:58:00Z", "2 years ago"),
        ("2026-07-23T12:59:30Z", "just now"),
        ("2026-07-23T12:30:00Z", "30 minutes ago"),
        ("2026-07-23T05:00:00Z", "8 hours ago"),
        ("2026-07-20T13:00:00Z", "3 days ago"),
        ("2026-05-23T13:00:00Z", "2 months ago"),
    ],
)
def test_relative_age_buckets(created: str, expected: str):
    assert _relative_age(created, NOW) == expected


def test_relative_age_unparseable_returns_empty():
    assert _relative_age("not-a-date", NOW) == ""
    assert _relative_age(None, NOW) == ""
    assert _relative_age("", NOW) == ""


def test_relative_age_future_is_flagged():
    assert _relative_age("2026-07-24T13:00:00Z", NOW) == "in the future"


def test_ts_appends_age_and_falls_back():
    assert _ts(YEAR_AGO, NOW) == f"{YEAR_AGO} · 1 year ago"
    # Unparseable/missing timestamps degrade to a bare label, never a crash.
    assert _ts(None, NOW) == "?"
    assert _ts("garbage", NOW) == "garbage"


# --------------------------------------------------------------------------- #
# Thread renderers embed the age on every line
# --------------------------------------------------------------------------- #


def test_render_thread_annotates_year_old_message():
    messages = [
        {
            "createdAt": YEAR_AGO,
            "direction": "incoming",
            "text": "Good morning, it appears there's a leak this morning",
            "from": "+14125379335",
        }
    ]
    out = _render_thread(
        messages,
        [],
        "James Gammiere",
        "+14125379335",
        {"+14125379335": "James Gammiere"},
        now=NOW,
    )
    # The "this morning" wording is a year old — the age must be right there.
    assert "1 year ago" in out
    assert YEAR_AGO in out


def test_group_activity_line_annotates_age():
    line = _format_group_activity_line(
        {"_kind": "message", "createdAt": YEAR_AGO, "from": "+1", "to": ["+2"], "text": "hi"},
        {},
        now=NOW,
    )
    assert "1 year ago" in line


def test_render_group_thread_from_db_annotates_age():
    out = _render_group_thread_from_db(
        [{"_kind": "message", "createdAt": YEAR_AGO, "from": "+1", "to": ["+2"], "text": "hi"}],
        ["+1", "+2"],
        {},
        now=NOW,
    )
    assert "1 year ago" in out


# --------------------------------------------------------------------------- #
# list_active_threads_impl flags stale threads (mocked Quo API)
# --------------------------------------------------------------------------- #

FRESH_CONV_ID = "CONVfresh"
STALE_CONV_ID = "CONVstale"
FRESH_PHONE = "+14120000001"
STALE_PHONE = "+14125379335"

_CONTACTS = [
    {
        "defaultFields": {
            "firstName": "Fresh",
            "lastName": "Tenant",
            "phoneNumbers": [{"value": FRESH_PHONE}],
        }
    },
    {
        "defaultFields": {
            "firstName": "James",
            "lastName": "Gammiere",
            "phoneNumbers": [{"value": STALE_PHONE}],
        }
    },
]

_CONVERSATIONS = [
    {
        "id": FRESH_CONV_ID,
        "participants": [FRESH_PHONE],
        "lastActivityAt": "2026-07-21T13:00:00Z",  # 2 days ago
    },
    {
        "id": STALE_CONV_ID,
        "participants": [STALE_PHONE],
        "lastActivityAt": YEAR_AGO,  # a year ago — dormant, never marked done
    },
]

_LATEST_MSG = {
    FRESH_PHONE: {
        "createdAt": "2026-07-21T13:00:00Z",
        "direction": "incoming",
        "text": "Trash day reminder?",
        "from": FRESH_PHONE,
    },
    STALE_PHONE: {
        "createdAt": YEAR_AGO,
        "direction": "incoming",
        "text": "Good morning, it appears there's a leak this morning",
        "from": STALE_PHONE,
    },
}


def _mock_handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path == "/v1/conversations":
        return httpx.Response(200, json={"data": _CONVERSATIONS, "nextPageToken": None})
    if path == "/v1/contacts":
        return httpx.Response(200, json={"data": _CONTACTS, "nextPageToken": None})
    if path == "/v1/messages":
        phone = request.url.params.get("participants")
        msg = _LATEST_MSG.get(phone)
        return httpx.Response(200, json={"data": [msg] if msg else []})
    if path == "/v1/calls":
        return httpx.Response(200, json={"data": []})
    return httpx.Response(404, json={"data": []})


@pytest.mark.asyncio
async def test_list_active_threads_flags_stale_thread(monkeypatch):
    """A year-old thread that was never marked 'done' still lists as active —
    it must be flagged STALE with a header warning so the agent doesn't treat
    it as a new report (the root cause of the false ClickUp task)."""
    invalidate_contact_cache()

    # Pin "now" so the year-old thread is deterministically past the threshold.
    import api.src.sernia_ai.tools.quo_tools as qt

    class _FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW

    monkeypatch.setattr(qt, "datetime", _FixedDateTime)

    async with httpx.AsyncClient(
        base_url="https://api.openphone.com",
        transport=httpx.MockTransport(_mock_handler),
    ) as client:
        out = await list_active_threads_impl(client, max_results=20)

    invalidate_contact_cache()

    # The dormant thread is flagged and explained.
    assert "⚠️ STALE" in out
    assert f"over {STALE_THREAD_DAYS} days" in out
    assert "do NOT treat it as a new report" in out
    assert "1 year ago" in out  # James's last-activity age
    # The fresh thread carries an age but no STALE prefix.
    assert "2 days ago" in out
    assert "⚠️ STALE — Thread: Fresh" not in out
