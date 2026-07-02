from functools import wraps
from types import MethodType

import logfire

from api.src.ai_demos.models import persist_agent_run_result


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
        else:
            conversation_id = getattr(deps, "conversation_id", None)
            clerk_user_id = getattr(deps, "clerk_user_id", "anonymous")
        if conversation_id is not None:
            await persist_agent_run_result(
                result=result,
                conversation_id=conversation_id,
                agent_name=agent.name,
                clerk_user_id=clerk_user_id,
            )
        else:
            logfire.warning("No conversation_id provided for persistence")
        return result

    agent.run = MethodType(run, agent)