"""The public booking API, under ``/api/showing-bookings/public/``. No sign-in.

This is the only part of the module the web server's ``/api`` proxy forwards
for a Sernia host. Every route answers 404 and saves nothing while the
setting ``public_booking_enabled`` is off (unset: off in production, on
elsewhere).

- ``GET /units/{unit}``: what the booking page shows for one unit: rent,
  showing length, the confirmations with their wording, the consent small
  print, the Turnstile site key, and the offers in order. No host names, no
  street address.
- ``POST /units/{unit}/bookings``: book or join a showing at one start.
- ``POST /bookings/lookup``: a booking by its cancel token, for the
  confirmation screen.
- ``POST /bookings/mobile``: correct the mobile on a booking by its cancel
  token, which texts the confirmation again, at most twice per booking.
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
from fastapi.responses import JSONResponse

from api.src.database.database import AsyncSessionFactory
from api.src.showing_bookings import bookings
from api.src.showing_bookings.providers import TextSender
from api.src.showing_bookings.settings import (
    current_environment,
    public_booking_on,
    settings_for_delivery,
)

PREFIX = "/showing-bookings/public"
_NO_STORE = {"Cache-Control": "no-store"}


async def public_booking_switch() -> None:
    """404, as for a path that does not exist, while public booking is off."""
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


@public_router.post("/bookings/lookup", response_model=bookings.BookingView)
async def lookup(request: Request):
    try:
        body = bookings.parse_body(bookings.TokenRequest, await _body(request))
        view = await bookings.booking_by_token(body.cancel_token)
    except bookings.Refused as refused:
        return _refused(refused)
    return JSONResponse(view.model_dump(mode="json"), headers=_NO_STORE)


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


@public_router.post("/team-phone", response_model=bookings.TeamPhone)
async def team_phone(request: Request):
    try:
        body = bookings.parse_body(bookings.TurnstileRequest, await _body(request))
        phone = await bookings.team_phone_after_check(body, _facts(request))
    except bookings.Refused as refused:
        return _refused(refused)
    return JSONResponse(phone.model_dump(mode="json"), headers=_NO_STORE)
