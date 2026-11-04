"""Check configured Google credentials locally; optionally check delegation.

Run from the repository root: python -m scripts.check_google_credentials
Set TEST_DELEGATION_EMAIL to also construct delegated credentials.
"""

import os

from dotenv import load_dotenv
from fastapi import HTTPException

from api.src.google.common.service_account_auth import (
    get_delegated_credentials,
    get_service_credentials,
)


def main():

    # Load environment variables
    load_dotenv()

    try:
        # Test service account credentials
        print("Testing service account credentials...")
        credentials = get_service_credentials()
        print("✓ Successfully loaded service account credentials")
        print(f"✓ Service account email: {credentials.service_account_email}")
        print(f"✓ Scopes: {credentials.scopes}")

        # Test delegation if email provided
        test_email = os.getenv("TEST_DELEGATION_EMAIL")
        if test_email:
            print(f"\nTesting delegation to {test_email}...")
            get_delegated_credentials(test_email)
            print(f"✓ Successfully created delegated credentials for {test_email}")

    except Exception as e:
        print(f"❌ Error: {str(e)}")
        if isinstance(e, HTTPException):
            print(f"HTTP Status: {e.status_code}")
            print(f"Detail: {e.detail}")
        raise


if __name__ == "__main__":
    main()
