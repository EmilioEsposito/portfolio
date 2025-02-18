from typing import Annotated
from fastapi import APIRouter, Depends
from clerk_backend_api import Session, User, RequestState

from api_src.utils.clerk import is_signed_in, get_auth_session, get_auth_user

router = APIRouter(prefix="/examples", tags=["examples"])

# @router.get("/protected")
# async def protected_route(
#     session: Annotated[Session, Depends(get_auth_session)],
#     user: Annotated[User, Depends(get_auth_user)]
# ):
#     """
#     Example protected endpoint that requires authentication.
#     Returns user information from Clerk.
#     """
#     return {
#         "message": "You are authenticated!",
#         "user_id": user.id,
#         "email": user.email_addresses[0].email_address if user.email_addresses else None,
#         "first_name": user.first_name,
#         "last_name": user.last_name,
#         "session_id": session.id
#     }



@router.get("/protected_simple")
async def protected_route_simple(
    dependencies=[Depends(is_signed_in)]
):
    """
    Example protected endpoint that requires authentication.
    Returns user information from Clerk.
    """

    return "You are authenticated!"



@router.get("/protected_get_session")
async def protected_route_get_session(
    session: Annotated[Session, Depends(get_auth_session)]
):
    """
    Example protected endpoint that requires authentication.
    Returns user information from Clerk.
    """
    return session

@router.get("/protected_get_user")
async def protected_route_get_user(
    user: Annotated[User, Depends(get_auth_user)]
):
    """
    Example protected endpoint that requires authentication.
    Returns user information from Clerk.
    """
    data = {
        "user": user,
        "dummy_data": "dummy"
    }
    return data
