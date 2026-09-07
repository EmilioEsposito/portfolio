"""Single inference gateway. Never fall back to a direct provider key."""

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from functools import cache

from openai import AsyncOpenAI, OpenAI
from pydantic_ai.messages import ModelMessage, ModelResponse
from pydantic_ai.models import ModelRequestParameters, StreamedResponse
from pydantic_ai.models.openrouter import OpenRouterModel, OpenRouterModelSettings
from pydantic_ai.providers.openrouter import OpenRouterProvider
from pydantic_ai.settings import ModelSettings
from pydantic_ai.usage import UsageLimits

DEMO_MODEL_ID = "openai/gpt-5.6-luna"
OPERATIONS_MODEL = f"openrouter:{DEMO_MODEL_ID}"
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


def _api_key() -> str:
    key = os.getenv("PORTFOLIO_OPENROUTER_API_KEY") or os.getenv("OPENROUTER_API_KEY")
    if not key:
        raise RuntimeError("PORTFOLIO_OPENROUTER_API_KEY is required for inference")
    return key


@cache
def async_openrouter_client() -> AsyncOpenAI:
    return AsyncOpenAI(api_key=_api_key(), base_url=OPENROUTER_BASE_URL, timeout=30, max_retries=0)


@cache
def openrouter_client() -> OpenAI:
    return OpenAI(api_key=_api_key(), base_url=OPENROUTER_BASE_URL, timeout=30, max_retries=0)


def demo_model_settings() -> OpenRouterModelSettings:
    # "Light" UI language maps to the provider's supported "low" effort.
    return OpenRouterModelSettings(
        max_tokens=1800,
        timeout=30,
        openrouter_reasoning={"effort": "low", "enabled": True},
        openrouter_usage={"include": True},
    )


def demo_usage_limits() -> UsageLimits:
    return UsageLimits(request_limit=5, tool_calls_limit=4, output_tokens_limit=5000)


def public_inference_available() -> bool:
    return bool(os.getenv("PUBLIC_PORTFOLIO_OPENROUTER_API_KEY"))


def async_public_openrouter_client() -> AsyncOpenAI:
    key = os.getenv("PUBLIC_PORTFOLIO_OPENROUTER_API_KEY")
    if not key:
        raise RuntimeError("Public inference is unavailable")
    return _public_client(key)


@cache
def _public_client(key: str) -> AsyncOpenAI:
    return AsyncOpenAI(api_key=key, base_url=OPENROUTER_BASE_URL, timeout=30, max_retries=0)


class PublicOpenRouterModel(OpenRouterModel):
    """Resolve the isolated public credential only when inference starts.

    The inert provider only satisfies model metadata initialization. Its client
    is never used: both inference entrypoints delegate to the dedicated client.
    Missing public configuration cannot break startup or use an operations key.
    """

    def __init__(self) -> None:
        super().__init__(
            DEMO_MODEL_ID, provider=OpenRouterProvider(api_key="public-inference-disabled")
        )

    def _public_model(self) -> OpenRouterModel:
        return OpenRouterModel(
            DEMO_MODEL_ID,
            provider=OpenRouterProvider(openai_client=async_public_openrouter_client()),
        )

    async def request(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
    ) -> ModelResponse:
        return await self._public_model().request(
            messages, model_settings, model_request_parameters
        )

    @asynccontextmanager
    async def request_stream(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
        run_context=None,
    ) -> AsyncIterator[StreamedResponse]:
        async with self._public_model().request_stream(
            messages, model_settings, model_request_parameters, run_context
        ) as response:
            yield response


DEMO_MODEL = PublicOpenRouterModel()
