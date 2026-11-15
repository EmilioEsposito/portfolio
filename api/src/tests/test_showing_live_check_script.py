"""The calendar live check script, run against ``FakeGoogle`` so it cannot rot.

Uses the test database. The script itself is run by hand against Google; here
nothing leaves the process.
"""

from datetime import UTC, datetime

import pytest
import pytest_asyncio

from api.src.database.database import AsyncSessionFactory
from api.src.showing_bookings import connections, google_oauth, token_crypto
from api.src.showing_bookings.models import CalendarConnection
from api.src.tests.showing_google import TEAM_ACCOUNT, TEAM_SUBJECT, FakeGoogle, configure
from api.src.tests.showing_helpers import TEAM_EMAIL, clear_showing_data
from scripts import showing_calendar_live_check as live_check

NOW = datetime(2027, 4, 14, 13, tzinfo=UTC)
CALENDAR = "c_showings@group.calendar.google.com"


@pytest_asyncio.fixture(autouse=True)
async def clean(monkeypatch):
    configure(monkeypatch)
    monkeypatch.setenv("SHOWING_TEST_EMAILS", TEAM_EMAIL)
    connections.forget_access_tokens()
    await clear_showing_data()
    yield
    connections.forget_access_tokens()
    await clear_showing_data()


@pytest_asyncio.fixture
async def google(monkeypatch) -> FakeGoogle:
    google = FakeGoogle().install(monkeypatch)
    google.calendars[CALENDAR] = {"summary": "Sernia showings", "owner": TEAM_SUBJECT}
    async with AsyncSessionFactory() as session:
        session.add(
            CalendarConnection(
                google_email=TEAM_ACCOUNT,
                google_subject=TEAM_SUBJECT,
                scopes=list(google_oauth.CALENDAR_SCOPES),
                encrypted_refresh_token=token_crypto.encrypt(google.refresh_token_for()),
                showings_calendar_id=CALENDAR,
                connected_by="pat@serniacapital.com",
                connected_at=NOW,
            )
        )
        await session.commit()
    return google


@pytest.mark.asyncio
async def test_it_reads_then_invites_only_the_test_guest_and_cancels(google):
    lines = await live_check.run(TEAM_ACCOUNT, TEAM_EMAIL, now=NOW)

    assert lines[0].startswith("Free/busy: read")
    assert any(line.startswith("Create: made event") for line in lines)
    assert lines[-1].startswith("Cancel: done")
    (created,) = google.sent("POST", f"/calendar/v3/calendars/{CALENDAR}/events")
    assert google.body(created)["attendees"] == [{"email": TEAM_EMAIL}]
    assert created.url.params["sendUpdates"] == "all"
    (event,) = google.events.values()
    assert event.deleted
    assert not [secret for secret in google.secrets if secret in "\n".join(lines)]


@pytest.mark.asyncio
@pytest.mark.parametrize("guest", ["alex@example.com", "not an address", ""])
async def test_any_guest_but_a_test_recipient_is_refused_before_google(google, guest):
    with pytest.raises(live_check.Refused, match="SHOWING_TEST_EMAILS"):
        await live_check.run(TEAM_ACCOUNT, guest, now=NOW)
    assert google.requests == []


@pytest.mark.asyncio
async def test_an_account_that_is_not_connected_here_is_refused(google, monkeypatch):
    with pytest.raises(live_check.Refused, match="No calendar connection"):
        await live_check.run("someone@example.com", TEAM_EMAIL, now=NOW)
    monkeypatch.delenv("SHOWING_TOKEN_KEY")
    with pytest.raises(live_check.Refused, match="cannot be used here"):
        await live_check.run(TEAM_ACCOUNT, TEAM_EMAIL, now=NOW)
    assert google.requests == []
