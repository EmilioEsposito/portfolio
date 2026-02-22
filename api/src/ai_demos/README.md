# AI Demo Agents

This directory contains AI demo agents built with PydanticAI.

```
ai_demos/
├─ models.py                # Shared: AgentConversation model, persistence helpers
├─ hitl_utils.py             # Shared: HITL approval utilities (used by hitl_agents + ai_sernia)
├─ agent_run_patching.py    # Shared: Auto-persistence patch for agents
├─ chat_emilio/             # Portfolio assistant (/api/ai-demos/chat-emilio)
├─ chat_weather/            # Weather + general chat (/api/ai-demos/chat-weather)
├─ multi_agent_chat/        # Graph router that fans out to leaf agents
└─ hitl_agents/             # Human-in-the-Loop agents with approval workflows
```

## Agent Run Patching (Auto-Persistence)

Agents that need conversation persistence can use the `patch_run_with_persistence` helper. This patches `agent.run()` to automatically save results to the database after each run.

```python
from api.src.ai_demos.agent_run_patching import patch_run_with_persistence

my_agent = Agent(...)
patch_run_with_persistence(my_agent)

# Now agent.run() automatically persists to DB when deps has conversation_id
result = await my_agent.run(
    user_prompt="Hello",
    deps=MyContext(clerk_user_id="user_123", conversation_id="conv_456"),
)
# Conversation saved to agent_conversations table
```

**Requirements:**
- Agent's `deps` must have `conversation_id` and `clerk_user_id` attributes (or be a dict with those keys)
- If `conversation_id` is None, persistence is skipped (useful for one-off queries)

## Quick checks

```bash
# Shell helpers
cd /Users/eesposito/portfolio
source .venv/bin/activate

# Run the simple in-file agent tests
python -c "from api.src.ai_demos.chat_emilio.agent import test_agent; import asyncio; asyncio.run(test_agent())"
python -c "from api.src.ai_demos.chat_weather.agent import test_agent"

# Exercise the multi-agent streaming endpoint (FastAPI + Vercel adapter)
pytest api/src/tests/test_multi_agent_chat_vercel.py -k weather -vv
```

Frontend pages:
- `/chat-emilio`
- `/chat-weather`
- `/multi-agent-chat` (uses the graph router)

Each page uses the same streaming contract (Vercel AI SDK Data Stream Protocol) so you can compare the standalone agents against the multi-agent orchestration.
