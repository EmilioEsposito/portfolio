from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException, Response

from api.src.agent_conversations.push import inventory, routes


pytestmark = pytest.mark.asyncio


def address(email, *, verified=True, key="primary"):
    return SimpleNamespace(
        id=key,
        email_address=email,
        verification=SimpleNamespace(status="verified" if verified else "unverified"),
    )


def member(key, *emails):
    return SimpleNamespace(id=key, primary_email_address_id="primary", email_addresses=emails)


@pytest.mark.parametrize(
    "emails",
    [
        [address("staff@serniacapital.com")],
        [address("emilio@serniacapital.com", verified=False)],
        [address("staff@serniacapital.com"), address("emilio@serniacapital.com", key="secondary")],
    ],
)
async def test_inventory_denies_other_accounts_before_lookup(emails, monkeypatch):
    lookup = AsyncMock()
    monkeypatch.setattr(routes, "team_registrations", lookup)
    with pytest.raises(HTTPException) as err:
        await routes.get_team_registrations(Response(), member("staff", *emails), AsyncMock())
    assert err.value.status_code == 403
    lookup.assert_not_awaited()


async def test_admin_sees_team_without_subscriptions_and_only_safe_registration_metadata(
    monkeypatch,
):
    admin = member("owner", address("emilio@serniacapital.com"))
    staff = member("staff", address("other@example.org"), address("ops@serniacapital.com", key="s"))
    outsider = member("outside", address("staff@serniacapital.com.example.org"))
    unverified = member("unverified", address("unverified@serniacapital.com", verified=False))
    users = AsyncMock(side_effect=[[admin, staff, *([outsider] * 97), unverified], []])
    monkeypatch.setattr(inventory.clerk_client.users, "list_async", users)
    now = datetime.now(UTC)
    registration = SimpleNamespace(
        clerk_user_id="owner",
        id=7,
        endpoint="https://web.push.apple.com/private",
        user_agent="iPhone",
        created_at=now,
        updated_at=now,
    )
    session = AsyncMock()
    session.execute.return_value = [registration]
    response = Response()
    roster = await routes.get_team_registrations(response, admin, session)
    assert response.headers["Cache-Control"] == "private, no-store"
    assert [m.email for m in roster] == ["emilio@serniacapital.com", "ops@serniacapital.com"]
    assert roster[1].registrations == []
    assert roster[0].registrations[0].model_dump() == {
        "id": 7,
        "ios_home_screen": True,
        "user_agent": "iPhone",
        "created_at": now,
        "updated_at": now,
    }
    assert users.await_args_list[1].kwargs["request"]["offset"] == 100
    assert session.execute.await_args.args[0].compile().params["clerk_user_id_1"] == [
        "owner",
        "staff",
    ]
