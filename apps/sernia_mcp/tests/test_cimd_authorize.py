"""Exercise ChatGPT CIMD discovery, consent and callback through real OAuth routes."""

import json
from contextlib import asynccontextmanager
from html.parser import HTMLParser
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from fastmcp.server.auth.providers.clerk import ClerkProvider
from fastmcp.server.auth.ssrf import SSRFFetchError, SSRFFetchResponse
from key_value.aio.stores.memory import MemoryStore
from starlette.applications import Starlette

BASE_URL = "https://mcp.example.test"
CLIENT_ID = "https://chatgpt.com/oauth/client.json"
REDIRECT_URI = "https://chatgpt.com/connector_platform_oauth_redirect"


class ConsentForm(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.fields = {}
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "input" and attrs.get("type") == "hidden":
            self.fields[attrs["name"]] = attrs.get("value", "")


@pytest.fixture(params=[False, True], ids=["clerk", "clerk-and-bearer"])
def cimd_server(monkeypatch, request):
    from sernia_mcp import server

    for name, value in {
        "FASTMCP_SERVER_AUTH_CLERK_DOMAIN": "test.clerk.accounts.dev",
        "FASTMCP_SERVER_AUTH_CLERK_CLIENT_ID": "test-client",
        "FASTMCP_SERVER_AUTH_CLERK_CLIENT_SECRET": "test-upstream-secret",
        "SERNIA_MCP_BASE_URL": BASE_URL,
    }.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(server, "clerk_oauth_configured", lambda: True)
    monkeypatch.setattr(server, "internal_bearer_configured", lambda: request.param)
    original = ClerkProvider.__init__

    def isolated(self, **kwargs):
        original(self, **kwargs, client_storage=MemoryStore())

    monkeypatch.setattr(ClerkProvider, "__init__", isolated)
    provider = server._build_auth_provider()
    exchange = AsyncMock(return_value={"access_token": "synthetic-upstream-token"})

    @asynccontextmanager
    async def upstream_client():
        yield SimpleNamespace(fetch_token=exchange)

    monkeypatch.setattr(provider, "_upstream_oauth_client", upstream_client)
    return Starlette(routes=provider.get_routes("/mcp")), exchange


def client_document(monkeypatch, *, status=200, client_id=CLIENT_ID):
    document = {
        "client_id": client_id,
        "client_name": "ChatGPT fixture",
        "redirect_uris": [REDIRECT_URI],
        "token_endpoint_auth_method": "none",
    }

    async def fetch(url, **kwargs):
        assert url == CLIENT_ID
        if status != 200:
            # The SSRF-safe transport raises for HTTP errors before CIMD parsing.
            raise SSRFFetchError(f"HTTP {status} fetching client metadata")
        return SSRFFetchResponse(json.dumps(document).encode(), status, {})

    monkeypatch.setattr("fastmcp.server.auth.cimd.ssrf_safe_fetch_response", fetch)


def authorization_params(**overrides):
    return {
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "state": "synthetic-client-state",
        "code_challenge": "a" * 43,
        "code_challenge_method": "S256",
        **overrides,
    }


@pytest.mark.parametrize("outcome", ["success", "upstream-denied", "consent-denied"])
async def test_stable_cimd_and_rfc9207_authorization_responses(cimd_server, monkeypatch, outcome):
    app, exchange = cimd_server
    client_document(monkeypatch)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url=BASE_URL
    ) as client:
        metadata = (await client.get("/.well-known/oauth-authorization-server")).json()
        resource = (await client.get("/.well-known/oauth-protected-resource/mcp")).json()
        assert metadata["client_id_metadata_document_supported"] is True
        assert metadata["authorization_response_iss_parameter_supported"] is True
        assert resource["authorization_servers"] == [metadata["issuer"]]
        start = await client.get("/authorize", params=authorization_params())
        assert start.status_code == 302
        assert urlsplit(start.headers["location"]).path == "/consent"
        consent = await client.get(start.headers["location"])
        assert consent.status_code == 200
        form = ConsentForm(consent.text).fields
        assert form["csrf_token"]
        form["action"] = "deny" if outcome == "consent-denied" else "approve"
        response = await client.post("/consent", data=form)
        assert response.status_code == 302
        if outcome != "consent-denied":
            upstream = urlsplit(response.headers["location"])
            assert upstream.hostname == "test.clerk.accounts.dev"
            transaction_id = parse_qs(upstream.query)["state"][0]
            callback = {"state": transaction_id}
            callback.update(
                {"code": "synthetic-idp-code"}
                if outcome == "success"
                else {"error": "access_denied"}
            )
            response = await client.get("/auth/callback", params=callback)
        assert response.status_code == 302
        redirect = urlsplit(response.headers["location"])
        assert f"{redirect.scheme}://{redirect.netloc}{redirect.path}" == REDIRECT_URI
        result = parse_qs(redirect.query)
        assert result["iss"] == [metadata["issuer"]]
        assert result["state"] == ["synthetic-client-state"]
        if outcome == "success":
            assert result["code"]
            assert "error" not in result
            exchange.assert_awaited_once()
        else:
            assert result["error"] == ["access_denied"]
            assert "code" not in result
            exchange.assert_not_awaited()


@pytest.mark.parametrize("invalid", ["missing-document", "mismatched-client", "unlisted-redirect"])
async def test_invalid_cimd_authorization_never_redirects(cimd_server, monkeypatch, invalid):
    app, exchange = cimd_server
    client_document(
        monkeypatch,
        status=404 if invalid == "missing-document" else 200,
        client_id="https://other.example/client.json"
        if invalid == "mismatched-client"
        else CLIENT_ID,
    )
    params = authorization_params()
    if invalid == "unlisted-redirect":
        params["redirect_uri"] = "https://attacker.example/callback"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url=BASE_URL
    ) as client:
        response = await client.get("/authorize", params=params)
    assert response.status_code == 400
    assert "location" not in response.headers
    exchange.assert_not_awaited()
