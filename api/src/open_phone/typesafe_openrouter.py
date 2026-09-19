"""Use the TypeSafe SDK with OpenRouter's compatible Decisions API."""

import os

import httpx2
from pydantic import Field
from typesafe_sdk import AsyncTypeSafeClient, RetryPolicy, SystemOneResponse, Usage

JEV_MODEL = "typesafe/jev-1.13"


class OpenRouterUsage(Usage):
    cost: float | None = Field(default=None, ge=0, allow_inf_nan=False)


class OpenRouterDecisionResponse(SystemOneResponse):
    usage: OpenRouterUsage


async def _route_decisions(request: httpx2.Request) -> None:
    """Rewrite only the SDK's system_one route; fail on unsupported SDK methods."""
    if request.url != httpx2.URL("https://openrouter.ai/v1/systemone"):
        raise ValueError("The OpenRouter TypeSafe adapter supports only system_one")
    request.url = request.url.copy_with(path="/api/alpha/decisions")


def create_jev_client(
    *, timeout: float = 30.0, transport: httpx2.AsyncBaseTransport | None = None
) -> AsyncTypeSafeClient:
    """Caller must close the returned client (prefer ``async with``).

    The escalation caller owns retries, so disable nested SDK retries. Explicit
    credentials/base URL prevent accidental fallback to the direct TypeSafe API.
    """
    api_key = os.getenv("PORTFOLIO_OPENROUTER_API_KEY")
    if not api_key:
        raise ValueError("PORTFOLIO_OPENROUTER_API_KEY is required for Jev")
    return AsyncTypeSafeClient(
        api_key=api_key,
        model=JEV_MODEL,
        base_url="https://openrouter.ai",
        retry=RetryPolicy(max_retries=0),
        timeout=timeout,
        http_client=httpx2.AsyncClient(
            timeout=timeout,
            transport=transport,
            event_hooks={"request": [_route_decisions]},
        ),
    )
