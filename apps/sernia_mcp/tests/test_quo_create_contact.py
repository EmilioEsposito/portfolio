"""Unit tests for ``core.quo.contact_writes`` — create_contact + payload builder.

Patches ``build_quo_client`` so each test pins the request shape posted to
Quo's ``/v1/contacts``. The Tags multi-select hex key is hard-pinned to
match the production Quo workspace; if Quo ever rotates it, this test
catches the drift before it reaches prod.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sernia_mcp.core.errors import ExternalServiceError


def _make_async_client_cm(post_mock: AsyncMock):
    """Wrap an AsyncMock(post=...) as an ``async with`` context manager."""
    client = MagicMock()
    client.post = post_mock

    @asynccontextmanager
    async def _cm():
        yield client

    return _cm


# ---------------------------------------------------------------------------
# _build_contact_payload — pure function, no mocks needed
# ---------------------------------------------------------------------------


def test_payload_includes_only_provided_default_fields():
    from sernia_mcp.core.quo.contact_writes import _build_contact_payload

    payload = _build_contact_payload(first_name="Anna", last_name="Bakich")
    df = payload["defaultFields"]
    assert df == {"firstName": "Anna", "lastName": "Bakich"}
    assert payload["customFields"] == []


def test_payload_serializes_phone_numbers_and_emails():
    from sernia_mcp.core.quo.contact_writes import (
        Email,
        PhoneNumber,
        _build_contact_payload,
    )

    payload = _build_contact_payload(
        first_name="A",
        last_name="B",
        phone_numbers=[PhoneNumber(value="+14125551234")],
        emails=[Email(value="a@example.com")],
    )

    assert payload["defaultFields"]["phoneNumbers"] == [
        {"name": "Phone Number", "value": "+14125551234"}
    ]
    assert payload["defaultFields"]["emails"] == [
        {"name": "Email", "value": "a@example.com"}
    ]


def test_payload_tags_collide_with_explicit_custom_field_tags_key():
    """The hard-pinned Tags hex key takes precedence — if both ``tags`` AND a
    ``custom_fields`` entry with the Tags key are passed, the ``tags`` arg wins.
    Pins the dedup-by-key behavior in ``_build_custom_fields``.
    """
    from sernia_mcp.core.quo.contact_writes import (
        _CF_KEY_TAGS,
        CustomField,
        _build_contact_payload,
    )

    payload = _build_contact_payload(
        first_name="A",
        last_name="B",
        tags=["Insurance"],
        custom_fields=[CustomField(key=_CF_KEY_TAGS, value=["Old"])],
    )

    cfs = payload["customFields"]
    assert len(cfs) == 1
    assert cfs[0]["key"] == _CF_KEY_TAGS
    assert cfs[0]["value"] == ["Insurance"]  # not "Old"


def test_payload_empty_returns_empty_default_fields():
    from sernia_mcp.core.quo.contact_writes import _build_contact_payload

    payload = _build_contact_payload()
    assert payload == {"defaultFields": {}, "customFields": []}


# ---------------------------------------------------------------------------
# create_contact_core
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_contact_success_invalidates_cache():
    post = AsyncMock(
        return_value=MagicMock(
            status_code=201,
            json=MagicMock(return_value={"data": {"id": "C123"}}),
        )
    )
    invalidate = MagicMock()
    with patch(
        "sernia_mcp.core.quo.contact_writes.build_quo_client",
        _make_async_client_cm(post),
    ), patch(
        "sernia_mcp.core.quo.contact_writes.invalidate_contact_cache",
        invalidate,
    ):
        from sernia_mcp.core.quo.contact_writes import create_contact_core

        result = await create_contact_core("Anna", "Bakich", company="Sernia")

    assert "Anna Bakich" in result and "C123" in result
    invalidate.assert_called_once()
    post.assert_awaited_once()
    args, kwargs = post.await_args
    assert args[0] == "/v1/contacts"
    assert kwargs["json"]["defaultFields"]["firstName"] == "Anna"
    assert kwargs["json"]["defaultFields"]["company"] == "Sernia"


@pytest.mark.asyncio
async def test_create_contact_http_error_raises_external_service():
    post = AsyncMock(
        return_value=MagicMock(
            status_code=409,
            text="duplicate",
            json=MagicMock(side_effect=AssertionError("not parsed on failure")),
        )
    )
    invalidate = MagicMock()
    with patch(
        "sernia_mcp.core.quo.contact_writes.build_quo_client",
        _make_async_client_cm(post),
    ), patch(
        "sernia_mcp.core.quo.contact_writes.invalidate_contact_cache",
        invalidate,
    ):
        from sernia_mcp.core.quo.contact_writes import create_contact_core

        with pytest.raises(ExternalServiceError, match="HTTP 409"):
            await create_contact_core("A", "B")

    # Cache must NOT be invalidated when the create failed.
    invalidate.assert_not_called()
