"""
Live integration tests for Google Workspace services (Calendar, Gmail,
Sheets, and service-account auth).

Moved verbatim from their service modules
(``api/src/google/calendar/service.py``, ``api/src/google/gmail/service.py``,
``api/src/google/sheets/service.py``,
``api/src/google/common/service_account_auth.py``, and
``api/src/google/gmail/tests.py``). These hit real Google APIs, so they are
marked ``live`` and only run when explicitly requested:

    pytest -m live api/src/tests/test_google_services.py -v -s
"""

import datetime
import os
from pprint import pprint

import logfire
import pytest
import pytz
from dotenv import find_dotenv, load_dotenv

load_dotenv(find_dotenv(".env"), override=False)

from api.src.google.calendar.service import (
    CalendarAttendee,
    CalendarEventInput,
    create_calendar_event,
    get_calendar_service,
)
from api.src.google.common.service_account_auth import (
    get_delegated_credentials,
    get_service_credentials,
)
from api.src.google.gmail.service import send_email
from api.src.google.sheets.service import get_sheet_as_json


# ---------------------------------------------------------------------------
# Calendar (from api/src/google/calendar/service.py)
# ---------------------------------------------------------------------------


@pytest.mark.live
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


@pytest.mark.live
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
        attendees=[CalendarAttendee(email="emilio@serniacapital.com")],
    )
    new_event = await create_calendar_event(
        service, event, organizer_email="all@serniacapital.com", overwrite=True
    )
    pprint(new_event)
    assert new_event["summary"] == event.summary


# ---------------------------------------------------------------------------
# Gmail (from api/src/google/gmail/service.py)
# ---------------------------------------------------------------------------


@pytest.mark.live
@pytest.mark.asyncio
async def test_send_email():
    await send_email(
        to="espo412@gmail.com",
        subject="Test email",
        message_text="This is a test email",
        credentials=get_delegated_credentials(
            user_email="emilio@serniacapital.com", scopes=["https://mail.google.com"]
        ),
    )


# ---------------------------------------------------------------------------
# Sheets (from api/src/google/sheets/service.py)
# ---------------------------------------------------------------------------


@pytest.mark.live
def test_get_contacts_sheet_as_json():
    spreadsheet_id = '1Gi0Wrkwm-gfCnAxycuTzHMjdebkB5cDt8wwimdYOr_M'
    return get_sheet_as_json(spreadsheet_id, sheet_name="OpenPhone")


# ---------------------------------------------------------------------------
# Service account auth (from api/src/google/common/service_account_auth.py)
# ---------------------------------------------------------------------------


@pytest.mark.live
def test_get_service_credentials():
    creds = get_service_credentials()
    print(creds)


# ---------------------------------------------------------------------------
# Gmail routes (from api/src/google/gmail/tests.py)
# ---------------------------------------------------------------------------


@pytest.mark.live
@pytest.mark.asyncio
async def test_get_zillow_emails(client):
    """Test the /gmail/get_zillow_emails endpoint"""
    # Log environment information
    logfire.info(f"Test environment - PYTEST_CURRENT_TEST: {os.environ.get('PYTEST_CURRENT_TEST')}")
    # logfire.info(f"All environment variables: {dict(os.environ)}")

    # Make the request to the endpoint
    response = client.get("/api/google/gmail/get_zillow_emails")

    # Check status code
    assert response.status_code == 200

    # Parse the response
    emails = response.json()

    # Verify the response structure
    assert type(emails) == list
    # assert len(emails) > 0 # Needed to comment this out now that we are using a local database that can be empty.
