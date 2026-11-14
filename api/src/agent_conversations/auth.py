"""Verified Sernia team identity shared by operational routers."""

from clerk_backend_api import User
from fastapi import Request

from api.src.utils.clerk import verify_serniacapital_user


async def _sernia_gate(request: Request) -> None:
    """Router-level dependency: verify @serniacapital.com and stash user."""
    user = await verify_serniacapital_user(request)
    request.state.sernia_user = user


async def _get_sernia_user(request: Request) -> User:
    """Per-endpoint dependency: retrieve user set by the router gate."""
    return request.state.sernia_user


SerniaUser = User  # plain type alias — resolved by _get_sernia_user dependency


def _display_name(user: User) -> str:
    return f"{user.first_name or ''} {user.last_name or ''}".strip() or "User"


def _sernia_email(user: User) -> str:
    """Extract the @serniacapital.com email from a Clerk User."""
    for ea in user.email_addresses or []:
        addr = ea.email_address if hasattr(ea, "email_address") else str(ea)
        if addr.endswith("@serniacapital.com"):
            return addr
    # Fallback: construct from first name (all Sernia users have one)
    name = (user.first_name or "unknown").lower()
    return f"{name}@serniacapital.com"
