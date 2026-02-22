import json
from pprint import pprint
from fastapi.testclient import TestClient
from pytest import fixture
import pytest
from unittest.mock import AsyncMock, MagicMock, patch, Mock
from api.index import app
from api.src.open_phone.routes import OpenPhoneWebhookPayload, verify_open_phone_signature
from api.src.utils.password import verify_admin_auth
from api.src.utils.dependencies import verify_cron_or_admin
from datetime import datetime
from pprint import pprint
from sqlalchemy.ext.asyncio import AsyncSession
from api.src.database.database import get_session
import uuid
from api.src.utils.dependencies import verify_admin_or_serniacapital
from api.src.utils.clerk import verify_serniacapital_user

@pytest.fixture(autouse=True, scope="module")
def mock_background_services_startup():
    """
    Mocks the startup of APScheduler etc to speed up tests in this module.
    Prevents actual scheduler/service startup during testing.
    """
    with patch('api.index.scheduler.start', autospec=True) as mock_scheduler_start, \
         patch('api.index.zillow_email_service.start_service', new_callable=AsyncMock) as mock_zillow_start:
        
        print(f"\n--- Mocking background services for tests in module ---")
        print(f"Mocked api.index.scheduler.start: {mock_scheduler_start}")
        print(f"Mocked api.index.zillow_email_service.start_service: {mock_zillow_start}")
        print(f"-------------------------------------------------------\n")
        yield mock_scheduler_start, mock_zillow_start
    print("\n--- Background services unmocked for module ---\n")

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

def test_open_phone_webhook(mocked_client):
    """Test the OpenPhone webhook message received endpoint"""
    with open("api/src/tests/requests/open_phone_message_received_FULL_PAYLOAD.json", "r") as f:
        request = json.load(f)

    # create random event id
    request["body"]["id"] = str(uuid.uuid4())

    headers = request["headers"]
    body = request["body"]

    validation_result = OpenPhoneWebhookPayload.model_validate(body)
    print("\n\nVALIDATION RESULT:")
    pprint(validation_result)

    response = mocked_client.post(
        "/api/open_phone/webhook", json=body, headers=headers
    )

    response_data = response.json()
    print("\n\nRESPONSE DATA:")
    pprint(response_data)

    assert response.status_code == 200


def test_open_phone_webhook(mocked_client):
    """Test the OpenPhone webhook message received endpoint"""
    with open("api/src/tests/requests/open_phone_contact_updated.json", "r") as f:
        body = json.load(f)['object']

    try:
        OpenPhoneWebhookPayload.model_validate(body)
    except Exception as e:
        print("\n\nEXCEPTION:")
        pprint(e)
        raise e


def test_open_phone_webhook_call_summary_completed(mocked_client):
    """Test the OpenPhone webhook message received endpoint"""
    with open("api/src/tests/requests/open_phone_call_summary_completed.json", "r") as f:
        body = json.load(f)['object']

    try:
        OpenPhoneWebhookPayload.model_validate(body)
    except Exception as e:
        print("\n\nEXCEPTION:")
        pprint(e)
        raise e


def test_open_phone_webhook_call_transcript_completed(mocked_client):
    """Test the OpenPhone webhook message received endpoint"""
    with open("api/src/tests/requests/open_phone_call_transcript_completed.json", "r") as f:
        body = json.load(f)['object']

    try:
        OpenPhoneWebhookPayload.model_validate(body)
    except Exception as e:
        print("\n\nEXCEPTION:")
        pprint(e)
        raise e


def test_get_contacts_success(mocked_client):
    """Test successful contact retrieval"""

    response = mocked_client.get(
        "/api/open_phone/contacts", params={"external_ids": ["e8024958857"]}
    )

    response_data = response.json()
    print("\n\nRESPONSE DATA:")
    pprint(response_data)

    assert response.status_code == 200


