import json
import os
from pprint import pprint

import pytest
from fastapi.testclient import TestClient
from pytest import fixture

# Payload fixtures are real webhook captures and are gitignored
# (api/src/tests/requests/* in .gitignore). Skip the whole module where
# they aren't present (fresh clones, CI, Claude Code on web).
pytestmark = pytest.mark.skipif(
    not os.path.isdir("api/src/tests/requests"),
    reason="requires gitignored fixture payloads in api/src/tests/requests/",
)
import uuid
from unittest.mock import AsyncMock, patch

from api.index import app
from api.src.open_phone.routes import OpenPhoneWebhookPayload, verify_open_phone_signature
from api.src.utils.clerk import verify_serniacapital_user
from api.src.utils.dependencies import verify_admin_or_serniacapital, verify_cron_or_admin
from api.src.utils.password import verify_admin_auth


@pytest.fixture(autouse=True, scope="module")
def mock_background_services_startup():
    """
    Mocks the startup of APScheduler etc to speed up tests in this module.
    Prevents actual scheduler/service startup during testing.
    """
    with patch(
        "api.index._apscheduler_startup_async", new_callable=AsyncMock
    ) as mock_scheduler_start:
        yield mock_scheduler_start


async def mock_verify(*args, **kwargs):
    return True


# @fixture
# def mock_db_session():
#     """Mock database session"""
#     session = AsyncMock(spec=AsyncSession)
#     session.commit = AsyncMock()
#     session.rollback = AsyncMock()
#     session.refresh = AsyncMock()
#     return session


@fixture
def mocked_client():
    with TestClient(app) as client:
        # Override the dependencies
        app.dependency_overrides[verify_open_phone_signature] = lambda: True
        app.dependency_overrides[verify_admin_or_serniacapital] = lambda: True
        app.dependency_overrides[verify_serniacapital_user] = lambda: True
        app.dependency_overrides[verify_admin_auth] = lambda: True
        app.dependency_overrides[verify_cron_or_admin] = lambda: True
        # app.dependency_overrides[get_session] = lambda: mock_db_session
        yield client
    # Clean up after the test
    app.dependency_overrides.clear()


def test_open_phone_webhook_message_received(mocked_client):
    """Test the OpenPhone webhook with a full message-received payload"""
    with open("api/src/tests/requests/open_phone_message_received_FULL_PAYLOAD.json") as f:
        request = json.load(f)

    # create random event id
    request["body"]["id"] = str(uuid.uuid4())

    headers = request["headers"]
    body = request["body"]

    validation_result = OpenPhoneWebhookPayload.model_validate(body)
    print("\n\nVALIDATION RESULT:")
    pprint(validation_result)

    response = mocked_client.post("/api/open_phone/webhook", json=body, headers=headers)

    response_data = response.json()
    print("\n\nRESPONSE DATA:")
    pprint(response_data)

    assert response.status_code == 200


def test_open_phone_webhook_contact_updated(mocked_client):
    """Test the OpenPhone webhook contact-updated payload validation"""
    with open("api/src/tests/requests/open_phone_contact_updated.json") as f:
        body = json.load(f)["object"]

    try:
        OpenPhoneWebhookPayload.model_validate(body)
    except Exception as e:
        print("\n\nEXCEPTION:")
        pprint(e)
        raise e


def test_open_phone_webhook_call_summary_completed(mocked_client):
    """Test the OpenPhone webhook message received endpoint"""
    with open("api/src/tests/requests/open_phone_call_summary_completed.json") as f:
        body = json.load(f)["object"]

    try:
        OpenPhoneWebhookPayload.model_validate(body)
    except Exception as e:
        print("\n\nEXCEPTION:")
        pprint(e)
        raise e


def test_open_phone_webhook_call_transcript_completed(mocked_client):
    """Test the OpenPhone webhook message received endpoint"""
    with open("api/src/tests/requests/open_phone_call_transcript_completed.json") as f:
        body = json.load(f)["object"]

    try:
        OpenPhoneWebhookPayload.model_validate(body)
    except Exception as e:
        print("\n\nEXCEPTION:")
        pprint(e)
        raise e


@pytest.mark.live
def test_get_contacts_success(mocked_client):
    """Test successful contact retrieval"""

    response = mocked_client.get(
        "/api/open_phone/contacts", params={"external_ids": ["e8024958857"]}
    )

    response_data = response.json()
    print("\n\nRESPONSE DATA:")
    pprint(response_data)

    assert response.status_code == 200
