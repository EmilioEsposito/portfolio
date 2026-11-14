"""HTTP compatibility, handler isolation, and approval behavior after extraction."""

import asyncio
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from pydantic_ai import Agent
from pydantic_ai.exceptions import ModelHTTPError
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, ToolCallPart, UserPromptPart
from pydantic_ai.models.function import FunctionModel
from sqlalchemy import delete

from api.index import app
from api.src.agent_conversations import routes
from api.src.agent_conversations.models import AgentConversation, save_agent_conversation
from api.src.database.database import AsyncSessionFactory
from api.src.utils import clerk
from api.src.zillow_leads import handle
from api.src.zillow_leads.identity import HANDLER

PREFIXES = ("/api/agent-conversations", "/api/sernia-ai")
OPERATIONS = (
    ("GET", "/conversation/unknown"),
    ("GET", "/conversation/unknown/messages"),
    ("GET", "/conversations/history"),
    ("GET", "/workflow/pending"),
    ("POST", "/conversation/unknown/approve"),
    ("DELETE", "/conversation/unknown"),
)


def person(email="reviewer@serniacapital.com", verified=True):
    return SimpleNamespace(
        id="system:conversation-route-test",
        first_name="Test",
        last_name="Reviewer",
        email_addresses=[
            SimpleNamespace(
                email_address=email,
                verification=SimpleNamespace(status="verified" if verified else "unverified"),
            )
        ],
    )


def test_shared_router_import_and_openapi_do_not_load_an_agent():
    code = """
import importlib.abc
import socket
import sys

class NoAgents(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.startswith("api.src.sernia_ai") or fullname == "api.src.zillow_leads.agent":
            raise AssertionError(f"Shared router loaded an agent: {fullname}")

sys.meta_path.insert(0, NoAgents())
def no_network(*args, **kwargs):
    raise AssertionError("Import must not make external calls")
socket.socket.connect = no_network
from fastapi import FastAPI
from api.src.agent_conversations.routes import router
app = FastAPI()
app.include_router(router, prefix="/api/agent-conversations")
assert len(app.openapi()["paths"]) == 5
"""
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=Path(__file__).resolve().parents[3],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr


@pytest_asyncio.fixture
async def client():
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client


@pytest.fixture
def authorized(monkeypatch):
    monkeypatch.setattr(clerk, "get_auth_user", AsyncMock(return_value=person()))


@pytest.mark.asyncio
@pytest.mark.parametrize("prefix", PREFIXES)
@pytest.mark.parametrize("method,path", OPERATIONS)
@pytest.mark.parametrize(
    "identity", [None, person("outsider@example.test"), person(verified=False)]
)
async def test_every_mount_requires_a_verified_sernia_user(
    client, monkeypatch, prefix, method, path, identity
):
    async def caller(_):
        if identity is None:
            from fastapi import HTTPException

            raise HTTPException(401, "Not signed in")
        return identity

    monkeypatch.setattr(clerk, "get_auth_user", caller)
    response = await client.request(method, prefix + path, json={"decisions": []})
    assert response.status_code == 401


@pytest_asyncio.fixture
async def conversations():
    general, lead = f"shared-general-{uuid4()}", f"shared-lead-{uuid4()}"
    async with AsyncSessionFactory() as session:
        for cid, tool, args, metadata in (
            (general, "record_test_decision", {}, {}),
            (
                lead,
                handle.SEND_REPLY,
                {"body": "Synthetic reply", "team_notes": []},
                {
                    "handler": HANDLER,
                    "reply_route": {
                        "to": "synthetic@convo.zillow.com",
                        "subject": "Synthetic inquiry",
                        "reply_to_message_id": "synthetic-message",
                    },
                },
            ),
        ):
            await save_agent_conversation(
                session,
                cid,
                "sernia",
                [
                    ModelRequest(parts=[UserPromptPart("Synthetic inquiry")]),
                    ModelResponse(parts=[ToolCallPart(tool, args, tool_call_id="pending-test")]),
                ],
                # A teammate must retain access, even though this is another user.
                clerk_user_id="system:another-team-member",
                metadata=metadata,
            )
    yield general, lead
    async with AsyncSessionFactory() as session:
        await session.execute(
            delete(AgentConversation).where(AgentConversation.id.in_([general, lead]))
        )
        await session.commit()


@pytest.mark.asyncio
async def test_old_and_new_urls_read_the_same_shared_history(client, authorized, conversations):
    for cid in conversations:
        for suffix in (f"/conversation/{cid}", f"/conversation/{cid}/messages"):
            responses = [await client.get(prefix + suffix) for prefix in PREFIXES]
            assert all(response.status_code == 200 for response in responses)
            assert responses[0].json() == responses[1].json()
            assert responses[0].json()["pending"][0]["tool_call_id"] == "pending-test"
    for suffix in ("/workflow/pending", "/conversations/history?limit=200"):
        responses = [await client.get(prefix + suffix) for prefix in PREFIXES]
        assert all(response.status_code == 200 for response in responses)
        assert responses[0].json() == responses[1].json()
        listed = {item["conversation_id"] for item in responses[0].json()["conversations"]}
        assert set(conversations) <= listed


@pytest.mark.asyncio
@pytest.mark.parametrize("prefix", PREFIXES)
async def test_delete_keeps_the_same_shared_team_behavior(
    client, authorized, conversations, prefix
):
    cid = conversations[0]
    response = await client.delete(f"{prefix}/conversation/{cid}")
    assert response.json() == {"success": True, "conversation_id": cid}
    for base in PREFIXES:
        assert (await client.get(f"{base}/conversation/{cid}")).status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize("approved", [True, False])
async def test_general_approval_still_resumes_and_records_feedback(
    client, authorized, conversations, monkeypatch, approved
):
    from api.src.sernia_ai import conversation_handler, runs

    calls = []
    agent = Agent(
        FunctionModel(lambda *_: ModelResponse(parts=[TextPart("Decision recorded.")])),
        output_type=str,
    )

    @agent.tool_plain(requires_approval=True)
    def record_test_decision() -> str:
        calls.append("executed")
        return "Recorded"

    monkeypatch.setattr(conversation_handler, "sernia_agent", agent)
    monkeypatch.setattr(
        conversation_handler, "resolve_active_run_kwargs", AsyncMock(return_value={})
    )
    monkeypatch.setattr(runs, "create_logged_task", lambda coro, **kwargs: coro.close())
    cid = conversations[0]
    response = await client.post(
        f"{PREFIXES[1]}/conversation/{cid}/approve",
        json={
            "decisions": [
                {
                    "tool_call_id": "pending-test",
                    "approved": approved,
                    "reason": "Synthetic decision",
                }
            ],
            "user_message": "Synthetic reviewer feedback",
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "completed"
    assert response.json()["pending"] == []
    assert calls == (["executed"] if approved else [])
    messages = await client.get(f"{PREFIXES[0]}/conversation/{cid}/messages")
    assert "Synthetic reviewer feedback" in messages.text
    assert "Decision recorded." in messages.text


@pytest.mark.asyncio
@pytest.mark.parametrize("approved", [True, False])
async def test_lead_approval_uses_stored_handler_and_route(
    client, authorized, conversations, monkeypatch, approved
):
    from api.src.sernia_ai import conversation_handler

    send = AsyncMock(return_value="Synthetic reply sent")
    general = AsyncMock(side_effect=AssertionError("Lead must not run the general agent"))
    monkeypatch.setattr(handle, "send_reply", send)
    monkeypatch.setattr(conversation_handler, "approve_general_conversation", general)
    cid = conversations[1]
    response = await client.post(
        f"{PREFIXES[0]}/conversation/{cid}/approve",
        json={
            "handler": "sernia",
            "decisions": [
                {
                    "tool_call_id": "pending-test",
                    "approved": approved,
                    "override_args": {"body": "Edited reply", "to": "wrong@example.test"},
                }
            ],
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["pending"] == []
    general.assert_not_called()
    if approved:
        assert send.await_count == 1
        assert send.await_args.args[0]["to"] == "synthetic@convo.zillow.com"
        assert send.await_args.args[1] == "Edited reply"
    else:
        send.assert_not_called()


@pytest.mark.asyncio
async def test_concurrent_lead_approvals_across_mounts_send_once(
    client, authorized, conversations, monkeypatch
):
    entered, release = asyncio.Event(), asyncio.Event()
    calls = []

    async def send(*args, **kwargs):
        calls.append(args)
        entered.set()
        await release.wait()
        return "Synthetic reply sent"

    monkeypatch.setattr(handle, "send_reply", send)
    second_loaded = asyncio.Event()
    load = routes.get_agent_conversation
    loads = 0

    async def observed_load(*args, **kwargs):
        nonlocal loads
        conversation = await load(*args, **kwargs)
        loads += 1
        if loads == 2:
            second_loaded.set()
        return conversation

    monkeypatch.setattr(routes, "get_agent_conversation", observed_load)
    cid = conversations[1]
    body = {"decisions": [{"tool_call_id": "pending-test", "approved": True}]}
    first = asyncio.create_task(client.post(f"{PREFIXES[0]}/conversation/{cid}/approve", json=body))
    try:
        await asyncio.wait_for(entered.wait(), timeout=5)
        second = asyncio.create_task(
            client.post(f"{PREFIXES[1]}/conversation/{cid}/approve", json=body)
        )
        await asyncio.wait_for(second_loaded.wait(), timeout=5)
    finally:
        release.set()
    responses = await asyncio.wait_for(asyncio.gather(first, second), timeout=10)
    assert sorted(response.status_code for response in responses) == [200, 404]
    assert len(calls) == 1
    stale = await client.post(f"{PREFIXES[0]}/conversation/{cid}/approve", json=body)
    assert stale.status_code == 404
    assert len(calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error,status,save_partial",
    [
        (ModelHTTPError(429, model_name="test", body="Synthetic provider detail"), 503, True),
        (RuntimeError("Synthetic internal detail"), 500, True),
        (ValueError("No pending conversation"), 404, False),
    ],
)
async def test_general_approval_failure_keeps_status_and_partial_history(
    client, authorized, conversations, monkeypatch, error, status, save_partial
):
    from api.src.sernia_ai import conversation_handler

    captured = [ModelRequest(parts=[UserPromptPart("Synthetic partial progress")])]

    @contextmanager
    def capture():
        yield captured

    save = AsyncMock()
    monkeypatch.setattr(conversation_handler, "capture_run_messages", capture)
    monkeypatch.setattr(
        conversation_handler, "resolve_active_run_kwargs", AsyncMock(return_value={})
    )
    monkeypatch.setattr(conversation_handler, "resume_with_approvals", AsyncMock(side_effect=error))
    monkeypatch.setattr(conversation_handler, "save_failed_run", save)
    response = await client.post(
        f"{PREFIXES[1]}/conversation/{conversations[0]}/approve",
        json={"decisions": [{"tool_call_id": "pending-test", "approved": False}]},
    )
    assert response.status_code == status
    assert "Synthetic provider detail" not in response.text
    assert "Synthetic internal detail" not in response.text
    if save_partial:
        save.assert_awaited_once()
        assert save.await_args.args == (captured,)
        assert save.await_args.kwargs["llm_error"] == isinstance(error, ModelHTTPError)
    else:
        save.assert_not_called()
