"""PydanticAI tool wiring and provider payloads; no live sends."""

import json
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from pydantic_ai import Agent
from pydantic_ai.models.test import TestModel

from api.src.sernia_ai.tools import templated_message_tools as mod
from api.src.tests.test_message_templates import contact


@pytest.mark.asyncio
async def test_agent_tool_simulcast(monkeypatch):
    calls = []

    def handler(req):
        calls.append(req)
        if req.method == "GET":
            assert req.url.path == "/v1/contacts/CT_test"
            return httpx.Response(200, json={"data": contact()})
        return httpx.Response(202, json={"data": {"id": "sms-id"}})

    monkeypatch.setattr(
        mod,
        "_build_quo_client",
        lambda: httpx.AsyncClient(
            base_url="https://mock.invalid", transport=httpx.MockTransport(handler)
        ),
    )
    creds = Mock(return_value="credentials")
    monkeypatch.setattr(mod, "get_delegated_credentials", creds)
    email = AsyncMock(return_value={"id": "email-id"})
    monkeypatch.setattr(mod, "send_email", email)
    result = await mod.send_templated_message("lease_end_mail_forwarding", "CT_test", "both")
    assert [r.status for r in result.results] == ["accepted", "accepted"]
    body = mod.list_templates()[0].body
    assert json.loads(calls[1].content) == {
        "to": ["+15551230000"],
        "from": mod.QUO_SHARED_EXTERNAL_PHONE_ID,
        "content": body,
    }
    email.assert_awaited_once_with(
        to="tenant@example.com",
        subject="Automated reminder: USPS mail forwarding",
        message_text=body,
        sender=mod.SHARED_EXTERNAL_EMAIL,
        credentials="credentials",
    )
    assert creds.call_args.kwargs["user_email"] == "all@serniacapital.com"


@pytest.mark.asyncio
async def test_tool_schema_and_agent_discovery():
    from api.src.sernia_ai.agent import sernia_agent
    from api.src.sernia_ai.instructions import STATIC_INSTRUCTIONS

    assert "list_message_templates" in STATIC_INSTRUCTIONS
    assert any(
        getattr(t, "wrapped", None) is mod.templated_message_toolset
        for t in sernia_agent._user_toolsets
    )
    model = TestModel(call_tools=["list_message_templates"])
    agent = Agent(model, toolsets=[mod.templated_message_toolset])
    result = await agent.run("List the fixed reminders")
    assert "lease_end_mail_forwarding" in str(result.output)
    definition = next(
        t
        for t in model.last_model_request_parameters.function_tools
        if t.name == "send_templated_message"
    )
    assert set(definition.parameters_json_schema["properties"]) == {
        "template_id",
        "contact_id",
        "delivery",
    }
    assert definition.parameters_json_schema["additionalProperties"] is False


@pytest.mark.asyncio
async def test_general_external_email_still_requires_approval(monkeypatch):
    from types import SimpleNamespace

    from pydantic_ai import ApprovalRequired

    from api.src.sernia_ai.tools import google_tools

    send = AsyncMock()
    monkeypatch.setattr(google_tools, "_send_email", send)
    ctx = SimpleNamespace(
        tool_call_approved=False,
        deps=SimpleNamespace(
            user_email="emilio@serniacapital.com", bypass_external_email_approval=False
        ),
    )
    with pytest.raises(ApprovalRequired):
        await google_tools.send_email(ctx, ["tenant@example.com"], "Custom", "Anything else")
    send.assert_not_called()
