"""
Graph definition for routing messages to specialized agents using Pydantic AI Graph Beta API
"""

import json
import logfire
from dataclasses import dataclass
from typing import Literal

import pytest
from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict
from pydantic_graph.beta import GraphBuilder, StepContext
from starlette.requests import Request
from starlette.responses import Response

from api.src.ai_demos.chat_emilio.agent import agent as emilio_agent, PortfolioContext as EmilioContext
from api.src.ai_demos.chat_weather.agent import agent as weather_agent, ChatContext as WeatherContext
from api.src.ai_demos.multi_agent_chat.decision_agent import AgentName, router_agent
from pydantic_ai.ui.vercel_ai import VercelAIAdapter


load_dotenv(".env")


@dataclass
class MultiAgentState:
    """State for the multi-agent graph"""

    user_name: str = "user"
    message: str = ""
    message_history: list | None = None
    selected_agent: str | None = None
    agent_response: str | Response | None = None
    agent_run_method: Literal["standard", "vercel_ai"] = "standard"
    vercel_ai_request: Request | None = None

    def __post_init__(self):
        """Conditionally require vercel_ai_request based on agent_run_method."""
        if self.agent_run_method == "vercel_ai" and self.vercel_ai_request is None:
            raise ValueError("vercel_ai_request must be provided when agent_run_method is 'vercel_ai'.")

    def require_vercel_request(self) -> Request:
        """Return the Request needed for Vercel AI runs, ensuring it exists."""
        if self.vercel_ai_request is None:
            raise ValueError("vercel_ai_request is required to run with Vercel AI.")
        return self.vercel_ai_request


class MultiAgentInput(BaseModel):
    """Input for the multi-agent graph"""

    message: str
    message_history: list | None = None


class MultiAgentOutput(BaseModel):
    """Output from the multi-agent graph"""

    agent_name: str
    response: str | Response  # Supports standard strings and Vercel streaming responses
    response_mode: Literal["standard", "vercel_ai"] = "standard"

    model_config = ConfigDict(arbitrary_types_allowed=True)


# Initialize the graph builder
g = GraphBuilder(
    state_type=MultiAgentState,
    input_type=MultiAgentInput,
    output_type=MultiAgentOutput
)


@g.step
async def route_message(ctx: StepContext[MultiAgentState, None, MultiAgentInput]) -> AgentName:
    """
    Use the router agent to determine which agent should handle the message.

    Returns:
        The agent name enum value
    """
    # Store message and history in state
    ctx.state.message = ctx.inputs.message
    ctx.state.message_history = ctx.inputs.message_history
    
    logfire.info("Routing message", message=ctx.state.message[:100])

    # Use the router agent to make the routing decision
    result = await router_agent.run(
        ctx.state.message,
        # deps=RouterContext(),
    )

    agent_name = result.output.agent_name
    ctx.state.selected_agent = agent_name.value

    logfire.info("Routing decision", agent_name=agent_name.value)

    return agent_name


@g.step
async def run_emilio_agent(
    ctx: StepContext[MultiAgentState, None, MultiAgentInput],
) -> MultiAgentOutput:
    """Run the Emilio portfolio agent"""
    logfire.info("Running Emilio agent", message=ctx.state.message[:100])

    if ctx.state.agent_run_method == "vercel_ai":
        request = ctx.state.require_vercel_request()
        vercel_response = await VercelAIAdapter.dispatch_request(
            request,
            agent=emilio_agent,
            deps=EmilioContext(user_name="visitor"),
            message_history=ctx.state.message_history,
        )
        ctx.state.agent_response = vercel_response
        return MultiAgentOutput(
            agent_name="emilio",
            response=vercel_response,
            response_mode="vercel_ai",
        )

    result = await emilio_agent.run(
        ctx.state.message,
        deps=EmilioContext(user_name="visitor"),
        message_history=ctx.state.message_history,
    )

    ctx.state.agent_response = result.output
    return MultiAgentOutput(agent_name="emilio", response=result.output, response_mode="standard")


@g.step
async def run_weather_agent(
    ctx: StepContext[MultiAgentState, None, MultiAgentInput],
) -> MultiAgentOutput:
    """Run the Weather agent"""
    logfire.info("Running Weather agent", message=ctx.state.message[:100])

    if ctx.state.agent_run_method == "vercel_ai":
        request = ctx.state.require_vercel_request()
        vercel_response = await VercelAIAdapter.dispatch_request(
            request,
            agent=weather_agent,
            deps=WeatherContext(),
            message_history=ctx.state.message_history,
        )
        ctx.state.agent_response = vercel_response
        return MultiAgentOutput(
            agent_name="weather",
            response=vercel_response,
            response_mode="vercel_ai",
        )

    result = await weather_agent.run(
        ctx.state.message,
        deps=WeatherContext(),
        message_history=ctx.state.message_history,
    )

    ctx.state.agent_response = result.output
    return MultiAgentOutput(agent_name="weather", response=result.output, response_mode="standard")


g.add(
    g.edge_from(g.start_node).to(route_message),
    g.edge_from(route_message).to(
        g.decision()
            .branch(
                g.match(route_message, matches=lambda output: output == AgentName.emilio).to(
                    run_emilio_agent
                )
            )
            .branch(
                g.match(route_message, matches=lambda output: output == AgentName.weather).to(
                    run_weather_agent
                )
            )
        ),
    g.edge_from(run_emilio_agent, run_weather_agent).to(g.end_node)
    )
    


# Build the graph
multi_agent_graph = g.build()


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

