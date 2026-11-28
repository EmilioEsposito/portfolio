"""What the Sernia MCP service may do to showings, under ``/api/showing-bookings/mcp``.

The MCP service (``apps/sernia_mcp``) has no database. It holds a signed-in
team member's Clerk OAuth token and calls here on their behalf, for its
``showings_read``, ``showing_book`` and ``showing_cancel`` tools, which match
the Sernia agent's tools of the same names.

Only that token is accepted: an OAuth access token Clerk issued to the MCP's
own OAuth client, for a user with a verified @serniacapital.com email
(``verify_serniacapital_oauth_user``). A browser session is refused here, and
the MCP's token is refused on every route in ``routes.py``, so this file is
the whole of what the MCP can reach in this module:

- ``GET /showings``: open times, booking links and booked showings
  (``team_view.read``), the same document the agent's read answers with.
- ``POST /showings/bookings/preview`` and ``POST /showings/cancellations/preview``:
  what a booking or a cancellation would do and who would be told, the
  paragraph from ``effects.py``. They change nothing. The MCP shows the
  paragraph on its Approve card.
- ``POST /showings/bookings`` and ``POST /showings/cancellations``: do it,
  given the paragraph the person approved. The paragraph is worked out again
  first; if it no longer matches (someone else took the time, a person left),
  nothing is done and the answer is 409 ``effect_changed`` with the paragraph
  as it now reads. Then ``team_actions.book_for_caller``,
  ``cancelling.cancel_showing`` or ``cancelling.remove_attendee`` runs, as
  from the Booking settings tab, with the paragraph as ``expected_effect``: it
  is worked out once more under the action's own locks, so a join or a
  calendar change that lands after the first comparison still changes nothing
  (``effects.EffectChanged``, answered the same way; its ``effect`` is None
  when the action can no longer be done at all).

A refusal answers ``{"detail": {"code", "message", "fields"}}``. Another host
and who came are not here: neither the agent nor the MCP has a tool for them,
and a person does them on the tab.

This API does not check that a person clicked Approve. It checks who is
calling and that the approved paragraph still holds; the card is the MCP's.
"""

import os
from datetime import datetime
from typing import Annotated

import logfire
from clerk_backend_api import User
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, StrictBool

from api.src.rental_listings.routes import MCP_CLIENT_ID_ENV
from api.src.showing_bookings import cancelling, effects, routes, team_actions, team_view
from api.src.showing_bookings.bookings import Refused
from api.src.showing_bookings.providers import TextSender
from api.src.utils.clerk import verify_serniacapital_oauth_user

# The refusal when the paragraph a person approved no longer describes what would happen.
EFFECT_CHANGED = effects.EFFECT_CHANGED


async def mcp_user(request: Request) -> User:
    """The team member the MCP service is calling for. Refuses any other caller."""
    client_id = os.environ.get(MCP_CLIENT_ID_ENV, "").strip()
    return await verify_serniacapital_oauth_user(request, client_id=client_id)


mcp_router = APIRouter(
    prefix="/showing-bookings/mcp",
    tags=["showing-bookings"],
    dependencies=[Depends(mcp_user)],
)

McpUser = Annotated[User, Depends(mcp_user)]
Now = Annotated[datetime, Depends(routes.clock)]
Texts = Annotated[TextSender | None, Depends(routes.text_sender)]


def _taken_by(user: User) -> str:
    """Who took a booking, for its consent record: the person and the route."""
    return f"{routes._sernia_email(user)} (via Sernia MCP)"


def _refusal(refused: Refused) -> HTTPException:
    return HTTPException(refused.status, refused.detail())


def _changed(changed: effects.EffectChanged) -> HTTPException:
    """409 ``effect_changed``, with the paragraph as it reads now (None: cannot be done)."""
    return HTTPException(409, {**changed.detail(), "effect": changed.effect})


class Effect(BaseModel):
    """What an action would do and who would be told, for a person to approve."""

    effect: str


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------


@mcp_router.get("/showings")
async def read_showings(
    user: McpUser,
    now: Now,
    unit: Annotated[str | None, Query()] = None,
    zillow_thread_id: Annotated[str | None, Query()] = None,
) -> dict:
    try:
        return await team_view.read(now=now, unit=unit, zillow_thread_id=zillow_thread_id)
    except Refused as refused:
        raise _refusal(refused) from None


# ---------------------------------------------------------------------------
# Booking
# ---------------------------------------------------------------------------


class BookingRequest(BaseModel):
    """The MCP's ``showing_book`` arguments."""

    model_config = ConfigDict(extra="forbid")

    unit: str = ""
    start: str = ""
    name: str = ""
    mobile: str = ""
    email: str = ""
    texts_accepted: StrictBool = False
    zillow_thread_id: str | None = None

    def team_request(self) -> team_actions.TeamBookingRequest:
        return team_actions.TeamBookingRequest(
            unit=self.unit,
            start=self.start,
            name=self.name,
            mobile=self.mobile,
            email=self.email,
            texts=self.texts_accepted,
        )

    def thread(self) -> str | None:
        return (self.zillow_thread_id or "").strip() or None


class Booking(BookingRequest):
    # The paragraph the person approved.
    effect: str


class Booked(BaseModel):
    result: str
    booking: team_actions.TeamBooking


async def _booking_effect(body: BookingRequest, now: datetime) -> effects.BookingPreview:
    try:
        return await effects.preview_booking(
            body.team_request(), now=now, zillow_thread_id=body.thread()
        )
    except Refused as refused:
        raise _refusal(refused) from None


@mcp_router.post("/showings/bookings/preview", response_model=Effect)
async def preview_booking(body: BookingRequest, user: McpUser, now: Now):
    return Effect(effect=(await _booking_effect(body, now)).sentence)


@mcp_router.post("/showings/bookings", response_model=Booked, status_code=201)
async def book(body: Booking, user: McpUser, now: Now, texts: Texts):
    preview = await _booking_effect(body, now)
    if preview.sentence != body.effect:
        raise _changed(effects.EffectChanged(preview.sentence))
    try:
        booked = await team_actions.book_for_caller(
            body.team_request(),
            taken_by=_taken_by(user),
            now=now,
            texts=texts,
            zillow_thread_id=body.thread(),
            expected_effect=body.effect,
        )
    except effects.EffectChanged as changed:
        raise _changed(changed) from None
    except Refused as refused:
        raise _refusal(refused) from None
    logfire.info(
        "showing bookings: booking added through the Sernia MCP",
        user_id=user.id,
        showing_id=booked.showing_id,
        attendee_id=booked.attendee_id,
        texts=booked.texts_accepted,
    )
    queued = ", ".join(f"{notice.kind} ({notice.status})" for notice in booked.notices)
    confirmation = (
        f" The confirmation text was {booked.confirmation}." if booked.confirmation else ""
    )
    kind = "joined a showing already booked" if booked.kind == "join" else "a new showing"
    result = (
        f"Booked {preview.name} at {preview.unit} on "
        f"{effects.when_text(preview.start, preview.timezone)} ({kind}). "
        f"showing_id {booked.showing_id}, attendee_id {booked.attendee_id}.{confirmation} "
        f"Queued: {queued or 'nothing'}."
    )
    return Booked(result=result, booking=booked)


# ---------------------------------------------------------------------------
# Cancelling
# ---------------------------------------------------------------------------


class CancellationRequest(BaseModel):
    """The MCP's ``showing_cancel`` arguments. No attendee cancels the whole showing."""

    model_config = ConfigDict(extra="forbid")

    showing_id: int
    attendee_id: int | None = None


class Cancellation(CancellationRequest):
    # The paragraph the person approved.
    effect: str


class Cancelled(BaseModel):
    result: str
    cancellation: cancelling.TeamCancel


async def _cancellation_effect(body: CancellationRequest, now: datetime) -> str:
    try:
        return await effects.preview_cancellation(body.showing_id, body.attendee_id, now=now)
    except Refused as refused:
        raise _refusal(refused) from None


@mcp_router.post("/showings/cancellations/preview", response_model=Effect)
async def preview_cancellation(body: CancellationRequest, user: McpUser, now: Now):
    return Effect(effect=await _cancellation_effect(body, now))


@mcp_router.post("/showings/cancellations", response_model=Cancelled)
async def cancel(body: Cancellation, user: McpUser, now: Now, texts: Texts):
    sentence = await _cancellation_effect(body, now)
    if sentence != body.effect:
        raise _changed(effects.EffectChanged(sentence))
    try:
        if body.attendee_id is None:
            done = await cancelling.cancel_showing(
                body.showing_id, now=now, texts=texts, expected_effect=body.effect
            )
        else:
            done = await cancelling.remove_attendee(
                body.showing_id, body.attendee_id, now=now, texts=texts, expected_effect=body.effect
            )
    except effects.EffectChanged as changed:
        raise _changed(changed) from None
    except Refused as refused:
        raise _refusal(refused) from None
    except cancelling.NotFound:
        raise _refusal(
            Refused(404, "unknown_showing", "That showing or person no longer exists.")
        ) from None
    except cancelling.Started:
        raise _refusal(
            Refused(409, "started", "This showing has started; it can no longer be cancelled.")
        ) from None
    logfire.info(
        "showing bookings: cancellation through the Sernia MCP",
        user_id=user.id,
        showing_id=body.showing_id,
        attendee_id=body.attendee_id,
        attendees_cancelled=done.attendees_cancelled,
    )
    if not done.attendees_cancelled:
        result = "Nothing was left to cancel: someone cancelled it meanwhile."
    else:
        people = "person" if done.attendees_cancelled == 1 else "people"
        texts_word = "text" if done.texts_queued == 1 else "texts"
        result = (
            f"Cancelled. Showing {body.showing_id} is now {done.showing_status}; "
            f"{done.attendees_cancelled} {people} cancelled, {done.texts_queued} cancellation "
            f"{texts_word} queued."
        )
    return Cancelled(result=result, cancellation=done)
