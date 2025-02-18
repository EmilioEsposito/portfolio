import pytest
from api_src.tests.conftest import *

@pytest.mark.asyncio
async def test_get_zillow_emails(client):
    """Test the /gmail/get_zillow_emails endpoint"""
    # Make the request to the endpoint
    response = client.get("/api/google/gmail/get_zillow_emails")
    
    # Check status code
    assert response.status_code == 200
    
    # Parse the response
    emails = response.json()
    
    # Verify the response structure
    assert type(emails) == list and len(emails) > 0
