# Public AI workspace

Public chat combines routed conversation with a live execution trace. Legacy
standalone frontend routes redirect here; their backend compatibility endpoints
remain bounded. Public chat never persists client-supplied conversation IDs.

All agents use GPT-5.6 Luna through OpenRouter with low reasoning. See
[`../utils/README.md`](../utils/README.md) for credentials and abuse budgets.
The authenticated HITL SMS agent remains protected by SerniaUser.
