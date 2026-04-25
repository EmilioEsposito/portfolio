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
        "workspace_read",
        "workspace_write",
        "quo_search_contacts",
        "quo_get_thread_messages",
        "google_search_emails",
        "google_read_email",
        "clickup_search_tasks",
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
