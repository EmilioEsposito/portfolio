"""Graph activity reaches clients before routing completes, without live inference."""

import asyncio
import json
from types import SimpleNamespace

import pytest
from starlette.requests import Request
from starlette.responses import StreamingResponse

from api.src.ai_demos.multi_agent_chat import routes


def request() -> Request:
    body = json.dumps(
        {
            "trigger": "submit-message",
            "messages": [
                {"role": "user", "parts": [{"type": "text", "text": "Tell me about Emilio"}]}
            ],
        }
    ).encode()
    result = Request({"type": "http", "method": "POST", "path": "/", "headers": []})
    result._body = body
    return result


@pytest.mark.asyncio
async def test_activity_precedes_router_completion_and_terminal_marker(monkeypatch):
    release_router = asyncio.Event()

    async def run(*, state, inputs):
        state.activity("router", "active")
        await release_router.wait()
        state.activity("router", "complete")
        state.activity("emilio", "active")

        async def chunks():
            yield 'data: {"type":"start"}\n\n'
            yield 'data: {"type":"finish"}\n\ndata: [DONE]\n\n'

        return SimpleNamespace(agent_name="emilio", response=StreamingResponse(chunks()))

    monkeypatch.setattr(routes.multi_agent_graph, "run", run)
    response = await routes.multi_agent_chat(request())
    iterator = response.body_iterator
    first = await asyncio.wait_for(anext(iterator), 1)
    assert '"node": "router", "status": "active"' in first
    release_router.set()
    rest = "".join([chunk async for chunk in iterator])
    assert rest.count("[DONE]") == 1
    assert rest.index('"node": "emilio", "status": "complete"') < rest.index("[DONE]")
    assert response.headers["x-vercel-ai-ui-message-stream"] == "v1"


@pytest.mark.asyncio
async def test_router_error_is_generic_stream_error(monkeypatch):
    async def run(**kwargs):
        raise RuntimeError("secret provider details")

    monkeypatch.setattr(routes.multi_agent_graph, "run", run)
    response = await routes.multi_agent_chat(request())
    output = "".join([chunk async for chunk in response.body_iterator])
    assert '"type": "error"' in output
    assert "secret provider details" not in output


@pytest.mark.asyncio
async def test_adapter_error_does_not_mark_specialist_complete(monkeypatch):
    async def run(*, state, inputs):
        state.activity("emilio", "active")

        async def chunks():
            yield 'data: {"type": "error", "errorText": "Unavailable"}\n\n'
            yield "data: [DONE]\n\n"

        return SimpleNamespace(agent_name="emilio", response=StreamingResponse(chunks()))

    monkeypatch.setattr(routes.multi_agent_graph, "run", run)
    response = await routes.multi_agent_chat(request())
    output = "".join([chunk async for chunk in response.body_iterator])
    assert '"status": "complete"' not in output
    assert output.count("[DONE]") == 1


@pytest.mark.asyncio
async def test_disconnect_cancels_pending_router(monkeypatch):
    cancelled = asyncio.Event()

    async def run(*, state, inputs):
        state.activity("router", "active")
        try:
            await asyncio.Future()
        finally:
            cancelled.set()

    monkeypatch.setattr(routes.multi_agent_graph, "run", run)
    response = await routes.multi_agent_chat(request())
    await anext(response.body_iterator)
    await response.body_iterator.aclose()
    assert cancelled.is_set()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status, expected",
    [(402, "Public demo budget is exhausted"), (429, "usage limit"), (500, "could not finish")],
)
async def test_router_provider_error_is_safe_and_actionable(monkeypatch, status, expected):
    from pydantic_ai.exceptions import ModelHTTPError

    async def run(*, state, inputs):
        state.activity("router", "active")
        raise ModelHTTPError(status, "private-model", {"message": "private-provider-body"})

    monkeypatch.setattr(routes.multi_agent_graph, "run", run)
    response = await routes.multi_agent_chat(request())
    output = "".join([chunk async for chunk in response.body_iterator])
    assert expected in output
    assert "private-model" not in output
    assert "private-provider-body" not in output
    assert '"status": "complete"' not in output


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status, expected",
    [(402, "Public demo budget is exhausted"), (429, "usage limit"), (500, "could not finish")],
)
async def test_split_adapter_provider_error_is_safe_and_actionable(monkeypatch, status, expected):
    async def run(*, state, inputs):
        state.activity("weather", "active")

        async def chunks():
            payload = (
                "data: "
                + json.dumps(
                    {
                        "type": "error",
                        "errorText": f"status_code: {status}, model_name: private-model, body: private-provider-body",
                    }
                )
                + "\n\ndata: [DONE]\n\n"
            )
            yield payload[:24]
            yield payload[24:]

        return SimpleNamespace(agent_name="weather", response=StreamingResponse(chunks()))

    monkeypatch.setattr(routes.multi_agent_graph, "run", run)
    response = await routes.multi_agent_chat(request())
    output = "".join([chunk async for chunk in response.body_iterator])
    assert expected in output
    assert "private-model" not in output
    assert "private-provider-body" not in output
    assert '"status": "complete"' not in output
    assert output.count("[DONE]") == 1


@pytest.mark.asyncio
async def test_router_and_specialist_share_one_inference_budget(monkeypatch):
    from api.src.ai_demos.multi_agent_chat import graph

    seen = []

    async def route(*args, usage, **kwargs):
        seen.append(usage)
        usage.requests += 1
        return SimpleNamespace(output=SimpleNamespace(agent_name=graph.AgentName.emilio))

    async def specialist(*args, usage, **kwargs):
        seen.append(usage)
        assert usage.requests == 1
        return SimpleNamespace(output="Answer")

    monkeypatch.setattr(graph.router_agent, "run", route)
    monkeypatch.setattr(graph.emilio_agent, "run", specialist)
    state = graph.MultiAgentState()
    await graph.multi_agent_graph.run(state=state, inputs=graph.MultiAgentInput(message="Emilio"))
    assert seen == [state.usage, state.usage]
    assert graph.MultiAgentState().usage is not state.usage
