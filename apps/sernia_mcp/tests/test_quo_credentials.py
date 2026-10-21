"""Credential selection must match the backend without legacy fallbacks."""

import pytest

from sernia_mcp.clients.quo import build_quo_client


@pytest.mark.asyncio
@pytest.mark.parametrize("configured", [True, False])
async def test_client_uses_only_portfolio_quo_key(monkeypatch, configured):
    monkeypatch.setenv("OPEN_PHONE_API_KEY", "test-legacy-openphone")
    monkeypatch.setenv("QUO_API_KEY", "test-legacy-quo")
    monkeypatch.delenv("QUO_API_KEY_PORTFOLIO", raising=False)
    if configured:
        monkeypatch.setenv("QUO_API_KEY_PORTFOLIO", "test-portfolio-quo")
    async with build_quo_client() as client:
        assert client.headers["Authorization"] == ("test-portfolio-quo" if configured else "")
