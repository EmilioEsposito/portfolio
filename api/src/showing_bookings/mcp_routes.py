"""What the Sernia MCP service may do to showings, under ``/api/showing-bookings/mcp``.

The MCP service (``apps/sernia_mcp``) has no database. It holds a signed-in
team member's Clerk OAuth token and calls here on their behalf, so a tool call
in their chat does what the same person could do on the Booking settings tab.

Only that token is accepted: an OAuth access token Clerk issued to the MCP's
own OAuth client, for a user with a verified @serniacapital.com email
(``verify_serniacapital_oauth_user``). A browser session is refused here, and
the MCP's token is refused on every route in ``routes.py``. So this file is
the whole list of what the MCP can reach in this module:

- ``GET /showings``: the unit's open times, how each agent's calendar was
  read, and the showings booked with the people in each.
- ``POST /showings/bookings``: book someone who called into an open time.
- ``POST /showings/{id}/cancel``: cancel a showing and everyone in it.
- ``POST /showings/{id}/attendees/{attendee_id}/remove``: remove one person.
- ``POST /showings/{id}/host``: give the showing to another agent who is free.
- ``PUT /showings/{id}/attendees/{attendee_id}/attendance``: mark who came.

Each one calls the team's own route function in ``routes.py`` and answers what
it answers, so the texts, invites, calendar changes and refusals are the same
whichever way the action arrived. Settings, showing agents, calendar
connections and notices are not here: a person changes those on the tab.

The MCP asks a person to approve a booking, a cancellation and a removal on a
card before it calls here (``apps/sernia_mcp/src/sernia_mcp/tools/showing_bookings.py``).
This API does not check for that approval; it checks who is calling.
"""

import os
from datetime import datetime
from typing import Annotated

import logfire
from clerk_backend_api import User
from fastapi import APIRouter, Depends, Request

from api.src.database.database import DBSession
from api.src.rental_listings.routes import MCP_CLIENT_ID_ENV
from api.src.showing_bookings import cancelling, routes, team_actions
from api.src.showing_bookings.providers import TextSender
from api.src.utils.clerk import verify_serniacapital_oauth_user


async def mcp_user(request: Request) -> User:
    """The team member the MCP service is calling for. Refuses any other caller."""
    client_id = os.environ.get(MCP_CLIENT_ID_ENV, "").strip()
    user = await verify_serniacapital_oauth_user(request, client_id=client_id)
    logfire.info(
        "showing bookings: call through the Sernia MCP",
        user_id=user.id,
        method=request.method,
        path=request.url.path,
    )
    return user


mcp_router = APIRouter(
    prefix="/showing-bookings/mcp",
    tags=["showing-bookings"],
    dependencies=[Depends(mcp_user)],
)

McpUser = Annotated[User, Depends(mcp_user)]
Now = Annotated[datetime, Depends(routes.clock)]
Texts = Annotated[TextSender | None, Depends(routes.text_sender)]


@mcp_router.get("/showings", response_model=routes.ShowingsView)
async def list_showings(session: DBSession, now: Now):
    return await routes.list_showings(session, now)


@mcp_router.post("/showings/bookings", response_model=team_actions.TeamBooking, status_code=201)
async def book_for_caller(
    body: team_actions.TeamBookingRequest, user: McpUser, now: Now, texts: Texts
):
    return await routes.book_for_caller(body, user, now, texts)


@mcp_router.post("/showings/{showing_id}/cancel", response_model=cancelling.TeamCancel)
async def cancel_showing(showing_id: int, user: McpUser, now: Now, texts: Texts):
    return await routes.cancel_showing(showing_id, user, now, texts)


@mcp_router.post(
    "/showings/{showing_id}/attendees/{attendee_id}/remove", response_model=cancelling.TeamCancel
)
async def remove_attendee(showing_id: int, attendee_id: int, user: McpUser, now: Now, texts: Texts):
    return await routes.remove_attendee(showing_id, attendee_id, user, now, texts)


@mcp_router.post("/showings/{showing_id}/host", response_model=team_actions.HostChange)
async def change_host(
    showing_id: int,
    body: team_actions.HostChangeRequest,
    user: McpUser,
    now: Now,
    texts: Texts,
):
    return await routes.change_host(showing_id, body, user, now, texts)


@mcp_router.put(
    "/showings/{showing_id}/attendees/{attendee_id}/attendance",
    response_model=team_actions.AttendanceMark,
)
async def mark_attendance(
    showing_id: int,
    attendee_id: int,
    body: team_actions.AttendanceRequest,
    user: McpUser,
    now: Now,
):
    return await routes.mark_attendance(showing_id, attendee_id, body, user, now)
