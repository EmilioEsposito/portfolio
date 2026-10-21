"""Offline credential selection regressions; no provider requests."""

import pytest


@pytest.mark.asyncio
@pytest.mark.parametrize("configured", [True, False])
async def test_backend_clients_use_only_portfolio_quo_key(monkeypatch, configured):
    from api.src.open_phone.service import _openphone_client
    from api.src.sernia_ai.tools.quo_tools import _build_quo_client

    monkeypatch.setenv("OPEN_PHONE_API_KEY", "test-legacy-openphone")
    monkeypatch.setenv("QUO_API_KEY", "test-legacy-quo")
    monkeypatch.delenv("QUO_API_KEY_PORTFOLIO", raising=False)
    if configured:
        monkeypatch.setenv("QUO_API_KEY_PORTFOLIO", "test-portfolio-quo")
    for factory in (_openphone_client, _build_quo_client):
        async with factory() as client:
            assert client.headers["Authorization"] == ("test-portfolio-quo" if configured else "")
