import json
from pprint import pprint
from fastapi.testclient import TestClient
from pytest import fixture
from api.index import app
from api.open_phone import OpenPhoneWebhookPayload, verify_open_phone_signature
import os

async def mock_verify(*args, **kwargs):
    return True


@fixture
def mocked_client():
    with TestClient(app) as client:
        # Override the dependency directly in the app
        app.dependency_overrides[verify_open_phone_signature] = lambda: True
        yield client
    # Clean up after the test
    app.dependency_overrides.clear()


def test_open_phone_message_received(mocked_client):
    """Test the OpenPhone webhook message received endpoint"""
    with open("api/tests/requests/open_phone_message_received.json", "r") as f:
        request = json.load(f)

    headers = request["headers"]
    body = request["body"]

    validation_result = OpenPhoneWebhookPayload.model_validate(body)
    print("\n\nVALIDATION RESULT:")
    pprint(validation_result)

    response = mocked_client.post(
        "/api/open_phone/message_received", json=body, headers=headers
    )

    response_data = response.json()
    print("\n\nRESPONSE DATA:")
    pprint(response_data)

    assert response.status_code == 200


def test_get_contacts_success(client):
    """Test successful contact retrieval"""

    response = client.get(
        "/api/open_phone/contacts", params={"external_ids": ["e8024958857"]}
    )

    response_data = response.json()
    print("\n\nRESPONSE DATA:")
    pprint(response_data)

    assert response.status_code == 200

 
def test_send_message_to_building(client):
    """Test sending a message to a building"""

    data = {
        "building_name": "Test", 
        "message": "Hello, world from Test!",
        "password": os.environ['LOCAL_ADMIN_PASSWORD']
    }
    response = client.post(
        "/api/open_phone/send_message_to_building",
        json=data,
    )
    assert response.status_code == 200
