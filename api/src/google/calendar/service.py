from api.src.google.common.service_account_auth import get_delegated_credentials
from googleapiclient.discovery import build
from fastapi import HTTPException
import datetime
from pprint import pprint
import pytz
import logging
import pytest

CALENDAR_SCOPES = [
    "https://www.googleapis.com/auth/calendar",
    # "https://www.googleapis.com/auth/calendar.readonly",
    # "https://www.googleapis.com/auth/calendar.events",
    # "https://www.googleapis.com/auth/calendar.events.readonly",
]

logger = logging.getLogger(__name__)


async def get_calendar_service(
    user_email: str,
):
    """
    Creates and returns an authorized Calendar API service instance.
    Args:
        user_email: The email address of the user for whom the service is being authorized.
    Returns:
        A Calendar API service instance.
    """
    try:
        credentials = get_delegated_credentials(
            user_email=user_email, scopes=CALENDAR_SCOPES
        )
        service = build("calendar", "v3", credentials=credentials)
        return service
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


async def get_calendar_events(service, calendar_id: str = "primary"):
    """
    Retrieves events from the specified calendar.
    Args:
        service: The authorized Calendar API service instance.
        calendar_id: The ID of the calendar to retrieve events from.
    Returns:
        A list of events from the specified calendar.
    """
    try:
        events = service.events().list(calendarId=calendar_id).execute()
        return events
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


async def create_calendar_event(service, event: dict, overwrite: bool = False):
    """
    Creates a new calendar event.
    Args:
        service: The authorized Calendar API service instance.
        event: The event to create.
        overwrite: Whether to overwrite the event if it already exists.
    """
    try:
        # first, check if the event already exists
        # query events with same summary and start time and end time
        events = (
            service.events()
            .list(
                calendarId="primary",
                q=event["summary"],
                timeMin=event["start"]["dateTime"],
                timeMax=event["end"]["dateTime"],
            )
            .execute()
        )

        if len(events["items"]) > 0:
            if overwrite:
                logger.info(
                    f"Event already exists, but overwrite is True, deleting it: {events['items'][0]['id']}"
                )
                await delete_calendar_event(service, events["items"][0]["id"])
            else:
                logger.info(f"Event already exists: {events['items'][0]['id']}")
                return events["items"][0]
        else:
            logger.info(f"Event does not exist, creating event: {event['summary']}")

        event = service.events().insert(calendarId="primary", body=event, sendUpdates='all').execute()
        return event

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
    service = await get_calendar_service(user_email="emilio@serniacapital.com")
    et_tz = pytz.timezone("US/Eastern")
    start_time = datetime.datetime(year=2026, month=5, day=30, hour=12).astimezone(
        et_tz
    )
    end_time = start_time + datetime.timedelta(hours=1)
    start_time_str = start_time.isoformat()  # 2025-05-14T12:00:00-04:00
    end_time_str = end_time.isoformat()

    event = {
        "summary": "Test Event Future",
        "description": "This is a test event",
        "organizer": {
            "email": "emilio@serniacapital.com",
        },
        "attendees": [
            {
                "email": "emilio+listings@serniacapital.com", 
            },
        ],
        "start": {
            "dateTime": start_time_str,
        },
        "end": {
            "dateTime": end_time_str,
        },
        "reminders": {
            "useDefault": False,
            "overrides": [
                {"method": "email", "minutes": 24 * 60},  # 1 day before
                {"method": "popup", "minutes": 120},  # 2 hours before
            ],
        }
    }
    new_event = await create_calendar_event(service, event, overwrite=True)
    pprint(new_event)
    assert new_event["summary"] == event["summary"]
