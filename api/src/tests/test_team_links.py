"""Links to the team's pages: the site per environment, and the links built from it."""

from unittest.mock import AsyncMock

import pytest

from api.src.utils import team_links


@pytest.mark.parametrize(
    "environment, site",
    [
        ("production", "https://serniacapital.com"),
        ("development", "https://dev.eesposito.com"),
        ("portfolio-pr-123", "http://localhost:5173"),
        ("", "http://localhost:5173"),
        (None, "http://localhost:5173"),
    ],
)
def test_the_site_follows_the_railway_environment(monkeypatch, environment, site):
    if environment is None:
        monkeypatch.delenv("RAILWAY_ENVIRONMENT_NAME", raising=False)
    else:
        monkeypatch.setenv("RAILWAY_ENVIRONMENT_NAME", environment)
    assert team_links.base_url() == site
    assert team_links.team_url(team_links.CHAT_PATH) == f"{site}/team/chat"


def test_every_team_page_is_under_team():
    paths = {
        name: value
        for name, value in vars(team_links).items()
        if name.endswith("_PATH") and isinstance(value, str)
    }
    assert paths == {
        "CHAT_PATH": "/team/chat",
        "SETTINGS_PATH": "/team/settings",
        "ADMIN_PATH": "/team/admin",
        "TENANTS_PATH": "/team/tenants",
        "SCHEDULER_PATH": "/team/scheduler",
        "LEADS_PATH": "/team/leads",
        "LEAD_LISTINGS_PATH": "/team/leads/listings",
        "LEAD_SETTINGS_PATH": "/team/leads/settings",
        "LEAD_BOOKINGS_PATH": "/team/leads/bookings",
    }


def test_a_conversation_link_carries_its_id():
    assert team_links.chat_path("conv-abc-123") == "/team/chat?id=conv-abc-123"
    # An id is encoded, so it cannot add to the query.
    assert team_links.chat_path("a&b=c") == "/team/chat?id=a%26b%3Dc"


def test_the_sms_review_link_is_absolute_on_the_production_site(monkeypatch):
    from api.src.sernia_ai import sms_approvals

    monkeypatch.setenv("RAILWAY_ENVIRONMENT_NAME", "production")
    assert sms_approvals.review_link("conv-abc-123") == (
        "https://serniacapital.com/team/chat?id=conv-abc-123"
    )


@pytest.mark.asyncio
async def test_approval_and_alert_pushes_open_the_team_chat(monkeypatch):
    from api.src.agent_conversations.push import service

    notify_all = AsyncMock()
    monkeypatch.setattr(service, "notify_all_sernia_users", notify_all)

    await service.notify_pending_approval("conv-abc-123", "send_email", {"to": "a@example.com"})
    await service.notify_trigger_alert("conv-abc-123", "sms", "Title", "Body")

    urls = [call.kwargs["data"]["url"] for call in notify_all.await_args_list]
    # Relative: the browser opens it on the site its notifications were turned on.
    assert urls == ["/team/chat?id=conv-abc-123", "/team/chat?id=conv-abc-123"]


def test_calendar_connections_start_from_the_team_sites():
    from api.src.showing_bookings import calendar_callback, google_oauth

    assert google_oauth.ALLOWED_ORIGINS == {
        "https://serniacapital.com",
        "https://dev.eesposito.com",
    }
    assert calendar_callback.back_to_tab("connected") == (
        "/team/leads/bookings?calendar=connected#calendar-connections"
    )
