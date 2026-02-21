"""
Chat Agent using PydanticAI with weather tool

This agent provides a general-purpose chat assistant with weather functionality.
"""
import logfire
import requests
from dataclasses import dataclass
from pydantic_ai import Agent, RunContext
from pydantic_ai.models.openai import OpenAIChatModel
import pytest


@dataclass
class ChatContext:
    """Context for the chat agent"""
    user_name: str = "user"


# Create the agent with the OpenAI model
model = OpenAIChatModel("gpt-4-turbo-preview")

agent = Agent(
    model=model,
    system_prompt=(
        "You are a helpful AI assistant. You can help users with various questions and tasks. "
        "You have access to a weather tool that can get current weather information for any location. "
        "Be friendly, concise, and helpful."
    ),
    retries=2,
    name="chat_weather",
)


@agent.tool
async def get_current_weather(ctx: RunContext[ChatContext], latitude: float, longitude: float) -> dict:
    """
    Get the current weather at a location.
    
    Args:
        ctx: The run context
        latitude: The latitude of the location
        longitude: The longitude of the location
    
    Returns:
        Weather data including current temperature, hourly forecast, and daily sunrise/sunset times
    """
    url = f"https://api.open-meteo.com/v1/forecast?latitude={latitude}&longitude={longitude}&current=temperature_2m&hourly=temperature_2m&daily=sunrise,sunset&timezone=auto"
    
    try:
        response = requests.get(url)
        response.raise_for_status()
        weather_data = response.json()
        logfire.info(f"Weather fetched for lat={latitude}, lon={longitude}")
        return weather_data
    except requests.RequestException as e:
        logfire.error(f"Error fetching weather data: {e}")
        return {"error": f"Failed to fetch weather data: {str(e)}"}

@pytest.mark.asyncio
async def test_agent():
    """Test the agent directly"""
    result = await agent.run("What's the weather like at coordinates 40.7128, -74.0060?", deps=ChatContext())
    assert result.output is not None

