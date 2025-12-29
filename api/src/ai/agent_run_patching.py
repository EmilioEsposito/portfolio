from functools import wraps
from types import MethodType
from api.src.ai.models import persist_agent_run_result
from pydantic_ai import Agent
import logfire
import pytest
import asyncio

def patch_run_with_persistence(agent):
    original = agent.run

    @wraps(original)
    async def run(self, *args, **kwargs):
        logfire.info(f"patch_run_with_persistence called for agent {agent.name}")
        # assume caller passes deps or you compute it here; adjust to your needs
        result = await original(*args, **kwargs)

        deps = kwargs.get("deps")
        if isinstance(deps, dict):
            conversation_id = deps.get("conversation_id")
            clerk_user_id = deps.get("clerk_user_id", "anonymous")
            db_session = deps.get("db_session", None)
        else:
            conversation_id = getattr(deps, "conversation_id", None)
            clerk_user_id = getattr(deps, "clerk_user_id", "anonymous")
            db_session = getattr(deps, "db_session", None)
        if conversation_id is not None:
            await persist_agent_run_result(
                result=result,
                conversation_id=conversation_id,
                agent_name=agent.name,
                clerk_user_id=clerk_user_id,
                session=db_session,
            )
        else:
            logfire.warning("No conversation_id provided for persistence")
        return result

    agent.run = MethodType(run, agent)


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

if __name__ == "__main__":
    asyncio.run(test_agent_run_with_persistence())