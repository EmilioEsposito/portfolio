"""Live integration tests for the Sernia AI → OpenRouter gateway.

These hit the real OpenRouter API. They require `PORTFOLIO_OPENROUTER_API_KEY`
(bridged to `OPENROUTER_API_KEY` in `api/__init__.py`) and skip otherwise.

Run with:
    pytest -m live api/src/tests/test_openrouter_integration.py -v -s

What they pin down — each is something the offline tests can only assert about
our *request*, not about what OpenRouter actually accepts:

1. The whole reasoning-effort ladder, including `max`, survives the gateway.
   OpenRouter validates `reasoning.effort` server-side and 400s on anything
   outside `max|xhigh|high|medium|low|minimal|none`, so a bad tier here is a
   hard failure at runtime, not a silent downgrade.
2. Effort actually changes behaviour end to end (low burns far fewer reasoning
   tokens than max) — i.e. the knob is honoured, not just accepted.
3. Usage accounting comes back with a real cost, which is the only cost signal
   for OpenRouter runs (genai-prices has no entry for these routes — see
   `test_model_pricing.py`).
4. The web-search domain allowlist reaches OpenRouter's `web` plugin: search
   results are confined to `WEB_SEARCH_ALLOWED_DOMAINS`.
"""

import os
import re

import pytest
from dotenv import find_dotenv, load_dotenv

load_dotenv(find_dotenv(".env"), override=False)

from api.src.sernia_ai.config import WEB_SEARCH_ALLOWED_DOMAINS  # noqa: E402
from api.src.sernia_ai.model_config import build_run_kwargs  # noqa: E402

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        not os.environ.get("OPENROUTER_API_KEY"),
        reason="PORTFOLIO_OPENROUTER_API_KEY not set",
    ),
]

# A prompt that forces real reasoning, so effort tiers are distinguishable.
_REASONING_PROMPT = (
    "A farmer must cross a river with a wolf, a goat, and a cabbage. The boat "
    "holds the farmer plus one item. The wolf eats the goat if left alone "
    "together; the goat eats the cabbage if left alone together. Enumerate "
    "every valid crossing sequence of minimum length and prove minimality."
)


def _bare_agent(effort: str):
    """Agent on the production model + settings, but no tools or instructions.

    Keeps these tests about the gateway rather than the Sernia system prompt.
    """
    from pydantic_ai import Agent

    kw = build_run_kwargs("gpt-5.6-luna", effort)
    return Agent(kw["model"], model_settings=kw["model_settings"])


@pytest.mark.asyncio
@pytest.mark.parametrize("effort", ["low", "medium", "high", "xhigh", "max"])
async def test_every_effort_tier_is_accepted(effort: str):
    """OpenRouter 400s on an unknown `reasoning.effort`, so this is a real check."""
    result = await _bare_agent(effort).run("Reply with exactly: OK")
    assert "OK" in result.output


@pytest.mark.asyncio
async def test_max_effort_reasons_more_than_low():
    """The tier is honoured downstream, not just accepted by the gateway."""
    low = await _bare_agent("low").run(_REASONING_PROMPT)
    high = await _bare_agent("max").run(_REASONING_PROMPT)

    low_reasoning = low.usage.details.get("reasoning_tokens", 0)
    high_reasoning = high.usage.details.get("reasoning_tokens", 0)
    assert high_reasoning > low_reasoning, (
        f"max effort used {high_reasoning} reasoning tokens vs {low_reasoning} at low — "
        "the effort knob does not appear to be reaching the model."
    )


@pytest.mark.asyncio
async def test_usage_accounting_returns_a_real_cost():
    """`usage.include` is the only cost signal on OpenRouter runs."""
    result = await _bare_agent("low").run("Reply with exactly: OK")

    response = result.all_messages()[-1]
    assert response.provider_name == "openrouter"
    cost = (response.provider_details or {}).get("cost")
    assert isinstance(cost, float) and cost > 0, (
        f"expected a positive reported cost, got {cost!r}. Without it "
        "SerniaOpenRouterModel has nothing to stamp as operation.cost and the "
        "run drops off the LLM-cost dashboard."
    )


@pytest.mark.asyncio
async def test_upstream_provider_is_pinned():
    """Provider pinning keeps the served compute identical to pre-OpenRouter."""
    from api.src.sernia_ai.config import OPENROUTER_ALLOWED_PROVIDERS

    result = await _bare_agent("low").run("Reply with exactly: OK")

    downstream = (result.all_messages()[-1].provider_details or {}).get("downstream_provider", "")
    assert downstream.lower() in {p.lower() for p in OPENROUTER_ALLOWED_PROVIDERS}, (
        f"routed to {downstream!r}, which is not in OPENROUTER_ALLOWED_PROVIDERS"
    )


@pytest.mark.asyncio
async def test_web_search_is_confined_to_allowed_domains():
    """The `web` plugin must honour `include_domains`.

    pydantic-ai drops `WebSearchTool.allowed_domains` on OpenRouter;
    `SerniaOpenRouterModel` re-attaches it. If that regressed, the agent would
    happily cite the whole web.
    """
    from pydantic_ai import Agent
    from pydantic_ai.capabilities import WebSearch
    from pydantic_ai.native_tools import WebSearchTool

    kw = build_run_kwargs("gpt-5.6-luna", "low")
    agent = Agent(
        kw["model"],
        model_settings=kw["model_settings"],
        capabilities=[
            WebSearch(
                native=WebSearchTool(allowed_domains=WEB_SEARCH_ALLOWED_DOMAINS, optional=True),
                local=False,
            )
        ],
    )
    result = await agent.run(
        "Search the web for the average rent of a 2 bedroom apartment in "
        "Pittsburgh PA, and cite your sources with full URLs."
    )

    urls = _cited_urls(result.output)
    assert urls, "expected at least one cited URL — did web search run at all?"
    offenders = sorted({u for u in urls if not _is_allowed(u)})
    assert not offenders, (
        f"web search cited results outside WEB_SEARCH_ALLOWED_DOMAINS: {offenders}"
    )


# OpenRouter surfaces the OpenAI web-search citations inline in the answer text
# (as markdown links), not as structured citation parts, so scrape the text.
_URL_RE = re.compile(r"https?://[^\s)\]<>\"']+")


def _cited_urls(text: str) -> list[str]:
    return _URL_RE.findall(text)


def _is_allowed(url: str) -> bool:
    from urllib.parse import urlparse

    host = (urlparse(url).hostname or "").lower().removeprefix("www.")
    return any(host == d or host.endswith(f".{d}") for d in WEB_SEARCH_ALLOWED_DOMAINS)
