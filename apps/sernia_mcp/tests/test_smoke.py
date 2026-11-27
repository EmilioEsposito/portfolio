"""Import + wiring smoke tests — run fast, no network, no API keys.

These verify the server module loads, all MCP tools register, the approvals
provider is mounted, and the public tool surface is what we expect.
"""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_server_module_imports():
    from sernia_mcp.server import mcp

    assert mcp is not None
    assert mcp.name == "sernia-mcp"


@pytest.mark.asyncio
async def test_expected_tools_exposed():
    from fastmcp import Client

    from sernia_mcp.server import mcp

    async with Client(mcp) as c:
        catalog = await c.list_tools()
        names = {t.name for t in catalog}

    expected_visible = {
        "sernia_context",
        "list_message_templates",
        "send_templated_message",
        "read_resource",
        "edit_resource",
        "write_resource",
        "quo_search_contacts",
        "quo_get_thread_messages",
        "quo_list_active_sms_threads",
        "quo_get_call_details",
        "google_search_emails",
        "google_read_email",
        "google_read_email_thread",
        "google_search_drive",
        "google_read_sheet",
        "google_read_doc",
        "google_read_pdf",
        "google_list_calendar_events",
        "clickup_search_tasks",
        "clickup_create_task",
        "clickup_update_task",
        "clickup_set_task_custom_field",
        "clickup_list_lists",
        "clickup_list_members",
        "clickup_get_tasks",
        "clickup_get_maintenance_field_options",
        "quo_list_tasks",
        "quo_get_task",
        "quo_create_task",
        "quo_update_task",
        "quo_delete_task",
        "rental_listing_update",
        "showing_rules_update",
        "quo_create_contact",
        "quo_send_sms",
        "google_send_email",
    }
    missing = expected_visible - names
    assert not missing, f"Missing tools from /tools/list: {missing}"

    # Apps hosts discover every button target, but raw callback names stay unavailable.
    from fastmcp.server.providers.addressing import hashed_backend_name

    callbacks = {
        "_confirm_delete_quo_task",
        "_confirm_send_sms",
        "_confirm_send_email",
        "_confirm_rental_change",
    }
    assert names.isdisjoint(callbacks)
    app_only = {
        t.name for t in catalog if (t.meta or {}).get("ui", {}).get("visibility") == ["app"]
    }
    assert {hashed_backend_name("sernia_approvals", name) for name in callbacks} <= app_only


@pytest.mark.asyncio
async def test_clerk_oauth_flag_default_off_in_tests():
    """Tests run unauthenticated — config must report this honestly."""
    from sernia_mcp.config import clerk_oauth_configured

    assert clerk_oauth_configured() is False


def test_clerk_domain_with_scheme_rejected(monkeypatch):
    """A scheme prefix in the Clerk domain would produce doubled OAuth URLs.
    The auth-provider builder must fail loudly so deploy logs catch it.
    """
    import pytest

    monkeypatch.setenv("FASTMCP_SERVER_AUTH_CLERK_DOMAIN", "https://example.clerk.accounts.dev")
    monkeypatch.setenv("FASTMCP_SERVER_AUTH_CLERK_CLIENT_ID", "x")
    monkeypatch.setenv("FASTMCP_SERVER_AUTH_CLERK_CLIENT_SECRET", "y")
    monkeypatch.setenv("SERNIA_MCP_BASE_URL", "https://example.com")

    from sernia_mcp.server import _build_auth_provider

    with pytest.raises(RuntimeError, match="bare hostname"):
        _build_auth_provider()
