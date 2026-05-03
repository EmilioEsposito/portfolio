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
        names = {t.name for t in await c.list_tools()}

    expected_visible = {
        "sernia_context",
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
        "clickup_get_tasks",
        "clickup_get_maintenance_field_options",
        "quo_create_contact",
        "quo_send_sms",
        "google_send_email",
    }
    missing = expected_visible - names
    assert not missing, f"Missing tools from /tools/list: {missing}"

    # Hidden backend tools must NOT appear in tools/list.
    assert "_confirm_send_sms" not in names
    assert "_confirm_send_email" not in names


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
