import pytest
from api_src.tests.conftest import *
import os
import logging

@pytest.mark.asyncio
async def test_get_zillow_emails(client):
    """Test the /gmail/get_zillow_emails endpoint"""
    # Log environment information
    logging.info(f"Test environment - PYTEST_CURRENT_TEST: {os.environ.get('PYTEST_CURRENT_TEST')}")
    logging.info(f"All environment variables: {dict(os.environ)}")
    
    # Make the request to the endpoint
    response = client.get("/api/google/gmail/get_zillow_emails")
    
    # Check status code
    assert response.status_code == 200
    
    # Parse the response
    emails = response.json()
    
    # Verify the response structure
    assert type(emails) == list and len(emails) > 0
