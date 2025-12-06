# AI Chat Examples

This directory now contains three standalone chat agents plus a graph-driven multi-agent router. Each folder exposes its own `routes.py` so the backend and frontend can showcase the agents independently.

```
ai/
├─ chat_emilio/          # Portfolio assistant (tools + /api/ai/chat-emilio)
│  ├─ agent.py
│  └─ routes.py
├─ chat_weather/         # Weather + general chat (/api/ai/chat-weather)
│  ├─ agent.py
│  └─ routes.py
└─ multi_agent_chat/     # Graph router that fans out to the leaf agents
   ├─ decision_agent.py
   ├─ graph.py
   └─ routes.py          # /api/ai/multi-agent-chat
```

## Quick checks

```bash
# Shell helpers
cd /Users/eesposito/portfolio
source .venv/bin/activate

# Run the simple in-file agent tests
python -c "from apps.api.src.ai.chat_emilio.agent import test_agent; import asyncio; asyncio.run(test_agent())"
python -c "from apps.api.src.ai.chat_weather.agent import test_agent"

# Exercise the multi-agent streaming endpoint (FastAPI + Vercel adapter)
pytest api/src/tests/test_multi_agent_chat_vercel.py -k weather -vv
```

Frontend pages:
- `/chat-emilio`
- `/chat-weather`
- `/multi-agent-chat` (uses the graph router)

Each page uses the same streaming contract (Vercel AI SDK Data Stream Protocol) so you can compare the standalone agents against the multi-agent orchestration.
