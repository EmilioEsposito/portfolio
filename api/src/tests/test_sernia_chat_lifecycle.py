"""Real adapter + Postgres, with deterministic model/tool barriers and no external calls."""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
import pytest_asyncio
from fastapi import HTTPException
from pydantic_ai import Agent
from pydantic_ai.messages import ToolReturnPart
from pydantic_ai.models.function import DeltaToolCall, FunctionModel
from sqlalchemy import delete
from starlette.requests import Request

from api.src.agent_conversations import routes as conversation_routes
from api.src.agent_conversations import run_state
from api.src.agent_conversations.models import (
    AgentConversation,
    list_user_conversations,
    save_agent_conversation,
)
from api.src.agent_conversations.schemas import ApprovalRequest
from api.src.database.database import AsyncSessionFactory
from api.src.sernia_ai import chat_runs, routes, runs

USER = SimpleNamespace(
    id="system:chat-test", first_name="Test", last_name="User", email_addresses=[]
)


def request(cid, request_id=None, prompt="test request"):
    payload = json.dumps(
        {
            "id": cid,
            "request_id": request_id or str(uuid4()),
            "trigger": "submit-message",
            "messages": [
                {"id": str(uuid4()), "role": "user", "parts": [{"type": "text", "text": prompt}]}
            ],
        }
    ).encode()

    async def receive():
        return {"type": "http.request", "body": payload, "more_body": False}

    return Request({"type": "http", "headers": [(b"content-type", b"application/json")]}, receive)


async def start(cid, **kwargs):
    async with AsyncSessionFactory() as session:
        return await routes.chat_sernia(request(cid, **kwargs), user=USER, session=session)


async def saved(cid):
    async with AsyncSessionFactory() as session:
        row = await session.get(AgentConversation, cid)
        return row


async def messages(cid):
    async with AsyncSessionFactory() as session:
        return await conversation_routes.get_conversation_messages_endpoint(
            cid, user=USER, session=session
        )


async def finished():
    await asyncio.wait_for(asyncio.gather(*list(chat_runs._tasks)), timeout=10)


@pytest_asyncio.fixture
async def scenario(monkeypatch):
    cid = f"test-chat-{uuid4()}"
    entered, release = asyncio.Event(), asyncio.Event()

    async def stream(history, info):
        if any(isinstance(part, ToolReturnPart) for message in history for part in message.parts):
            yield "Finished after the browser left."
        else:
            yield {
                0: DeltaToolCall(
                    name="wait_for_test", json_args="{}", tool_call_id="read-only-test"
                )
            }

    agent = Agent(FunctionModel(stream_function=stream), deps_type=object)

    @agent.tool_plain
    async def wait_for_test() -> str:
        entered.set()
        await release.wait()
        return "Saved tool result"

    async def config():
        return {}

    def discard_notification(coro, **kwargs):
        coro.close()

    monkeypatch.setattr(routes, "sernia_agent", agent)
    monkeypatch.setattr(chat_runs, "resolve_active_run_kwargs", config)
    monkeypatch.setattr(runs, "create_logged_task", discard_notification)
    yield SimpleNamespace(cid=cid, entered=entered, release=release, agent=agent)
    release.set()
    await chat_runs.shutdown_chat_runs()
    async with AsyncSessionFactory() as session:
        await session.execute(delete(AgentConversation).where(AgentConversation.id == cid))
        await session.commit()


@pytest.mark.asyncio
async def test_prompt_is_saved_and_listed_before_model_finishes_and_survives_disconnect(scenario):
    response = await start(scenario.cid)
    row = await saved(scenario.cid)
    assert "test request" in json.dumps(row.messages)
    assert row.metadata_[chat_runs.RUN_KEY]["status"] == "running"
    listing = await list_user_conversations(agent_name=routes.AGENT_NAME, clerk_user_id=None)
    assert any(conv["conversation_id"] == scenario.cid for conv in listing)

    # Real ASGI disconnect cancels the SSE subscriber while the model/tool is blocked.
    disconnected = asyncio.Event()

    async def receive():
        await disconnected.wait()
        return {"type": "http.disconnect"}

    async def send(message):
        pass

    subscriber = asyncio.create_task(
        response({"type": "http", "asgi": {"spec_version": "2.0"}}, receive, send)
    )
    await asyncio.wait_for(scenario.entered.wait(), timeout=10)
    disconnected.set()
    await asyncio.wait_for(subscriber, timeout=2)
    assert chat_runs._tasks
    progress = await messages(scenario.cid)
    assert progress["run"]["status"] == "running"
    assert progress["pending"] == []  # A checkpointed tool call is not an approval.
    assert "wait_for_test" in json.dumps(progress["messages"])
    scenario.release.set()
    await finished()
    completed = await messages(scenario.cid)
    assert completed["run"]["status"] == "completed"
    assert "Finished after the browser left." in json.dumps(completed["messages"])
    assert json.dumps(completed["messages"]).count("test request") == 1


@pytest.mark.asyncio
async def test_cancel_from_another_worker_preserves_progress_and_allows_followup(scenario):
    await start(scenario.cid)
    await asyncio.wait_for(scenario.entered.wait(), timeout=10)
    run = (await saved(scenario.cid)).metadata_[chat_runs.RUN_KEY]
    # Removing only the fast-path token simulates a cancel request on another replica.
    token = chat_runs._tokens.pop((scenario.cid, run["id"]))
    try:
        result = await chat_runs.cancel_run(scenario.cid, run["id"])
        assert result["cancel_requested"] is True
        await finished()
        assert token.cancelled
    finally:
        chat_runs._tokens.pop((scenario.cid, run["id"]), None)
    result = await messages(scenario.cid)
    assert result["run"]["status"] == "cancelled"
    assert result["pending"] == []
    assert "test request" in json.dumps(result["messages"])
    scenario.release.set()
    await start(scenario.cid, prompt="continue from saved progress")
    await finished()
    assert (await messages(scenario.cid))["run"]["status"] == "completed"


@pytest.mark.asyncio
async def test_active_run_rejects_overlap_delete_and_approval(scenario):
    await start(scenario.cid)
    await asyncio.wait_for(scenario.entered.wait(), timeout=10)
    stale_cancel = await chat_runs.cancel_run(scenario.cid, "a-previous-request")
    assert not stale_cancel.get("cancel_requested")
    with pytest.raises(HTTPException, match="still running") as overlap:
        await start(scenario.cid, prompt="must not overwrite")
    assert overlap.value.status_code == 409
    async with AsyncSessionFactory() as session:
        with pytest.raises(HTTPException) as deletion:
            await conversation_routes.delete_conversation_endpoint(
                scenario.cid, user=USER, session=session
            )
        assert deletion.value.status_code == 409
    async with AsyncSessionFactory() as session:
        with pytest.raises(HTTPException) as approval:
            await conversation_routes.approve_conversation(
                scenario.cid, ApprovalRequest(decisions=[]), user=USER, session=session
            )
        assert approval.value.status_code == 409
    assert "must not overwrite" not in json.dumps((await saved(scenario.cid)).messages)


@pytest.mark.asyncio
async def test_concurrent_first_messages_have_one_winner(scenario):
    results = await asyncio.gather(
        start(scenario.cid, prompt="first contender"),
        start(scenario.cid, prompt="second contender"),
        return_exceptions=True,
    )
    errors = [result for result in results if isinstance(result, HTTPException)]
    assert len(errors) == 1 and errors[0].status_code == 409
    history = json.dumps((await saved(scenario.cid)).messages)
    assert ("first contender" in history) != ("second contender" in history)


@pytest.mark.asyncio
async def test_run_timeout_keeps_the_conversation(scenario, monkeypatch):
    monkeypatch.setattr(chat_runs, "MAX_RUN_SECONDS", 0.1)
    await start(scenario.cid)
    await finished()
    result = await messages(scenario.cid)
    assert result["run"]["status"] == "failed"
    assert "test request" in json.dumps(result["messages"])


@pytest.mark.asyncio
async def test_provider_failure_keeps_initial_prompt_and_terminal_status(scenario, monkeypatch):
    async def broken(history, info):
        raise RuntimeError("synthetic provider outage")
        yield "unreachable"

    monkeypatch.setattr(routes, "sernia_agent", Agent(FunctionModel(stream_function=broken)))
    await start(scenario.cid)
    await finished()
    result = await messages(scenario.cid)
    assert result["run"]["status"] == "failed"
    assert "test request" in json.dumps(result["messages"])


@pytest.mark.asyncio
async def test_duplicate_request_does_not_run_twice(scenario):
    scenario.release.set()
    rid = str(uuid4())
    await start(scenario.cid, request_id=rid)
    await finished()
    with pytest.raises(HTTPException) as duplicate:
        await start(scenario.cid, request_id=rid)
    assert duplicate.value.status_code == 409
    assert (await saved(scenario.cid)).run_count == 1


def test_expired_worker_is_reported_interrupted():
    run = chat_runs.new_run("test")
    run["expires_at"] = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
    assert chat_runs.public_run({chat_runs.RUN_KEY: run})["status"] == "interrupted"
    run_state.require_idle({chat_runs.RUN_KEY: run})


@pytest.mark.asyncio
async def test_superseded_worker_cannot_overwrite_current_run(scenario):
    await start(scenario.cid)
    async with AsyncSessionFactory() as session:
        with pytest.raises(ValueError, match="superseded"):
            await save_agent_conversation(
                session,
                scenario.cid,
                routes.AGENT_NAME,
                [],
                expected_run_id="previous-worker",
            )
    assert "test request" in json.dumps((await saved(scenario.cid)).messages)
