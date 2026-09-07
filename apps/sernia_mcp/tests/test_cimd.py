"""Real OAuth routes with isolated storage and external client metadata fixtures."""

import json
import time
from uuid import uuid4

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastmcp.server.auth.providers.clerk import ClerkProvider
from fastmcp.server.auth.ssrf import SSRFFetchResponse
from key_value.aio.stores.memory import MemoryStore
from starlette.applications import Starlette


@pytest.fixture(params=[False, True], ids=["clerk", "clerk-and-bearer"])
def oauth(monkeypatch, request):
    from sernia_mcp import server

    for name, value in {
        "FASTMCP_SERVER_AUTH_CLERK_DOMAIN": "test.clerk.accounts.dev",
        "FASTMCP_SERVER_AUTH_CLERK_CLIENT_ID": "test-client",
        "FASTMCP_SERVER_AUTH_CLERK_CLIENT_SECRET": "test-upstream-secret",
        "SERNIA_MCP_BASE_URL": "https://mcp.example.test",
    }.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(server, "clerk_oauth_configured", lambda: True)
    monkeypatch.setattr(server, "internal_bearer_configured", lambda: request.param)
    original = ClerkProvider.__init__

    def isolated(self, **kwargs):
        original(self, **kwargs, client_storage=MemoryStore())

    monkeypatch.setattr(ClerkProvider, "__init__", isolated)
    provider = server._build_auth_provider()
    return Starlette(routes=provider.get_routes("/mcp"))


async def test_cimd_discovery_and_signed_token_audience(oauth, monkeypatch):
    client_id = "https://chatgpt.com/oauth/client.json"
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
    document = {
        "client_id": client_id,
        "client_name": "ChatGPT fixture",
        "redirect_uris": ["https://chatgpt.com/connector_platform_oauth_redirect"],
        "token_endpoint_auth_method": "private_key_jwt",
        "token_endpoint_auth_methods_supported": ["none", "private_key_jwt"],
        "jwks": {"keys": [{**jwk, "kid": "test", "alg": "RS256"}]},
    }

    async def fetch(url, **kwargs):
        assert url == client_id
        return SSRFFetchResponse(json.dumps(document).encode(), 200, {})

    monkeypatch.setattr("fastmcp.server.auth.cimd.ssrf_safe_fetch_response", fetch)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=oauth), base_url="https://mcp.example.test"
    ) as client:
        metadata = (await client.get("/.well-known/oauth-authorization-server")).json()
        assert metadata["client_id_metadata_document_supported"] is True
        assert {"none", "private_key_jwt"} <= set(metadata["token_endpoint_auth_methods_supported"])
        assert metadata["token_endpoint"] == "https://mcp.example.test/token"
        now = int(time.time())
        assertion = jwt.encode(
            {
                "iss": client_id,
                "sub": client_id,
                "aud": metadata["token_endpoint"],
                "iat": now,
                "exp": now + 120,
                "jti": uuid4().hex,
            },
            key,
            algorithm="RS256",
            headers={"kid": "test"},
        )
        form = {
            "grant_type": "authorization_code",
            "client_id": client_id,
            "code": "nonexistent-code",
            "code_verifier": "a" * 64,
            "redirect_uri": document["redirect_uris"][0],
            "client_assertion_type": "urn:ietf:params:oauth:client-assertion-type:jwt-bearer",
            "client_assertion": assertion,
        }
        response = await client.post("/token", data=form)
        # Authentication succeeded against the advertised audience; the absent grant is rejected.
        assert response.status_code in {400, 401}
        assert response.json()["error"] == "invalid_grant"
        assert (await client.post("/token", data=form)).json()["error"] == "invalid_client"


@pytest.mark.parametrize(
    "redirect_uri",
    [
        "https://claude.ai/api/mcp/auth_callback",
        "https://chatgpt.com/connector_platform_oauth_redirect",
        "https://chatgpt.com/connector/oauth/test-connector",
        "http://127.0.0.1:43123/callback",
    ],
)
async def test_dcr_cloud_and_local_callbacks_remain_available(oauth, redirect_uri):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=oauth), base_url="https://mcp.example.test"
    ) as client:
        response = await client.post(
            "/register",
            json={
                "client_name": "Legacy client",
                "redirect_uris": [redirect_uri],
                "token_endpoint_auth_method": "none",
            },
        )
        assert response.status_code == 201
        assert response.json()["redirect_uris"] == [redirect_uri]
