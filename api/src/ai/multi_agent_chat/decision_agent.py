"""
Router Agent using PydanticAI

This agent determines which specialized agent should handle a user's message.
It uses structured output to return either "emilio" or "weather".
"""
import logfire
from dataclasses import dataclass
from pydantic import BaseModel
from pydantic_ai import Agent
from pydantic_ai.models.openai import OpenAIChatModel
import pytest
from dotenv import load_dotenv

load_dotenv('.env.development.local')
from enum import Enum


# @dataclass
# class RouterContext:
#     """Context for the router agent"""
#     user_name: str = "user"


class AgentName(str, Enum):
    """Agent name enum for routing decisions"""
    emilio = "emilio"
    weather = "weather"


class RoutingDecision(BaseModel):
    """Structured output for routing decisions"""
    agent_name: AgentName


# Create the router agent with structured output
router_model = OpenAIChatModel("gpt-4o-mini")

router_agent = Agent(
    model=router_model,
    system_prompt=(
        "You are a routing agent that determines which specialized agent should handle a user's message.\n\n"
        "You have access to two agents:\n"
        "1. **Emilio Agent**: Handles questions about Emilio Esposito, his portfolio, skills, projects, "
        "experience, LinkedIn profile, articles, interviews, and related topics.\n"
        "2. **Weather Agent**: Handles weather-related questions, current weather conditions, forecasts, "
        "and location-based weather queries.\n\n"
        "Analyze the user's message and determine which agent should handle it. "
        "If the message is about Emilio, his work, portfolio, skills, projects, or related topics, route to 'emilio'. "
        "If the message is about weather, temperature, forecast, or climate, route to 'weather'. "
        "If the message is unclear or could be handled by either agent, default to 'emilio' for general questions."
    ),
    output_type=RoutingDecision,
    retries=2,
)

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