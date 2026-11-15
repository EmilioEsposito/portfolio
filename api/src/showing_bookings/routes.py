"""The team's API for showing bookings, under ``/api/showing-bookings``.

Signed-in Sernia users only, on every route.

- ``GET /settings`` and ``PATCH /settings``: the send mode, live units and the
  test-send switch. A save sends ``based_on``, the settings as the caller last
  read them; if they have changed since, nothing is saved and the answer is 409.
- ``GET /notices``: notices newest first, with their wording, status and mode.
- ``POST /notices/send-pending``: run the sending step once, the same one the
  scheduled job will run, and answer what it did.
"""

from datetime import UTC, datetime

import logfire
from clerk_backend_api import User
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert

from api.src.database.app_settings import AppSetting
from api.src.database.database import DBSession
from api.src.rental_listings.models import RentalListingRecord
from api.src.showing_bookings import outbox
from api.src.showing_bookings.guard import PRODUCTION
from api.src.showing_bookings.models import Showing, ShowingNotice
from api.src.showing_bookings.schema import DrainReport, Notice
from api.src.showing_bookings.settings import (
    SETTINGS_KEY,
    ShowingBookingSettings,
    current_environment,
    read_settings,
    read_test_recipients,
)
from api.src.utils.clerk import verify_serniacapital_user

router = APIRouter(
    prefix="/showing-bookings",
    tags=["showing-bookings"],
    dependencies=[Depends(verify_serniacapital_user)],
)


class SettingsView(BaseModel):
    settings: ShowingBookingSettings
    # Railway's environment name, or "local".
    environment: str
    is_production: bool
    # Who test sends can reach here, shortened: "•••-0142", "p…@example.com".
    test_recipients: list[str]


class SettingsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    settings: ShowingBookingSettings
    based_on: ShowingBookingSettings


def _shortened(address: str) -> str:
    if "@" in address:
        name, domain = address.split("@", 1)
        return f"{name[:1]}…@{domain}"
    return f"•••-{address[-4:]}"


def _wording(notice: ShowingNotice) -> str:
    payload = notice.payload
    if notice.channel == "sms":
        return payload.get("body", "")
    if notice.channel == "email":
        return f"Subject: {payload.get('subject', '')}\n\n{payload.get('body', '')}"
    action = {"create": "New event", "update": "Changed event", "cancel": "Cancelled event"}
    lines = [f"{action.get(payload.get('operation'), 'Event')}: {payload.get('summary', '')}"]
    if payload.get("location"):
        lines.append(payload["location"])
    if payload.get("description"):
        lines.append(payload["description"])
    if payload.get("attendees"):
        lines.append("Guests: " + ", ".join(payload["attendees"]))
    return "\n".join(lines)


@router.get("/settings", response_model=SettingsView)
async def get_settings(session: DBSession):
    try:
        settings = await read_settings(session)
    except Exception:
        logfire.exception("showing bookings: settings could not be loaded")
        raise HTTPException(503, "Settings could not be loaded. Try again.") from None
    environment = current_environment()
    return SettingsView(
        settings=settings,
        environment=environment or "local",
        is_production=environment == PRODUCTION,
        test_recipients=sorted(_shortened(address) for address in read_test_recipients()),
    )


@router.patch("/settings", response_model=ShowingBookingSettings)
async def update_settings(
    body: SettingsUpdate, session: DBSession, user: User = Depends(verify_serniacapital_user)
):
    new = body.settings
    if new.live_units:
        # Save each live unit as its listing spells it, and refuse a unit with no listing.
        listed = {
            unit.casefold(): unit
            for unit in await session.scalars(
                select(RentalListingRecord.unit).where(
                    func.lower(RentalListingRecord.unit).in_(
                        [unit.casefold() for unit in new.live_units]
                    )
                )
            )
        }
        unknown = [unit for unit in new.live_units if unit.casefold() not in listed]
        if unknown:
            raise HTTPException(422, f"No rental listing for: {', '.join(unknown)}.")
        new = new.model_copy(
            update={"live_units": [listed[unit.casefold()] for unit in new.live_units]}
        )

    # Create the row if absent, then lock it, so two first saves cannot both win.
    current = await read_settings(session)
    await session.execute(
        insert(AppSetting)
        .values(key=SETTINGS_KEY, value=current.model_dump())
        .on_conflict_do_nothing(index_elements=["key"])
    )
    row = await session.scalar(
        select(AppSetting).where(AppSetting.key == SETTINGS_KEY).with_for_update()
    )
    if ShowingBookingSettings.model_validate(row.value) != body.based_on:
        raise HTTPException(409, "Settings changed elsewhere. Reload before saving.")
    row.value = new.model_dump()
    await session.commit()
    logfire.info("showing bookings: settings updated", user_id=user.id, **new.model_dump())
    return new


@router.get("/notices", response_model=list[Notice])
async def list_notices(session: DBSession, limit: int = Query(100, ge=1, le=500)):
    rows = await session.execute(
        select(ShowingNotice, Showing.unit, Showing.starts_at)
        .join(Showing, Showing.id == ShowingNotice.showing_id)
        .order_by(ShowingNotice.id.desc())
        .limit(limit)
    )
    return [
        Notice(
            id=notice.id,
            kind=notice.kind,
            channel=notice.channel,
            showing_id=notice.showing_id,
            attendee_id=notice.attendee_id,
            unit=unit,
            showing_starts_at=starts_at,
            recipient=notice.recipient,
            recipient_key=notice.recipient_key,
            wording=_wording(notice),
            payload=notice.payload,
            status=notice.status,
            mode=notice.mode,
            guard_reason=notice.guard_reason,
            not_before=notice.not_before,
            attempts=notice.attempts,
            provider_status=notice.provider_status,
            last_error=notice.last_error,
            created_at=notice.created_at,
            delivered_at=notice.delivered_at,
        )
        for notice, unit, starts_at in rows
    ]


@router.post("/notices/send-pending", response_model=DrainReport)
async def send_pending(user: User = Depends(verify_serniacapital_user)):
    report = await outbox.drain(now=datetime.now(UTC))
    logfire.info(
        "showing bookings: send pending run by hand", user_id=user.id, **report.model_dump()
    )
    return report
