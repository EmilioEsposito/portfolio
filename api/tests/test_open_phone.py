import json
from pprint import pprint
from api.open_phone import OpenPhoneWebhookPayload

# Note: client fixture is automatically available from conftest.py


def test_open_phone_message_received(client):
    """Test the OpenPhone webhook message received endpoint"""
    with open("api/tests/requests/open_phone_message_received.json", "r") as f:
        request = json.load(f)

    headers = request["headers"]
    body = request["body"]

    validation_result = OpenPhoneWebhookPayload.model_validate(body)
    print("\n\nVALIDATION RESULT:")
    pprint(validation_result)

    response = client.post(
        "/api/open_phone/message_received", json=body, headers=headers
    )

    response_data = response.json()
    print("\n\nRESPONSE DATA:")
    pprint(response_data)

    assert response.status_code == 200
