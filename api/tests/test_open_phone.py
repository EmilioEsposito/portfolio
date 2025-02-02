import json
from pprint import pprint
from api.open_phone import OpenPhoneWebhookPayload
# Note: client fixture is automatically available from conftest.py

def test_open_phone_message_received(client):
    """Test the OpenPhone webhook message received endpoint"""
    with open("api/tests/requests/open_phone_message_recieved.json", "r") as f:
        data = json.load(f)

    validation_result = OpenPhoneWebhookPayload.model_validate(data)
    print("\n\nVALIDATION RESULT:")
    pprint(validation_result)

    response = client.post("/api/open_phone/message_received?path=open_phone%2Fmessage_received", json=data)

    response_data = response.json()
    print("\n\nRESPONSE DATA:")
    pprint(response_data)

    assert response.status_code == 200
