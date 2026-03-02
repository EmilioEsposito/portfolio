from __future__ import annotations

import datetime
from enum import StrEnum
from pprint import pprint

import logfire
import pytz
import pytest
from fastapi import HTTPException
from googleapiclient.discovery import build
from pydantic import BaseModel, EmailStr, Field, field_serializer

from api.src.google.common.service_account_auth import get_delegated_credentials

CALENDAR_SCOPES = [
    "https://www.googleapis.com/auth/calendar",
]


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class ReminderMethod(StrEnum):
    EMAIL = "email"
    POPUP = "popup"


class CalendarReminder(BaseModel):
    """A single reminder override for a calendar event."""

    method: ReminderMethod = Field(
        default=ReminderMethod.POPUP, description="Reminder delivery method."
    )
    minutes: int = Field(description="Minutes before the event to trigger the reminder.")


DEFAULT_REMINDERS = [
    CalendarReminder(method=ReminderMethod.EMAIL, minutes=24 * 60),
    CalendarReminder(method=ReminderMethod.POPUP, minutes=60),
]


class CalendarEventInput(BaseModel):
    """Structured input for creating a Google Calendar event."""

    summary: str = Field(description="Event title.")
    start: datetime.datetime = Field(
        description="Start time in ISO 8601 format (e.g. 2025-06-15T10:00:00-04:00)."
    )
    end: datetime.datetime = Field(
        description="End time in ISO 8601 format."
    )
    description: str | None = Field(
        default=None, description="Event description."
    )
    attendees: list[EmailStr] | None = Field(
        default=None, description="Attendee email addresses."
    )
    location: str | None = Field(
        default=None, description="Event location."
    )
    reminders: list[CalendarReminder] | None = Field(
        default=None,
        description="Custom reminders. Defaults to email 1 day before + popup 1 hour before.",
    )

    @field_serializer("start", "end")
    def serialize_datetime(self, dt: datetime.datetime) -> dict:
        return {"dateTime": dt.isoformat()}

    @field_serializer("attendees")
    def serialize_attendees(self, attendees: list[EmailStr] | None) -> list[dict] | None:
        if attendees is None:
            return None
        return [{"email": email} for email in attendees]

    @field_serializer("reminders")
    def serialize_reminders(self, reminders: list[CalendarReminder] | None) -> list[dict]:
        return [r.model_dump(mode="json") for r in (reminders or DEFAULT_REMINDERS)]

    def to_api_body(self, organizer_email: str | None = None) -> dict:
        """Serialize to a Google Calendar API event body dict."""
        body = self.model_dump(exclude_none=True)
        # reminders is excluded by exclude_none when the raw value is None,
        # so we always set it from the serialized form.
        body["reminders"] = {
            "useDefault": False,
            "overrides": self.serialize_reminders(self.reminders),
        }
        if organizer_email:
            body["organizer"] = {"email": organizer_email}
        return body


# ---------------------------------------------------------------------------
# Service helpers
# ---------------------------------------------------------------------------


async def get_calendar_service(user_email: str):
    """Create and return an authorized Calendar API service instance."""
    try:
        credentials = get_delegated_credentials(
            user_email=user_email, scopes=CALENDAR_SCOPES
        )
        service = build("calendar", "v3", credentials=credentials)
        return service
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


async def get_calendar_events(service, calendar_id: str = "primary"):
    """Retrieve events from the specified calendar."""
    try:
        events = service.events().list(calendarId=calendar_id).execute()
        return events
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


async def create_calendar_event(
    service,
    event: CalendarEventInput,
    organizer_email: str | None = None,
    overwrite: bool = False,
):
    """Create a new calendar event with idempotency check.

    Args:
        service: Authorized Calendar API service instance.
        event: Typed event input.
        organizer_email: Optional organizer email to set on the event.
        overwrite: Delete and recreate if a matching event already exists.
    """
    body = event.to_api_body(organizer_email=organizer_email)

    try:
        existing = (
            service.events()
            .list(
                calendarId="primary",
                q=event.summary,
                timeMin=event.start.isoformat(),
                timeMax=event.end.isoformat(),
            )
            .execute()
        )

        if len(existing["items"]) > 0:
            if overwrite:
                logfire.info(
                    f"Event already exists, but overwrite is True, deleting it: {existing['items'][0]['id']}"
                )
                await delete_calendar_event(service, existing["items"][0]["id"])
            else:
                logfire.info(f"Event already exists: {existing['items'][0]['id']}")
                return existing["items"][0]
        else:
            logfire.info(f"Event does not exist, creating event: {event.summary}")

        created = (
            service.events()
            .insert(calendarId="primary", body=body, sendUpdates="all")
            .execute()
        )
        return created

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


async def delete_calendar_event(service, event_id: str):
    """
    Deletes a calendar event.

    Args:
        service: The authorized Calendar API service instance.
        event_id: The ID of the event to delete.
    Returns:
        A boolean indicating whether the deletion was successful.
    """
    try:
        service.events().delete(calendarId="primary", eventId=event_id).execute()
        return True
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ------------------------------------------------------------------------------------------------
# ------------------------------ TESTS -----------------------------------------------------------
# ------------------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_query_calendar_events():
    service = await get_calendar_service(user_email="emilio@serniacapital.com")
    now = datetime.datetime.now(tz=pytz.timezone("US/Eastern"))
    now_str = now.isoformat()
    print("Getting the upcoming 3 events")
    events_result = (
        service.events()
        .list(
            calendarId="primary",
            timeMin=now_str,
            maxResults=3,
            singleEvents=True,
            orderBy="startTime",
        )
        .execute()
    )
    events = events_result.get("items", [])

    assert len(events) == 3


@pytest.mark.asyncio
async def test_create_calendar_event():
    service = await get_calendar_service(user_email="all@serniacapital.com")
    et_tz = pytz.timezone("US/Eastern")
    start_time = datetime.datetime(year=2026, month=3, day=29, hour=12).astimezone(
        et_tz
    )
    end_time = start_time + datetime.timedelta(hours=1)

    event = CalendarEventInput(
        summary="Test Event3",
        description="This is a test event",
        start=start_time,
        end=end_time,
        attendees=["emilio@serniacapital.com"],
    )
    new_event = await create_calendar_event(
        service, event, organizer_email="all@serniacapital.com", overwrite=True
    )
    pprint(new_event)
    assert new_event["summary"] == event.summary
