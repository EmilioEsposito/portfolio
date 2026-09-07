# Inference gateway and public AI budgets

All runtime LLM calls use **OpenRouter**. Set `PORTFOLIO_OPENROUTER_API_KEY`
from [OpenRouter keys](https://openrouter.ai/keys) in the deployment secret
manager. `api/__init__.py` bridges it to `OPENROUTER_API_KEY` for PydanticAI;
`llm.py` provides lazy operational SDK clients with the same key and explicit
OpenRouter base URL. Public chat and email use only
`PUBLIC_PORTFOLIO_OPENROUTER_API_KEY`, a separate capped key. Public model
credentials are resolved at inference time; missing public configuration returns
503 and cannot use the operations key or prevent application startup. There is no direct OpenAI/Anthropic fallback. Never place keys in
frontend environment variables.

The public agents and email studio use `openai/gpt-5.6-luna` with `low`
reasoning (the provider spelling of light effort). Verified against the
[model catalog](https://openrouter.ai/api/v1/models) on 2026-09-07; reasoning
configuration follows [OpenRouter's reasoning documentation](https://openrouter.ai/docs/guides/best-practices/reasoning-tokens).
Production Claude IDs use dots: `anthropic/claude-sonnet-4.6`,
`anthropic/claude-opus-4.7`, `anthropic/claude-haiku-4.5`.

`PublicAIGuard` wraps all public inference paths before parsing/model dispatch:

- 24 KB streamed request body, 10 seconds to upload.
- 16 messages, 4,000 characters each, 12,000 characters total; only user and
  assistant roles. Only text reaches inference; browser-supplied tool results,
  attachments, and system messages are never accepted as privileged context.
- Six runs per minute and 30 per hour per ASGI client address, shared across
  endpoints. Raw forwarded headers are not trusted. Configure the ASGI server
  to trust only your actual ingress proxy; shared proxy identities may share
  a budget, which fails closed.
- Four active runs and 300 starts per rolling day per worker, across clients.
- 60 seconds for the entire streaming response; cancellation releases the slot.
- Agents have five model requests/four tool calls and 5,000 aggregate output
  tokens per run, with 1,800 output tokens per model response. Tool network
  calls also have timeouts and bounded content. Email is a single bounded call.

These counters reset at worker restart and are multiplied by worker count.
They are **not a durable account spending limit**. Set an OpenRouter credit limit on the dedicated public key for a durable
spending backstop. Exhaustion pauses public inference without consuming the
Sernia AI operations key. Do not raise worker count
without considering the resulting public budget.

For 429 responses, wait for the rolling window; a global daily cap can take
up to a day to reopen. For provider failures, inspect Logfire status/cost and
OpenRouter key balance without logging secrets. Changing providers requires
checking model IDs, reasoning support, tools, and structured output support.
