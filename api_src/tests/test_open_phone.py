import json
from pprint import pprint
from fastapi.testclient import TestClient
from pytest import fixture
import pytest
from unittest.mock import AsyncMock, MagicMock, patch, Mock
from api.index import app
from api_src.open_phone.routes import OpenPhoneWebhookPayload, verify_open_phone_signature
from api_src.utils.password import verify_admin_auth
from api_src.utils.dependencies import verify_cron_or_admin
from datetime import datetime
from pprint import pprint
from sqlalchemy.ext.asyncio import AsyncSession
from api_src.database.database import get_session
import uuid

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
        app.dependency_overrides[verify_admin_auth] = lambda: True
        app.dependency_overrides[verify_cron_or_admin] = lambda: True
        # app.dependency_overrides[get_session] = lambda: mock_db_session
        yield client
    # Clean up after the test
    app.dependency_overrides.clear()

def test_open_phone_webhook(mocked_client):
    """Test the OpenPhone webhook message received endpoint"""
    with open("api_src/tests/requests/open_phone_message_received_FULL_PAYLOAD.json", "r") as f:
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


def test_get_contacts_success(mocked_client):
    """Test successful contact retrieval"""

    response = mocked_client.get(
        "/api/open_phone/contacts", params={"external_ids": ["e8024958857"]}
    )

    response_data = response.json()
    print("\n\nRESPONSE DATA:")
    pprint(response_data)

    assert response.status_code == 200

 
def test_send_tenant_mass_message(mocked_client):
    """Test sending a message to a building"""

    data = {
        "property_names": ["Test"], 
        "message": "Please ignore, this is a test."
    }
    response = mocked_client.post(
        "/api/open_phone/tenant_mass_message",
        json=data,
    )
    assert response.status_code == 200


def test_check_unreplied_emails(mocked_client):
    """Test the check_unreplied_emails endpoint with a custom phone number"""

    # Test with custom phone number
    target_phone = "+14123703550"
    response = mocked_client.post(
        "/api/cron/check_unreplied_emails",
        params={"target_phone_number": target_phone}
    )

    
    # Verify the response
    # assert response.status_code == 200
    response_data = response.json()
    print("\n\nRESPONSE DATA:")
    pprint(response_data)

    assert response.status_code == 200
