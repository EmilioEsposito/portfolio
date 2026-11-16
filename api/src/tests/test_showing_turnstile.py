"""``turnstile.verify``: when Cloudflare is asked, and the test secret's shortcut outside production.

No database and no network: Cloudflare is an ``httpx.MockTransport`` that
records each check, so a test can see whether it was asked at all.
"""

import httpx
import pytest

from api.src.showing_bookings import turnstile


@pytest.fixture
def cloudflare(monkeypatch):
    for name in ("RAILWAY_ENVIRONMENT_NAME", turnstile.SECRET_ENV, turnstile.SITE_KEY_ENV):
        monkeypatch.delenv(name, raising=False)
    checks: list[dict[str, str]] = []

    def answer(request: httpx.Request) -> httpx.Response:
        form = dict(httpx.QueryParams(request.content.decode()))
        checks.append(form)
        return httpx.Response(200, json={"success": form["response"] == "good-token"})

    monkeypatch.setattr(
        turnstile, "client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(answer))
    )
    return checks


@pytest.mark.asyncio
async def test_outside_production_the_test_secret_passes_any_token_without_a_call(cloudflare):
    assert turnstile.secret_key() == turnstile.TEST_SECRET
    assert await turnstile.verify("any-token") is True
    assert await turnstile.verify("another-token", "203.0.113.7") is True
    assert cloudflare == []


@pytest.mark.asyncio
async def test_the_shortcut_still_refuses_an_empty_or_oversized_token(cloudflare):
    assert await turnstile.verify(None) is False
    assert await turnstile.verify("") is False
    assert await turnstile.verify("x" * (turnstile.MAX_TOKEN_LENGTH + 1)) is False
    assert cloudflare == []


@pytest.mark.asyncio
async def test_the_test_secret_set_by_name_outside_production_also_skips_the_call(
    cloudflare, monkeypatch
):
    monkeypatch.setenv(turnstile.SECRET_ENV, turnstile.TEST_SECRET)
    monkeypatch.setenv("RAILWAY_ENVIRONMENT_NAME", "portfolio-pr-401")
    assert await turnstile.verify("any-token") is True
    assert cloudflare == []


@pytest.mark.asyncio
async def test_another_secret_asks_cloudflare(cloudflare, monkeypatch):
    monkeypatch.setenv(turnstile.SECRET_ENV, "made-up-secret")
    assert await turnstile.verify("good-token", "203.0.113.7") is True
    assert await turnstile.verify("bad-token") is False
    assert [(c["secret"], c["response"], c.get("remoteip")) for c in cloudflare] == [
        ("made-up-secret", "good-token", "203.0.113.7"),
        ("made-up-secret", "bad-token", None),
    ]


@pytest.mark.asyncio
async def test_production_asks_cloudflare_even_with_the_test_secret(cloudflare, monkeypatch):
    monkeypatch.setenv("RAILWAY_ENVIRONMENT_NAME", "production")
    monkeypatch.setenv(turnstile.SECRET_ENV, turnstile.TEST_SECRET)
    assert await turnstile.verify("bad-token") is False
    assert [c["secret"] for c in cloudflare] == [turnstile.TEST_SECRET]


@pytest.mark.asyncio
async def test_production_without_a_secret_is_unavailable(cloudflare, monkeypatch):
    monkeypatch.setenv("RAILWAY_ENVIRONMENT_NAME", "production")
    with pytest.raises(turnstile.TurnstileUnavailable):
        await turnstile.verify("any-token")
    assert cloudflare == []
