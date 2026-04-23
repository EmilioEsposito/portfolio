"""
Resolve the acting user identity for MCP tool calls.

POC: single user. The bearer token proves "some authorized caller"; there is
no multi-tenant story yet. When MCP auth moves to Clerk JWTs, this will extract
the caller's email from the JWT claims.
"""
import os

from api.src.sernia_ai.config import GOOGLE_DELEGATION_EMAIL


def resolve_user_email_for_request() -> str:
    """Return the Google Workspace email to impersonate for API calls."""
    return os.environ.get("SERNIA_MCP_DEFAULT_USER", GOOGLE_DELEGATION_EMAIL)
