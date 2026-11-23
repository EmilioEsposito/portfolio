"""Dedicated lead settings: explicit opt-in, validated writes, and stale-save protection."""

import asyncio
from types import SimpleNamespace

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI, HTTPException
from sqlalchemy import delete

from api.src.database.app_settings import AppSetting
from api.src.database.database import AsyncSessionFactory
from api.src.zillow_leads import settings

# The three settings for replies that change a showing, as they are until saved.
BOOKING_OFF = {
    "auto_send_new_booking": False,
    "auto_send_reschedule": False,
    "auto_send_cancellation": False,
}


@pytest_asyncio.fixture(autouse=True)
async def clean_settings():
    async def clear():
        async with AsyncSessionFactory() as session:
            await session.execute(
                delete(AppSetting).where(
                    AppSetting.key.in_(
                        [
                            settings.SETTINGS_KEY,
                            "zillow_email_config",
                        ]
                    )
                )
            )
            await session.commit()

    await clear()
    yield
    await clear()


async def store(key, value):
    async with AsyncSessionFactory() as session:
        await session.merge(AppSetting(key=key, value=value))
        await session.commit()


def app(allowed=True):
    application = FastAPI()
    application.include_router(settings.router)

    async def caller():
        if not allowed:
            raise HTTPException(403, "A verified Sernia account is required.")
        return SimpleNamespace(id="test-user")

    application.dependency_overrides[settings.verify_serniacapital_user] = caller
    return application


@pytest_asyncio.fixture
async def client():
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app()), base_url="http://test"
    ) as client:
        yield client


@pytest.mark.asyncio
async def test_legacy_off_never_enables_dedicated_auto_sends(client):
    await store("zillow_email_config", {"debounce_seconds": 480, "require_approval": False})
    response = await client.get("/zillow-leads/settings")
    assert response.status_code == 200
    assert response.json() == {
        "debounce_seconds": 480,
        "require_approval": True,
        **BOOKING_OFF,
        "automation_enabled": False,
        "books_showings": False,
    }
    assert (await settings.effective_settings()).require_approval is True


@pytest.mark.asyncio
async def test_explicit_save_controls_only_dedicated_settings(client):
    old = {"debounce_seconds": 300, "require_approval": True}
    new = {"debounce_seconds": 120, "require_approval": False}
    result = await client.patch("/zillow-leads/settings", json={"settings": new, "based_on": old})
    assert result.status_code == 200
    assert result.json() == {**new, **BOOKING_OFF}
    assert (await settings.effective_settings()).model_dump() == {**new, **BOOKING_OFF}
    async with AsyncSessionFactory() as session:
        assert await session.get(AppSetting, "zillow_email_config") is None


@pytest.mark.asyncio
async def test_each_booking_setting_is_saved_on_its_own(client):
    old = {"debounce_seconds": 300, "require_approval": True}
    new = {**old, "require_approval": False, **BOOKING_OFF, "auto_send_reschedule": True}
    result = await client.patch("/zillow-leads/settings", json={"settings": new, "based_on": old})
    assert result.status_code == 200
    assert result.json() == new
    saved = await settings.effective_settings()
    assert (saved.auto_send_new_booking, saved.auto_send_reschedule) == (False, True)
    assert saved.auto_send_cancellation is False


@pytest.mark.asyncio
async def test_a_page_from_before_the_booking_settings_cannot_clear_them(client):
    """A save that leaves the three out is based on "all off": refused once one is on."""
    old = {"debounce_seconds": 300, "require_approval": True}
    on = {**old, "require_approval": False, **BOOKING_OFF, "auto_send_new_booking": True}
    await client.patch("/zillow-leads/settings", json={"settings": on, "based_on": old})
    stale = {"debounce_seconds": 300, "require_approval": False}
    response = await client.patch(
        "/zillow-leads/settings",
        json={"settings": {**stale, "debounce_seconds": 120}, "based_on": stale},
    )
    assert response.status_code == 409
    assert (await settings.effective_settings()).auto_send_new_booking is True


@pytest.mark.asyncio
async def test_the_page_is_told_whether_replies_can_change_a_showing(client, monkeypatch):
    from unittest.mock import AsyncMock

    monkeypatch.setattr(settings.lead_bookings, "enabled", AsyncMock(return_value=True))
    assert (await client.get("/zillow-leads/settings")).json()["books_showings"] is True


@pytest.mark.asyncio
async def test_a_stale_save_cannot_undo_someone_elses_approval_choice(client):
    old = {"debounce_seconds": 300, "require_approval": True}
    new = {"debounce_seconds": 300, "require_approval": False}
    await client.patch("/zillow-leads/settings", json={"settings": new, "based_on": old})
    response = await client.patch(
        "/zillow-leads/settings",
        json={"settings": {**old, "debounce_seconds": 120}, "based_on": old},
    )
    assert response.status_code == 409
    assert (await settings.effective_settings()).model_dump() == {**new, **BOOKING_OFF}


@pytest.mark.asyncio
async def test_two_first_saves_cannot_silently_overwrite_each_other(client):
    old = {"debounce_seconds": 300, "require_approval": True}
    responses = await asyncio.gather(
        *[
            client.patch(
                "/zillow-leads/settings",
                json={
                    "settings": {"debounce_seconds": seconds, "require_approval": False},
                    "based_on": old,
                },
            )
            for seconds in (120, 180)
        ]
    )
    assert sorted(response.status_code for response in responses) == [200, 409]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "patch",
    [
        {"require_approval": "false"},
        {"require_approval": 0},
        {"debounce_seconds": 0},
        {"debounce_seconds": 3601},
        {"auto_send_new_booking": "true"},
        {"auto_send_cancellation": 1},
        {"unknown": True},
    ],
)
async def test_invalid_settings_are_not_saved(client, patch):
    old = {"debounce_seconds": 300, "require_approval": True}
    result = await client.patch(
        "/zillow-leads/settings", json={"settings": {**old, **patch}, "based_on": old}
    )
    assert result.status_code == 422
    assert (await settings.effective_settings()).require_approval is True


@pytest.mark.asyncio
async def test_invalid_stored_settings_keep_approval_on():
    await store(settings.SETTINGS_KEY, {"debounce_seconds": 300, "require_approval": "false"})
    assert (await settings.effective_settings()).require_approval is True


@pytest.mark.asyncio
async def test_settings_require_a_verified_sernia_user():
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app(False)), base_url="http://test"
    ) as client:
        assert (await client.get("/zillow-leads/settings")).status_code == 403
        assert (await client.patch("/zillow-leads/settings", json={})).status_code == 403
    assert [dep.dependency for dep in settings.router.dependencies] == [
        settings.verify_serniacapital_user
    ]


@pytest.mark.asyncio
async def test_canonical_and_compatibility_urls_share_settings(monkeypatch):
    from api.index import app as api_app
    from api.src.agent_conversations import auth

    async def caller(*_):
        return SimpleNamespace(id="test-user")

    monkeypatch.setattr(auth, "verify_serniacapital_user", caller)

    # The feature router uses Depends; the legacy parent invokes the same
    # verifier directly. Exercise both real mounts without a provider call.
    async def dependency():
        return await caller()

    monkeypatch.setitem(
        api_app.dependency_overrides, settings.verify_serniacapital_user, dependency
    )
    old = {"debounce_seconds": 300, "require_approval": True}
    new = {"debounce_seconds": 180, "require_approval": False}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=api_app), base_url="http://test"
    ) as client:
        saved = await client.patch(
            "/api/zillow-leads/settings", json={"settings": new, "based_on": old}
        )
        assert saved.status_code == 200
        for url in ("/api/zillow-leads/settings", "/api/sernia-ai/zillow-leads/settings"):
            response = await client.get(url)
            assert response.status_code == 200
            assert response.json() == {
                **new,
                **BOOKING_OFF,
                "automation_enabled": False,
                "books_showings": False,
            }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url", ["/api/zillow-leads/settings", "/api/sernia-ai/zillow-leads/settings"]
)
async def test_both_real_url_mounts_reject_unauthenticated_requests(url):
    from api.index import app as api_app

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=api_app), base_url="http://test"
    ) as client:
        assert (await client.get(url)).status_code == 401
        assert (await client.patch(url, json={})).status_code == 401
