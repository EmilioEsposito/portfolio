"""Which Clerk tokens the API's shared sign-in check accepts.

Runs the real Clerk SDK's ``authenticate_request``, so its token-type check is
what decides, with only the network step (``verify_token``) stubbed out. The
stub "verifies" every token it is given, so a token type that should be
refused can only be refused by the token-type check itself. Users come from the
module's profile cache, so nothing calls Clerk.
"""

from types import SimpleNamespace

import httpx
import pytest
from clerk_backend_api import Clerk
from clerk_backend_api.security import authenticaterequest
from fastapi import Depends, FastAPI

from api.src.rental_listings import routes as rental_routes
from api.src.utils import clerk as clerk_module

USER_ID = "user_test"
MCP_CLIENT = "client_mcp"

# What Clerk reports for each kind of token: a session token names the user in
# ``sub``; the others name who they belong to in ``subject``.
PAYLOADS = {
    "session": {"sub": USER_ID, "sid": "sess_test"},
    "oat_": {"subject": USER_ID, "client_id": MCP_CLIENT, "scopes": ["email"]},
    "ak_": {"subject": USER_ID, "name": "a key"},
    "mt_": {"subject": "mch_test"},
    "m2m_": {"subject": "mch_test"},
}


def payload_for(token: str) -> dict:
    return next(
        (p for prefix, p in PAYLOADS.items() if token.startswith(prefix)), PAYLOADS["session"]
    )


@pytest.fixture
def clerk(monkeypatch):
    verified = []

    def verify_token(token, options):
        verified.append(token)
        return dict(payload_for(token))

    monkeypatch.setattr(authenticaterequest, "verify_token", verify_token)
    monkeypatch.setattr(clerk_module, "clerk_client", Clerk(bearer_auth="sk_test_dummy"))
    monkeypatch.setenv(rental_routes.MCP_CLIENT_ID_ENV, MCP_CLIENT)
    user = SimpleNamespace(
        id=USER_ID,
        email_addresses=[
            SimpleNamespace(
                email_address="pat@serniacapital.com",
                verification=SimpleNamespace(status="verified"),
            )
        ],
    )
    clerk_module._user_cache.clear()
    clerk_module._set_cached_user(USER_ID, user)
    yield SimpleNamespace(verified=verified)
    clerk_module._user_cache.clear()


@pytest.fixture
def http():
    app = FastAPI()

    @app.get("/sernia")
    async def sernia(user=Depends(clerk_module.verify_serniacapital_user)):
        return {"user": user.id}

    @app.get("/rental-listings")
    async def rental_listings(caller=Depends(rental_routes.rental_listing_caller)):
        return {"user": caller.user.id, "source": caller.source}

    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


def bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_a_browser_session_token_is_accepted(clerk, http):
    async with http:
        response = await http.get("/sernia", headers=bearer("eyJhbGciOi.session.jwt"))

    assert response.status_code == 200 and response.json() == {"user": USER_ID}
    assert clerk.verified == ["eyJhbGciOi.session.jwt"]


@pytest.mark.asyncio
async def test_a_session_cookie_is_accepted(clerk, http):
    async with http:
        response = await http.get("/sernia", headers={"cookie": "__session=eyJhbGciOi.session.jwt"})

    assert response.status_code == 200


@pytest.mark.asyncio
@pytest.mark.parametrize("token", ["oat_abc", "ak_abc", "mt_abc", "m2m_abc"])
async def test_other_clerk_tokens_are_refused_as_signed_out(clerk, http, token):
    async with http:
        response = await http.get("/sernia", headers=bearer(token))

    assert response.status_code == 401
    assert clerk.verified == []  # Refused by type, before anything reads a payload.


@pytest.mark.asyncio
async def test_no_token_is_refused(clerk, http):
    async with http:
        response = await http.get("/sernia")

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_the_mcp_oauth_token_still_reaches_rental_listings_through_its_own_path(clerk, http):
    async with http:
        response = await http.get("/rental-listings", headers=bearer("oat_abc"))

    assert response.status_code == 200
    assert response.json() == {"user": USER_ID, "source": rental_routes.MCP_SOURCE}


@pytest.mark.asyncio
@pytest.mark.parametrize("token", ["ak_abc", "mt_abc"])
async def test_rental_listings_refuse_api_keys_and_machine_tokens(clerk, http, token):
    async with http:
        response = await http.get("/rental-listings", headers=bearer(token))

    assert response.status_code == 401
