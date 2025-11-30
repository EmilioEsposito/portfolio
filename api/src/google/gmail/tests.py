import pytest
from api.src.tests.conftest import *
import os
import logfire

@pytest.mark.asyncio
async def test_get_zillow_emails(client):
    """Test the /gmail/get_zillow_emails endpoint"""
    # Log environment information
    logfire.info(f"Test environment - PYTEST_CURRENT_TEST: {os.environ.get('PYTEST_CURRENT_TEST')}")
    # logfire.info(f"All environment variables: {dict(os.environ)}")
    
    # Make the request to the endpoint
    response = client.get("/api/google/gmail/get_zillow_emails")
    
    # Check status code
    assert response.status_code == 200
    
    # Parse the response
    emails = response.json()
    
    # Verify the response structure
    assert type(emails) == list
    # assert len(emails) > 0 # Needed to comment this out now that we are using a local database that can be empty.
