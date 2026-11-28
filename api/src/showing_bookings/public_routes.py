"""The public booking API, under ``/api/showing-bookings/public/``. No sign-in.

This is the only part of the module the web server's ``/api`` proxy forwards
for a Sernia host. Every route answers 404 and saves nothing while the
setting ``public_booking_enabled`` is off (unset: off in production, on
elsewhere), except the read, the calendar file and the cancel by token, so the
links in a text or an email keep working after booking is closed.

- ``GET /units``: the units on offer for the list at ``/showings``: address,
  rent, lease start, and whether each has an open time now (``public_units.py``).
- ``GET /call-line``: only the Turnstile site key, for the call line on a page
  with no unit (the Sernia home page). Reads no listings and no calendars.
- ``GET /units/{unit}``: what the booking page shows for one unit: address,
  rent, showing length, the confirmations with their wording, the consent
  small print, the Turnstile site key, and the offers in order. No host names.
- ``POST /units/{unit}/bookings``: book or join a showing at one start.
- ``POST /units/{unit}/lead-booking``: ``{lead_token}``: the place booked for
  the token's thread at this unit, and whether the lead's details are in
  (``lead_details.py``); 404 ``no_booking`` when there is none.
- ``POST /units/{unit}/lead-booking/unit``: ``{lead_token}``: what the booking
  page shows for the unit, with no times, for a lead with such a place. The page
  asks it when the unit itself answers 404, which in live mode is every unit
  that is not live.
- ``POST /units/{unit}/lead-booking/details``: the booking form without a
  time, plus ``lead_token``: completes that place with the lead's details.
- ``POST /bookings/lookup``: a booking by its cancel token, for the
  confirmation screen.
- ``POST /bookings/calendar``: the same booking as an iCalendar file, for the
  confirmation email's "Apple or Outlook calendar" link, which the web app
  serves at ``/showings/calendar/<cancel token>.ics``. Only while it is booked
  and ahead; 409 ``not_booked`` or ``started`` otherwise.
- ``POST /bookings/mobile``: correct the mobile on a booking by its cancel
  token, which texts the confirmation again, at most twice per booking.
- ``POST /bookings/cancel``: cancel a booking by its cancel token, up to the
  showing's start. A second call answers the booking as cancelled and sends
  nothing more.
- ``POST /team-phone``: the team's number and extension, after Turnstile.

A refusal answers ``{"detail": {"code", "message", "fields"}}``, where
``fields`` maps a form field (``name``, ``mobile``, ``email``, ``start``,
``confirmations.pets``, ``turnstile``...) to the line shown beside it.

Bodies are read from the raw request rather than declared as parameters, so
request tracing does not record names, numbers or tokens; a cancel token
travels in the body rather than the path for the same reason.
"""

from datetime import UTC, datetime

import logfire
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, Response

from api.src.database.database import AsyncSessionFactory
from api.src.showing_bookings import bookings, cancelling, lead_details, public_units
from api.src.showing_bookings.providers import TextSender
from api.src.showing_bookings.settings import (
    current_environment,
    public_booking_on,
    settings_for_delivery,
)

PREFIX = "/showing-bookings/public"
_NO_STORE = {"Cache-Control": "no-store"}


async def public_booking_switch(request: Request) -> None:
    """404, as for a path that does not exist, while public booking is off.

    The read and the cancel by token answer either way: a person booked
    before booking was closed keeps a working cancel link. A token exists
    only for a booking once made, so this shows nothing while booking has
    never been on.
    """
    route = request.scope.get("route")
    if getattr(route, "endpoint", None) in _ANSWER_WHEN_OFF:
        return
    async with AsyncSessionFactory() as session:
        settings = await settings_for_delivery(session)
    if not public_booking_on(settings, current_environment()):
        raise HTTPException(404, "Not Found")


def clock() -> datetime:
    """The time a request is handled at. Tests replace it to move the clock."""
    return datetime.now(UTC)


def text_sender() -> TextSender | None:
    """None sends through Quo, behind the outbox's guard. Tests replace it with a stand-in."""
    return None


public_router = APIRouter(
    prefix=PREFIX,
    tags=["showing-bookings-public"],
    dependencies=[Depends(public_booking_switch)],
)


def _facts(request: Request) -> bookings.RequestFacts:
    headers = request.headers
    forwarded = headers.get("x-forwarded-for", "").split(",")[0].strip()
    ip = (
        headers.get("cf-connecting-ip", "").strip()
        or forwarded
        or (request.client.host if request.client else "")
    )
    return bookings.RequestFacts(
        ip=ip or None,
        user_agent=headers.get("user-agent") or None,
        signature_agent=headers.get("signature-agent") or None,
    )


async def _body(request: Request) -> object:
    try:
        return await request.json()
    except Exception:
        return None


def _refused(refused: bookings.Refused) -> JSONResponse:
    return JSONResponse({"detail": refused.detail()}, status_code=refused.status, headers=_NO_STORE)


@public_router.get("/units", response_model=public_units.UnitsOnOffer)
async def list_units(now: datetime = Depends(clock)):
    try:
        return await public_units.units_on_offer(now=now)
    except bookings.Refused as refused:
        return _refused(refused)


@public_router.get("/call-line", response_model=public_units.CallLine)
async def call_line():
    return public_units.call_line()


@public_router.get("/units/{unit}", response_model=bookings.UnitPage)
async def get_unit(unit: str, now: datetime = Depends(clock)):
    try:
        return await bookings.unit_page(unit, now=now)
    except bookings.Refused as refused:
        return _refused(refused)


@public_router.post(
    "/units/{unit}/bookings", status_code=201, response_model=bookings.BookingResult
)
async def book(
    unit: str,
    request: Request,
    now: datetime = Depends(clock),
    texts: TextSender | None = Depends(text_sender),
):
    try:
        body = bookings.parse_body(bookings.BookingRequest, await _body(request))
        result = await bookings.book(unit, body, _facts(request), now=now, texts=texts)
    except bookings.Refused as refused:
        logfire.info("public booking refused", code=refused.code, fields=sorted(refused.fields))
        return _refused(refused)
    return JSONResponse(result.model_dump(mode="json"), status_code=201, headers=_NO_STORE)


@public_router.post("/units/{unit}/lead-booking", response_model=lead_details.LeadPlaceView)
async def lead_booking(unit: str, request: Request, now: datetime = Depends(clock)):
    try:
        body = bookings.parse_body(lead_details.LeadTokenRequest, await _body(request))
        view = await lead_details.place(unit, body, now=now)
    except bookings.Refused as refused:
        return _refused(refused)
    return JSONResponse(view.model_dump(mode="json"), headers=_NO_STORE)


@public_router.post("/units/{unit}/lead-booking/unit", response_model=bookings.UnitPage)
async def lead_booking_unit(unit: str, request: Request, now: datetime = Depends(clock)):
    try:
        body = bookings.parse_body(lead_details.LeadTokenRequest, await _body(request))
        page = await lead_details.unit_for_place(unit, body, now=now)
    except bookings.Refused as refused:
        return _refused(refused)
    return JSONResponse(page.model_dump(mode="json"), headers=_NO_STORE)


@public_router.post(
    "/units/{unit}/lead-booking/details",
    status_code=201,
    response_model=bookings.BookingResult,
)
async def lead_booking_details(
    unit: str,
    request: Request,
    now: datetime = Depends(clock),
    texts: TextSender | None = Depends(text_sender),
):
    try:
        body = bookings.parse_body(lead_details.DetailsRequest, await _body(request))
        result = await lead_details.complete(unit, body, _facts(request), now=now, texts=texts)
    except bookings.Refused as refused:
        logfire.info("lead details refused", code=refused.code, fields=sorted(refused.fields))
        return _refused(refused)
    return JSONResponse(result.model_dump(mode="json"), status_code=201, headers=_NO_STORE)


@public_router.post("/bookings/lookup", response_model=bookings.BookingView)
async def lookup(request: Request, now: datetime = Depends(clock)):
    try:
        body = bookings.parse_body(bookings.TokenRequest, await _body(request))
        view = await bookings.booking_by_token(body.cancel_token, now=now)
    except bookings.Refused as refused:
        return _refused(refused)
    return JSONResponse(view.model_dump(mode="json"), headers=_NO_STORE)


@public_router.post("/bookings/calendar", response_class=Response)
async def calendar_file(request: Request, now: datetime = Depends(clock)):
    try:
        body = bookings.parse_body(bookings.TokenRequest, await _body(request))
        ics = await bookings.calendar_file(body.cancel_token, now=now)
    except bookings.Refused as refused:
        return _refused(refused)
    return Response(ics, media_type="text/calendar; charset=utf-8", headers=_NO_STORE)


@public_router.post("/bookings/mobile", response_model=bookings.BookingView)
async def correct_mobile(
    request: Request,
    now: datetime = Depends(clock),
    texts: TextSender | None = Depends(text_sender),
):
    try:
        body = bookings.parse_body(bookings.MobileCorrection, await _body(request))
        view = await bookings.correct_mobile(body, now=now, texts=texts)
    except bookings.Refused as refused:
        return _refused(refused)
    return JSONResponse(view.model_dump(mode="json"), headers=_NO_STORE)


@public_router.post("/bookings/cancel", response_model=bookings.BookingView)
async def cancel(
    request: Request,
    now: datetime = Depends(clock),
    texts: TextSender | None = Depends(text_sender),
):
    try:
        body = bookings.parse_body(bookings.TokenRequest, await _body(request))
        view = await cancelling.cancel_by_token(body.cancel_token, now=now, texts=texts)
    except bookings.Refused as refused:
        logfire.info("public cancellation refused", code=refused.code)
        return _refused(refused)
    return JSONResponse(view.model_dump(mode="json"), headers=_NO_STORE)


# Answer while public booking is off (``public_booking_switch``).
_ANSWER_WHEN_OFF = frozenset({lookup, calendar_file, cancel})


@public_router.post("/team-phone", response_model=bookings.TeamPhone)
async def team_phone(request: Request):
    try:
        body = bookings.parse_body(bookings.TurnstileRequest, await _body(request))
        phone = await bookings.team_phone_after_check(body, _facts(request))
    except bookings.Refused as refused:
        return _refused(refused)
    return JSONResponse(phone.model_dump(mode="json"), headers=_NO_STORE)
