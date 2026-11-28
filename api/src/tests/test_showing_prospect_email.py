"""The prospect's confirmation and cancellation emails, and the calendar file they link.

The words and the layout without a database, then bookings and cancellations
through the public routes against the test database with the stand-ins of
``showing_public.py``: Quo is a ``RecordingTextSender``, Gmail a
``RecordingEmailSender`` and the calendars an ``InMemoryCalendar``. Nothing is
sent anywhere. Names, numbers and addresses are made up.
"""

import re
from datetime import UTC, date, datetime, time, timedelta
from urllib.parse import parse_qs, urlparse

import pytest
import pytest_asyncio
from sqlalchemy import update

from api.src.database.database import AsyncSessionFactory
from api.src.showing_bookings import open_times, outbox, prospect_email, retention, routes, wording
from api.src.showing_bookings.models import ShowingAttendee, ShowingNotice
from api.src.showing_bookings.schema import EmailPayload
from api.src.tests.showing_helpers import LOCAL, clear_showing_data, store_settings
from api.src.tests.showing_public import (
    ALEX_EMAIL,
    ALEX_MOBILE,
    CASEY_EMAIL,
    JANE,
    ROBIN_EMAIL,
    ROBIN_MOBILE,
    UNIT,
    PublicWorld,
    add_world_rows,
    at,
    booking,
    by_kind,
    install,
    rows,
)

PUBLIC = "/api/showing-bookings/public"
BOOK = f"{PUBLIC}/units/{UNIT}/bookings"
CALENDAR = f"{PUBLIC}/bookings/calendar"
MINUTE = timedelta(minutes=1)
TZ = "America/New_York"
VISIT = wording.Visit(
    unit="324-11",
    address="324 S Example St Apt 11, Pittsburgh, PA 15224",
    starts_at=datetime(2026, 11, 29, 18, 30, tzinfo=UTC),
    ends_at=datetime(2026, 11, 29, 19, 0, tzinfo=UTC),
    timezone=TZ,
)
CANCEL = "https://serniacapital.com/showings/cancel/tok_EN-123"
FILE = "https://serniacapital.com/showings/calendar/tok_EN-123.ics"


# ---------------------------------------------------------------------------
# The words and the layout
# ---------------------------------------------------------------------------


def confirmation(**changes) -> EmailPayload:
    return prospect_email.confirmation(VISIT, cancel_url=CANCEL, file_url=FILE, **changes)


def test_the_confirmation_email_in_plain_text():
    email = confirmation()
    google = prospect_email.google_calendar_url(VISIT, CANCEL)
    maps = prospect_email.map_url(VISIT.address)

    assert email.subject == "Your showing: 324 S Example St Apt 11, Sun, Nov 29 at 1:30 PM"
    assert email.body == (
        "Your showing is booked.\n\n"
        "When: Sun, Nov 29, 1:30 to 2:00 PM\n"
        "Where: 324 S Example St Apt 11, Pittsburgh, PA 15224\n"
        f"Map: {maps}\n\n"
        "A Sernia Capital agent will meet you there.\n\n"
        f"Add to Google Calendar: {google}\n"
        f"Add to Apple or Outlook calendar: {FILE}\n\n"
        f"Can't make it? Cancel here: {CANCEL}\n\n"
        "Sernia Capital"
    )
    assert "!" not in email.body


def test_the_confirmation_email_in_html_has_the_same_links_and_no_images():
    html = confirmation().html

    assert html.startswith("<!DOCTYPE html>")
    assert ">Your showing is booked</h1>" in html
    assert "Sun, Nov 29, 1:30 to 2:00 PM" in html
    for url in (CANCEL, FILE, prospect_email.map_url(VISIT.address)):
        assert f'href="{url}"' in html.replace("&amp;", "&")
    google = prospect_email.google_calendar_url(VISIT, CANCEL)
    assert f'href="{google}"' in html.replace("&amp;", "&")
    assert ">Add to Google Calendar</a>" in html
    assert ">Add to Apple or Outlook calendar</a>" in html
    assert "<img" not in html and "<script" not in html


def test_html_escapes_what_it_shows():
    visit = wording.Visit(
        unit="1-A",
        address='1 <b>Bold</b> & "Quoted" Rd',
        starts_at=VISIT.starts_at,
        ends_at=VISIT.ends_at,
        timezone=TZ,
    )

    html = prospect_email.confirmation(visit, cancel_url=CANCEL, file_url=FILE).html

    assert "<b>Bold</b>" not in html
    assert "1 &lt;b&gt;Bold&lt;/b&gt; &amp; &quot;Quoted&quot; Rd" in html


def test_a_moved_showing_says_what_it_replaces():
    email = confirmation(replaces=(datetime(2026, 11, 28, 19, 0, tzinfo=UTC),))

    assert email.subject == (
        "Your showing has moved: 324 S Example St Apt 11, Sun, Nov 29 at 1:30 PM"
    )
    assert email.body.startswith(
        "Your showing has moved.\n\n"
        "It replaces your showing on Sat, Nov 28 at 2:00 PM. "
        "If that is on your calendar, remove it.\n\n"
        "When: Sun, Nov 29, 1:30 to 2:00 PM\n"
    )


@pytest.mark.parametrize(
    "by_team, line, closing",
    [
        (
            False,
            "Your showing on Sun, Nov 29 at 1:30 PM at 324 S Example St Apt 11, Pittsburgh, "
            "PA 15224 is cancelled.",
            "Book another time: https://serniacapital.com/showings/324-11",
        ),
        (
            True,
            "We're sorry, we had to cancel your showing on Sun, Nov 29 at 1:30 PM at "
            "324 S Example St Apt 11, Pittsburgh, PA 15224.",
            "See the units open for showings: https://serniacapital.com/showings",
        ),
    ],
)
def test_the_cancellation_email(by_team, line, closing):
    url = closing.split(": ", 1)[1]
    email = prospect_email.payload(wording.cancellation_email(VISIT, by_team=by_team, next_url=url))

    assert email.subject == "Showing cancelled: 324 S Example St Apt 11, Sun, Nov 29 at 1:30 PM"
    assert email.body == (
        f"Your showing is cancelled.\n\n{line}\n\n"
        "If it is on your calendar, remove it.\n\n"
        f"{closing}\n\nSernia Capital"
    )
    assert "When:" not in email.body and "calendar.google.com" not in email.body


def test_times_across_noon_name_both_halves_of_the_day():
    visit = wording.Visit(
        unit=VISIT.unit,
        address=VISIT.address,
        starts_at=datetime(2026, 11, 29, 16, 30, tzinfo=UTC),
        ends_at=datetime(2026, 11, 29, 17, 0, tzinfo=UTC),
        timezone=TZ,
    )

    assert wording.time_span(visit) == "Sun, Nov 29, 11:30 AM to 12:00 PM"


def test_the_google_calendar_link_is_filled_in():
    query = parse_qs(urlparse(prospect_email.google_calendar_url(VISIT, CANCEL)).query)

    assert query == {
        "action": ["TEMPLATE"],
        "text": ["Showing: 324 S Example St Apt 11 (Sernia Capital)"],
        "dates": ["20261129T183000Z/20261129T190000Z"],
        "details": [
            f"A Sernia Capital agent will meet you there.\n\nCan't make it? Cancel here: {CANCEL}"
        ],
        "location": [VISIT.address],
    }


def test_the_calendar_file_is_one_published_event_with_no_people():
    token = "tok_EN-123"
    ics = prospect_email.ics(
        VISIT, cancel_token=token, cancel_url=CANCEL, now=datetime(2026, 11, 28, tzinfo=UTC)
    )
    lines = ics.split("\r\n")
    unfolded = ics.replace("\r\n ", "")

    assert ics.endswith("\r\n") and "\n" not in ics.replace("\r\n", "")
    assert all(len(line.encode()) <= 75 for line in lines)
    assert "METHOD:PUBLISH" in lines
    assert "DTSTART:20261129T183000Z" in lines and "DTEND:20261129T190000Z" in lines
    assert "SUMMARY:Showing: 324 S Example St Apt 11 (Sernia Capital)" in unfolded
    assert "LOCATION:324 S Example St Apt 11\\, Pittsburgh\\, PA 15224" in unfolded
    assert f"URL:{CANCEL}" in unfolded
    assert "ATTENDEE" not in ics and "ORGANIZER" not in ics
    (uid,) = re.findall(r"^UID:([^\r\n]*)", unfolded, re.M)
    # The event's id is derived from the token, and never the token itself.
    assert token not in uid and uid.endswith("@showings.serniacapital.com")
    again = prospect_email.ics(VISIT, cancel_token=token, cancel_url=CANCEL, now=VISIT.starts_at)
    assert re.findall(r"^UID:([^\r\n]*)", again.replace("\r\n ", ""), re.M) == [uid]


# ---------------------------------------------------------------------------
# Bookings and cancellations
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def world(monkeypatch):
    for name in ("RAILWAY_ENVIRONMENT_NAME", "TURNSTILE_SECRET_KEY", "TURNSTILE_SITE_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("SHOWING_PUBLIC_BASE_URL", raising=False)
    monkeypatch.setenv("SHOWING_TEST_PHONES", f"{ALEX_MOBILE},{ROBIN_MOBILE}")
    monkeypatch.setenv("SHOWING_TEST_EMAILS", f"{ALEX_EMAIL},{ROBIN_EMAIL},{JANE}")
    open_times.forget_free_busy()
    await clear_showing_data()
    state = PublicWorld()
    install(state, monkeypatch)
    await add_world_rows(state)
    yield state
    open_times.forget_free_busy()
    await clear_showing_data()


async def use_test_mode() -> None:
    await store_settings(send_mode="test")


async def book(world: PublicWorld, body: dict | None = None) -> dict:
    async with world.client() as client:
        answer = await client.post(BOOK, json=body or booking())
    assert answer.status_code == 201, answer.text
    return answer.json()


def team_app(world: PublicWorld):
    application = world.team_app()
    application.dependency_overrides[routes.text_sender] = lambda: world.texts
    application.dependency_overrides[routes.clock] = lambda: world.now
    return application


async def booked_and_emailed(world: PublicWorld, start: datetime | None = None) -> dict:
    """Alex booked in test mode, the text delivered and the confirmation email sent."""
    await use_test_mode()
    result = await book(world, booking(start) if start else None)
    world.texts.report(ALEX_MOBILE, "delivered")
    await world.send_pending(world.now + MINUTE)
    assert [email.subject for email in world.emails.to(ALEX_EMAIL)] == [
        f"Your showing: 320 Example St, {wording.when(visit_at(start or at(16)))}"
    ]
    return result


def visit_at(start: datetime) -> wording.Visit:
    return wording.Visit(UNIT, "320 Example St", start, start + timedelta(minutes=30), TZ)


async def cancel(world: PublicWorld, token: str) -> None:
    async with world.client() as client:
        answer = await client.post(f"{PUBLIC}/bookings/cancel", json={"cancel_token": token})
    assert answer.status_code == 200, answer.text


@pytest.mark.asyncio
async def test_a_booking_emails_the_confirmation_after_the_text_and_invites_no_one(world):
    await use_test_mode()
    result = await book(world)
    _, _, notices = await rows()
    kinds = {n.kind: (n.channel, n.status) for n in notices}
    assert kinds["prospect_confirmation"] == ("sms", "sent")
    assert kinds["host_event"] == ("calendar_event", "held")
    assert kinds["prospect_email"] == ("email", "held")
    assert "prospect_invite" not in kinds

    world.texts.report(ALEX_MOBILE, "delivered")
    await world.send_pending(world.now + MINUTE)

    (email,) = world.emails.new
    assert email.to == ALEX_EMAIL
    assert email.subject == "Your showing: 320 Example St, Wed, Apr 14 at 4:00 PM"
    token = result["cancel_token"]
    assert f"Can't make it? Cancel here: https://serniacapital.com/showings/cancel/{token}" in (
        email.text
    )
    assert f"https://serniacapital.com/showings/calendar/{token}.ics" in email.text
    assert email.html and "Your showing is booked" in email.html
    # Only the host's event reaches the calendar, with the host as its one guest.
    assert [event.draft.attendees for event in world.calendar.events.values()] == [[JANE]]
    _, (alex,), notices = await rows()
    assert alex.calendar_event_id is None
    assert by_kind(notices)["prospect_email"][0].status == "sent"
    assert "prospect_invite" not in by_kind(notices)


@pytest.mark.asyncio
async def test_in_capture_mode_the_email_is_kept_not_sent(world):
    await book(world)

    await world.send_pending(world.now + MINUTE)

    _, _, notices = await rows()
    (email,) = by_kind(notices)["prospect_email"]
    assert email.status == "captured" and email.recipient == ALEX_EMAIL
    assert email.payload["subject"].startswith("Your showing: ")
    assert email.payload["html"].startswith("<!DOCTYPE html>")
    assert world.emails.new == []


@pytest.mark.asyncio
async def test_outside_the_test_list_the_email_is_captured(world):
    await use_test_mode()
    await book(world, booking(email=CASEY_EMAIL))
    world.texts.report(ALEX_MOBILE, "delivered")

    await world.send_pending(world.now + MINUTE)

    _, _, notices = await rows()
    assert by_kind(notices)["prospect_email"][0].status == "captured"
    assert world.emails.new == []


@pytest.mark.asyncio
async def test_an_undelivered_text_stops_the_email(world):
    await use_test_mode()
    await book(world)
    world.texts.report(ALEX_MOBILE, "undelivered")

    await world.send_pending(world.now + MINUTE)

    _, (alex,), notices = await rows()
    assert by_kind(notices)["prospect_email"][0].status == "cancelled"
    assert alex.attention_reason == "confirmation_undelivered"
    assert world.emails.new == []


@pytest.mark.asyncio
async def test_no_address_no_email(world):
    await use_test_mode()
    async with world.client(team_app(world)) as client:
        answer = await client.post(
            "/api/showing-bookings/showings/bookings",
            json={
                "unit": UNIT,
                "start": at(16).isoformat(),
                "name": "Robin Lee",
                "mobile": "412-555-0118",
                "texts": True,
            },
        )
    assert answer.status_code == 201, answer.text

    world.texts.report(ROBIN_MOBILE, "delivered")
    await world.send_pending(world.now + MINUTE)

    _, _, notices = await rows()
    assert "prospect_email" not in by_kind(notices)
    assert world.emails.new == []


@pytest.mark.asyncio
async def test_gmail_refusing_the_email_fails_it_for_the_team(world):
    await use_test_mode()
    await book(world)
    world.texts.report(ALEX_MOBILE, "delivered")
    world.emails.failing = "rejected"

    await world.send_pending(world.now + MINUTE)

    _, _, notices = await rows()
    (email,) = by_kind(notices)["prospect_email"]
    assert (email.status, email.provider_status) == ("failed", "rejected")


@pytest.mark.asyncio
async def test_a_lost_answer_is_unknown_and_never_retried(world):
    await use_test_mode()
    await book(world)
    world.texts.report(ALEX_MOBILE, "delivered")
    world.emails.failing = "lost"

    await world.send_pending(world.now + MINUTE)
    world.emails.failing = None
    await world.send_pending(world.now + 5 * MINUTE)

    _, _, notices = await rows()
    assert by_kind(notices)["prospect_email"][0].status == "unknown"
    assert world.emails.new == []


@pytest.mark.asyncio
async def test_a_prospect_who_cancels_gets_the_cancellation_email(world):
    result = await booked_and_emailed(world)

    await cancel(world, result["cancel_token"])
    await world.send_pending(world.now + 2 * MINUTE)

    subjects = [email.subject for email in world.emails.to(ALEX_EMAIL)]
    assert subjects[-1] == "Showing cancelled: 320 Example St, Wed, Apr 14 at 4:00 PM"
    last = world.emails.to(ALEX_EMAIL)[-1]
    assert f"Book another time: https://serniacapital.com/showings/{UNIT}" in last.text
    _, _, notices = await rows()
    assert by_kind(notices)["prospect_email_cancel"][0].status == "sent"
    assert "prospect_invite_cancel" not in by_kind(notices)


@pytest.mark.asyncio
async def test_the_team_cancelling_says_sorry_and_lists_the_units(world):
    await booked_and_emailed(world)
    showings, _, _ = await rows()

    async with world.client(team_app(world)) as client:
        answer = await client.post(f"/api/showing-bookings/showings/{showings[0].id}/cancel")
    assert answer.status_code == 200, answer.text
    await world.send_pending(world.now + 2 * MINUTE)

    last = world.emails.to(ALEX_EMAIL)[-1]
    assert last.subject.startswith("Showing cancelled: ")
    assert "We're sorry, we had to cancel your showing" in last.text
    assert "See the units open for showings: https://serniacapital.com/showings" in last.text


@pytest.mark.asyncio
async def test_cancelling_before_the_email_went_stops_it_and_sends_no_cancellation(world):
    await use_test_mode()
    result = await book(world)

    await cancel(world, result["cancel_token"])
    world.texts.report(ALEX_MOBILE, "delivered")
    await world.send_pending(world.now + MINUTE)

    _, _, notices = await rows()
    kinds = by_kind(notices)
    assert kinds["prospect_email"][0].status == "cancelled"
    assert "prospect_email_cancel" not in kinds
    assert world.emails.new == []


@pytest.mark.asyncio
async def test_a_cancellation_of_an_email_only_captured_is_not_sent(world):
    """Captured in capture mode, then test mode: the cancellation would be the first email."""
    result = await book(world)
    await world.send_pending(world.now + MINUTE)
    await use_test_mode()

    await cancel(world, result["cancel_token"])
    await world.send_pending(world.now + 2 * MINUTE)

    _, _, notices = await rows()
    (cancellation,) = by_kind(notices)["prospect_email_cancel"]
    assert cancellation.status == "cancelled"
    assert "captured and never sent" in cancellation.last_error
    assert world.emails.new == []


@pytest.mark.asyncio
async def test_the_cancellation_waits_while_the_confirmation_is_being_sent(world):
    await booked_and_emailed(world)
    _, (alex,), notices = await rows()
    sent = by_kind(notices)["prospect_email"][0]
    async with AsyncSessionFactory() as session, session.begin():
        await session.execute(
            update(ShowingNotice)
            .where(ShowingNotice.id == sent.id)
            .values(status="pending", sending_started_at=world.now)
        )
    await cancel(world, alex.cancel_token)

    report = await outbox.drain(
        now=world.now + MINUTE, texts=world.texts, calendar=world.calendar, emails=world.emails
    )

    _, _, notices = await rows()
    (cancellation,) = by_kind(notices)["prospect_email_cancel"]
    assert cancellation.status == "pending" and cancellation.not_before > world.now + MINUTE
    assert report.sent == 0 or all(
        n.kind != "prospect_email_cancel" for n in notices if n.status == "sent"
    )


@pytest.mark.asyncio
async def test_a_booking_with_an_invite_from_before_keeps_its_invite_withdrawal(world):
    """An invite sent before the email took its place is withdrawn as before, and no
    cancellation email follows an email that never existed."""
    await use_test_mode()
    result = await book(world)
    world.texts.report(ALEX_MOBILE, "delivered")
    await world.send_pending(world.now + MINUTE)
    _, (alex,), notices = await rows()
    email = by_kind(notices)["prospect_email"][0]
    async with AsyncSessionFactory() as session, session.begin():
        # As a booking from before 2026-11-28 left it: a sent invite and no email.
        await session.execute(
            update(ShowingNotice)
            .where(ShowingNotice.id == email.id)
            .values(
                kind="prospect_invite",
                channel="calendar_invite",
                provider_id="invite-1",
                payload={
                    "operation": "create",
                    "calendar_id": "sernia-showings-test@example.com",
                    "summary": "Showing of 320 Example St with Sernia Capital",
                    "starts_at": at(16).isoformat(),
                    "ends_at": at(16, 30).isoformat(),
                    "attendees": [ALEX_EMAIL],
                },
            )
        )
        await session.execute(
            update(ShowingAttendee)
            .where(ShowingAttendee.id == alex.id)
            .values(calendar_event_id="invite-1")
        )

    await cancel(world, result["cancel_token"])

    _, _, notices = await rows()
    kinds = by_kind(notices)
    assert [n.kind for n in kinds["prospect_invite_cancel"]] == ["prospect_invite_cancel"]
    assert "prospect_email_cancel" not in kinds


# ---------------------------------------------------------------------------
# The calendar file
# ---------------------------------------------------------------------------


async def calendar_file(world: PublicWorld, token: str, now: datetime | None = None):
    from api.src.showing_bookings import public_routes

    app = world.app()
    if now is not None:
        app.dependency_overrides[public_routes.clock] = lambda: now
    async with world.client(app) as client:
        return await client.post(CALENDAR, json={"cancel_token": token})


@pytest.mark.asyncio
async def test_the_calendar_file_for_a_booking(world):
    result = await book(world)

    answer = await calendar_file(world, result["cancel_token"])

    assert answer.status_code == 200
    assert answer.headers["content-type"] == "text/calendar; charset=utf-8"
    assert answer.headers["cache-control"] == "no-store"
    body = answer.text
    assert "METHOD:PUBLISH" in body and "ATTENDEE" not in body
    assert "SUMMARY:Showing: 320 Example St (Sernia Capital)" in body.replace("\r\n ", "")
    # Nothing about the person: no name, number or address of theirs.
    for private in ("Alex", "Parker", ALEX_EMAIL, "555-0117", "5550117"):
        assert private not in body


@pytest.mark.asyncio
async def test_no_calendar_file_for_a_cancelled_started_or_unknown_booking(world):
    result = await book(world)
    token = result["cancel_token"]

    started = await calendar_file(world, token, now=at(16, 1))
    assert (started.status_code, started.json()["detail"]["code"]) == (409, "started")

    await cancel(world, token)
    cancelled = await calendar_file(world, token)
    assert (cancelled.status_code, cancelled.json()["detail"]["code"]) == (409, "not_booked")

    unknown = await calendar_file(world, "no-such-token")
    assert (unknown.status_code, unknown.json()["detail"]["code"]) == (404, "unknown_booking")


@pytest.mark.asyncio
async def test_the_calendar_file_answers_with_public_booking_switched_off(world):
    result = await book(world)
    await store_settings(public_booking_enabled=False)

    answer = await calendar_file(world, result["cancel_token"])

    assert answer.status_code == 200


# ---------------------------------------------------------------------------
# Removing details after 12 months
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_emails_are_removed_after_12_months(world):
    old_start = datetime.combine(date(2025, 4, 9), time(16), LOCAL)
    world.now = old_start - timedelta(days=1)
    result = await booked_and_emailed(world, old_start)
    await cancel(world, result["cancel_token"])
    await world.send_pending(world.now + 2 * MINUTE)
    _, _, notices = await rows()
    assert by_kind(notices)["prospect_email_cancel"][0].status == "sent"

    await retention.remove_old_details(now=old_start + timedelta(days=366))

    _, _, notices = await rows()
    for notice in by_kind(notices)["prospect_email"] + by_kind(notices)["prospect_email_cancel"]:
        assert notice.recipient == retention.REMOVED_RECIPIENT
        assert notice.payload == {"body": retention.REMOVED, retention.REMOVED_KEY: True}
