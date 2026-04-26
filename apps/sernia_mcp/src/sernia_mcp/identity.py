"""Resolve the acting user identity for tool calls.

POC: single user. The Clerk OAuth token proves "some authorized caller"; there
is no multi-tenant story yet. When the JWT-claims path is wired up, this
will switch to extracting the caller's email from the Clerk token via
``get_access_token()`` instead of falling back to the env default.
"""
from sernia_mcp.config import GOOGLE_DELEGATION_EMAIL


def resolve_user_email_for_request() -> str:
    """Return the Google Workspace email to impersonate for API calls."""
    return GOOGLE_DELEGATION_EMAIL
