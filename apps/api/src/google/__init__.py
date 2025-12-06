"""
Google package initialization.
Exposes core functionality from various Google services.
"""

from apps.api.src.google.gmail import send_email
from apps.api.src.google.sheets import get_sheet_as_json
from apps.api.src.google.common.service_account_auth import (
    get_service_credentials,
    get_delegated_credentials
)

__all__ = [
    'send_email',
    'get_sheet_as_json',
    'get_service_credentials',
    'get_delegated_credentials'
] 