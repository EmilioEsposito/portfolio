"""
Integration test for OAuth credential persistence.

Moved verbatim from ``api/src/oauth/service.py``. Requires the gitignored
local fixture ``api/src/tests/sensitive/creds_response.pkl`` (skips when
absent) and a reachable Postgres database:

    pytest api/src/tests/test_oauth_service.py -v -s
"""

import os

import pytest

from api.src.database.database import AsyncSessionFactory
from api.src.oauth.service import save_oauth_credentials


@pytest.mark.asyncio
@pytest.mark.skipif(
    not os.path.exists("api/src/tests/sensitive/creds_response.pkl"),
    reason="requires gitignored local fixture api/src/tests/sensitive/creds_response.pkl",
)
async def test_save_oauth_credentials():
    import pickle

    with open("api/src/tests/sensitive/creds_response.pkl", "rb") as f:
        creds_response = pickle.load(f)

    user_id = "user_2tHQGipY2lem9Xat1823wKuGl7J"
    provider = "oauth_google"

    session = AsyncSessionFactory()
    await save_oauth_credentials(session, user_id, provider, creds_response=creds_response)
    await session.close()
