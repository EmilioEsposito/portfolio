# Portfolio API (eesposito.com)

Everything in this folder exists only for eesposito.com. Every other package
under `api/src/` is Sernia operations or shared infrastructure. Code here may
import only this folder and `api/src/utils/`; outside it, only `api/index.py`
and tests import it. `api/src/tests/test_api_package_layout.py` checks both.

Public chat combines routed conversation with a live execution trace. Legacy
standalone frontend routes redirect here; their backend compatibility endpoints
remain bounded. Public chat never persists client-supplied conversation IDs.

All agents use GPT-5.6 Luna through OpenRouter with low reasoning. See
[`../utils/README.md`](../utils/README.md) for credentials and abuse budgets;
`PublicAIGuard` enforces those budgets from this folder.

| Path | What it is |
|------|------------|
| `routes.py` | Groups the demo routes under `/ai-demos`; `api/index.py` mounts it at `/api` |
| `chat_emilio/`, `chat_weather/`, `multi_agent_chat/` | The public demo agents and their routes |
| `public_ai_guard.py` | `PublicAIGuard`: request limits and budgets for the public demo paths |
| `input_sanitization.py` | Keeps only text parts of public chat messages (no URLs or files) |
| `swagger_schema.py` | Inlines `$ref`s in the demos' request schemas for the API docs page |

The public-key model (`DEMO_MODEL`) and the demo model settings stay in
`api/src/utils/llm.py`, which also holds the operations key and serves the
Sernia HITL demo. That demo,
`/api/ai-demos/hitl-agent/*`, needs a Sernia sign-in and lives in
[`../hitl_agents/`](../hitl_agents/README.md).
