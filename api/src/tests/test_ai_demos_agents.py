"""
Live agent tests moved from their service modules under ``api/src/ai_demos/``.

These tests hit real LLM APIs (and, for the graph tests, real downstream
tools), so they are marked ``live`` and only run when explicitly requested:

    pytest -m live api/src/tests/test_ai_demos_agents.py -v -s
"""

import asyncio
import json

import pytest
from pydantic_ai import Agent
from starlette.requests import Request
from starlette.responses import Response

from api.src.ai_demos.agent_run_patching import patch_run_with_persistence
from api.src.ai_demos.chat_emilio.agent import agent as emilio_agent
from api.src.ai_demos.chat_weather.agent import ChatContext, agent as weather_agent
from api.src.ai_demos.multi_agent_chat.decision_agent import (
    AgentName,
    RoutingDecision,
    router_agent,
)
from api.src.ai_demos.multi_agent_chat.graph import (
    MultiAgentInput,
    MultiAgentState,
    multi_agent_graph,
)


# ---------------------------------------------------------------------------
# From api/src/ai_demos/agent_run_patching.py
# ---------------------------------------------------------------------------


@pytest.mark.live
@pytest.mark.asyncio
async def test_agent_run_with_persistence():
    agent = Agent(
        name="test_agent",
        model="gpt-4o-mini",
        system_prompt="You are a test agent.",
        output_type=str,
    )
    patch_run_with_persistence(agent)
    result = await agent.run(
        user_prompt="Hello, world!",
        deps={"conversation_id": "123"},
    )
    print(f"Result: {result}")


# ---------------------------------------------------------------------------
# From api/src/ai_demos/chat_emilio/agent.py (was ``test_agent``)
# ---------------------------------------------------------------------------


@pytest.mark.live
@pytest.mark.asyncio
async def test_chat_emilio_agent():
    """Test the agent locally"""
    result = await emilio_agent.run("Summarize Emilio's LinkedIn profile")
    print(f"\n\nAgent Response:\n{result}")


# ---------------------------------------------------------------------------
# From api/src/ai_demos/chat_weather/agent.py (was ``test_agent``)
# ---------------------------------------------------------------------------


@pytest.mark.live
@pytest.mark.asyncio
async def test_chat_weather_agent():
    """Test the agent directly"""
    result = await weather_agent.run("What's the weather like at coordinates 40.7128, -74.0060?", deps=ChatContext())
    assert result.output is not None


# ---------------------------------------------------------------------------
# From api/src/ai_demos/multi_agent_chat/decision_agent.py
# ---------------------------------------------------------------------------


@pytest.mark.live
@pytest.mark.asyncio
async def test_router_agent_routes_to_weather():
    """Test that router agent routes weather-related questions to weather agent"""
    result = await router_agent.run(
        "What's the weather in Tokyo?",
        # deps=RouterContext(),
    )

    assert result.output is not None
    assert isinstance(result.output, RoutingDecision)
    assert result.output.agent_name == AgentName.weather


# ---------------------------------------------------------------------------
# From api/src/ai_demos/multi_agent_chat/graph.py
# ---------------------------------------------------------------------------


@pytest.mark.live
@pytest.mark.asyncio
async def test_multi_agent_graph_routes_to_weather():
    """Test that multi-agent graph routes weather questions to weather agent"""
    input_data = MultiAgentInput(message="What's the weather in Tokyo?")
    state = MultiAgentState()
    result = await multi_agent_graph.run(state=state, inputs=input_data)
    assert result.agent_name == AgentName.weather
    assert result.response is not None
    assert len(result.response) > 0
    print(result.response)


@pytest.mark.live
@pytest.mark.asyncio
async def test_multi_agent_graph_routes_to_weather_vercel_ai():
    """Test that multi-agent graph routes weather questions to weather agent"""
    input_data = MultiAgentInput(message="What's the weather in Tokyo?")
    request_payload = {
        "trigger": "submit-message",
        "id": "test-id",
        "messages": [
            {
                "id": "msg-1",
                "role": "user",
                "parts": [
                    {
                        "type": "text",
                        "text": "What's the weather in Tokyo?"
                    }
                ]
            }
        ]
    }
    vercel_request = build_test_vercel_request(request_payload)
    state = MultiAgentState(agent_run_method="vercel_ai", vercel_ai_request=vercel_request)
    result = await multi_agent_graph.run(state=state, inputs=input_data)
    assert result.agent_name == AgentName.weather
    assert result.response is not None
    assert isinstance(result.response, Response)
    assert result.response.status_code == 200
    print(result.response)


def build_test_vercel_request(payload: dict) -> Request:
    """Create a Starlette Request suitable for Vercel adapter testing."""
    body_bytes = json.dumps(payload).encode("utf-8")
    body_consumed = False

    async def receive():
        nonlocal body_consumed
        if body_consumed:
            return {"type": "http.request", "body": b"", "more_body": False}
        body_consumed = True
        return {"type": "http.request", "body": body_bytes, "more_body": False}

    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/api/ai-demos/multi-agent-chat",
        "raw_path": b"/api/ai-demos/multi-agent-chat",
        "root_path": "",
        "query_string": b"",
        "headers": [
            (b"content-type", b"application/json"),
        ],
        "client": ("testclient", 50000),
        "server": ("testserver", 80),
    }

    return Request(scope, receive)


if __name__ == "__main__":
    # Runner moved from api/src/ai_demos/agent_run_patching.py
    asyncio.run(test_agent_run_with_persistence())
