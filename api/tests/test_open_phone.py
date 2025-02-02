import json
from pprint import pprint

# Note: client fixture is automatically available from conftest.py

def test_open_phone_message_received(client):
    """Test the OpenPhone webhook message received endpoint"""
    with open("api/tests/requests/open_phone_message_recieved.json", "r") as f:
        data = json.load(f)

    response = client.post("/api/open_phone/message_received", json=data)

    response_data = response.json()
    print("\n\nRESPONSE DATA:")
    pprint(response_data)

    assert response.status_code == 200

def test_open_phone_message_received_string_payload(client):
    """Test with string payload to simulate potential OpenPhone behavior"""
    with open("api/tests/requests/open_phone_message_recieved.json", "r") as f:
        data = json.load(f)
    
    # Try with string payload and explicit content type
    response = client.post(
        "/api/open_phone/message_received", 
        data=json.dumps(data),
        headers={"Content-Type": "application/json"}
    )
    
    print("\n\nString payload test response:", response.status_code)
    if response.status_code != 200:
        print(response.json())
