"""Offline regressions for the operational credential used by direct evals and clients."""

import httpx
import pytest

import api
from api.src.open_phone.typesafe_openrouter import create_jev_client
from api.src.utils.llm import async_openrouter_client, openrouter_client
from api.src.utils.openrouter_models import resolve_model


@pytest.mark.parametrize("local_key", [None, "", "hosted", "ambient", "public"])
@pytest.mark.asyncio
async def test_local_clients_reject_unsafe_keys(monkeypatch, local_key):
    monkeypatch.setenv("PORTFOLIO_OPENROUTER_API_KEY", "hosted")
    monkeypatch.setenv("OPENROUTER_API_KEY", "ambient")
    monkeypatch.setenv("PUBLIC_PORTFOLIO_OPENROUTER_API_KEY", "public")
    monkeypatch.setenv("RAILWAY_ENVIRONMENT_NAME", "production")
    if local_key is None:
        monkeypatch.delenv("LOCAL_PORTFOLIO_OPENROUTER_API_KEY", raising=False)
    else:
        monkeypatch.setenv("LOCAL_PORTFOLIO_OPENROUTER_API_KEY", local_key)

    for factory in (
        async_openrouter_client.__wrapped__,
        openrouter_client.__wrapped__,
        create_jev_client,
    ):
        with pytest.raises(ValueError, match="LOCAL_PORTFOLIO_OPENROUTER_API_KEY"):
            factory()

    async def unexpected_network(request):
        pytest.fail("An unsafe credential reached the HTTP transport")

    client = resolve_model("openrouter:test/unsafe-key").client
    async with httpx.AsyncClient(transport=httpx.MockTransport(unexpected_network)) as transport:
        with pytest.raises(ValueError, match="LOCAL_PORTFOLIO_OPENROUTER_API_KEY"):
            await client.with_options(http_client=transport).models.list()


@pytest.mark.asyncio
async def test_eval_model_uses_local_key_even_with_ambient_sdk_key(monkeypatch):
    monkeypatch.setenv("PORTFOLIO_OPENROUTER_API_KEY", "hosted")
    monkeypatch.setenv("OPENROUTER_API_KEY", "ambient")
    monkeypatch.setenv("LOCAL_PORTFOLIO_OPENROUTER_API_KEY", "local")

    def check_auth(request):
        assert request.headers["authorization"] == "Bearer local"
        return httpx.Response(200, json={"object": "list", "data": []})

    client = resolve_model("openrouter:test/local-key").client
    async with httpx.AsyncClient(transport=httpx.MockTransport(check_auth)) as transport:
        await client.with_options(http_client=transport).models.list()


def test_hosted_api_uses_deployed_key(monkeypatch):
    monkeypatch.setattr(api, "HOSTED_API", True)
    monkeypatch.setenv("PORTFOLIO_OPENROUTER_API_KEY", "hosted")
    monkeypatch.setenv("LOCAL_PORTFOLIO_OPENROUTER_API_KEY", "local")
    assert api.operational_openrouter_key() == "hosted"
    monkeypatch.delenv("PORTFOLIO_OPENROUTER_API_KEY")
    with pytest.raises(ValueError, match="PORTFOLIO_OPENROUTER_API_KEY"):
        api.operational_openrouter_key()
