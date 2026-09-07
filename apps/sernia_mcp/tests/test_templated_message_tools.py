"""MCP round trips exercise validation, exact transport payloads and discovery."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

from tests.test_message_templates import contact


@pytest.mark.asyncio
async def test_mcp_discovery_and_simulcast(monkeypatch):
    from sernia_mcp.server import mcp
    from sernia_mcp.tools import templated_messages as mod

    calls = []

    def handler(req):
        calls.append(req)
        if req.method == "GET":
            assert req.url.path == "/v1/contacts/CT_test"
            return httpx.Response(200, json={"data": contact()})
        return httpx.Response(202, json={"data": {"id": "sms-id"}})

    monkeypatch.setattr(
        mod,
        "build_quo_client",
        lambda: httpx.AsyncClient(
            base_url="https://mock.invalid", transport=httpx.MockTransport(handler)
        ),
    )
    email = AsyncMock(return_value=SimpleNamespace(message_id="email-id"))
    monkeypatch.setattr(mod, "send_email_core", email)
    async with Client(mcp) as client:
        tools = {t.name: t for t in await client.list_tools()}
        schema = tools["send_templated_message"].inputSchema
        assert set(schema["properties"]) == {"template_id", "contact_id", "delivery"}
        catalog = await client.call_tool("list_message_templates", {})
        assert "lease_end_mail_forwarding" in str(catalog)
        result = await client.call_tool(
            "send_templated_message",
            {
                "template_id": "lease_end_mail_forwarding",
                "contact_id": "CT_test",
                "delivery": "both",
            },
        )
        assert "accepted" in str(result)
    import json

    body = mod.list_templates()[0].body
    assert json.loads(calls[1].content) == {
        "to": ["+15551230000"],
        "from": mod.QUO_SHARED_EXTERNAL_PHONE_ID,
        "content": body,
    }
    email.assert_awaited_once_with(
        to=["tenant@example.com"],
        subject="Automated reminder: USPS mail forwarding",
        body=body,
        user_email="all@serniacapital.com",
        sender_override="all@serniacapital.com",
    )


@pytest.mark.parametrize(
    "extra", [{"body": "evil"}, {"template_id": "evil"}, {"contact_id": "../../messages"}]
)
@pytest.mark.asyncio
async def test_mcp_rejects_hijack_before_provider_access(monkeypatch, extra):
    from sernia_mcp.server import mcp
    from sernia_mcp.tools import templated_messages as mod

    build = AsyncMock(side_effect=AssertionError("Provider should not be reached"))
    monkeypatch.setattr(mod, "build_quo_client", build)
    async with Client(mcp) as client:
        with pytest.raises(ToolError):
            await client.call_tool(
                "send_templated_message",
                {
                    "template_id": "lease_end_mail_forwarding",
                    "contact_id": "CT_test",
                    "delivery": "sms",
                }
                | extra,
            )
    build.assert_not_called()
