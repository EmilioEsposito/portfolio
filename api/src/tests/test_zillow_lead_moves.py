"""A showing a lead moves by email keeps the details they gave.

The lead books by email, adds their details through their own link (or books
through it), and a later approved reply moves the showing: the new place takes
over the name, mobile, email, consent record and requirement ticks, the moved
confirmation text and the invite wait until the reply's send is settled, and
the old place is cancelled with no text to the lead. Also: what the lead's link
shows once their details are in, and that their own showing is not offered
back to them.

Everything runs through the lead agent's code, the job every minute and the
public routes against the test database, with the stand-ins of
``showing_public.py`` and the lead fixtures of ``test_zillow_lead_bookings.py``.
Names, numbers and threads are made up.
"""

# The ``world`` and ``lead`` fixtures are imported, so each test's parameter of
# the same name is that fixture, not a redefinition.
# ruff: noqa: F811

from datetime import datetime, time, timedelta
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from sqlalchemy import select, update

from api.src.database.database import AsyncSessionFactory
from api.src.showing_bookings import (
    cancelling,
    delivery_checks,
    holds,
    lead_bookings,
    open_times,
    outbox,
    resident_notices,
    wording,
)
from api.src.showing_bookings.fakes import RecordingAlerter
from api.src.showing_bookings.holds import send_pending
from api.src.showing_bookings.models import ShowingAttendee, ShowingNotice
from api.src.tests.showing_helpers import LOCAL, store_settings
from api.src.tests.showing_public import (
    ALEX_EMAIL,
    ALEX_MOBILE,
    JANE,
    ROBIN_EMAIL,
    ROBIN_MOBILE,
    TUESDAY,
    UNIT,
    WEDNESDAY,
    at,
    booking,
    by_kind,
    rows,
)
from api.src.tests.test_zillow_lead_bookings import (  # noqa: F401  (fixtures)
    EDITED,
    JORDAN_MOBILE,
    OTHER_THREAD,
    PUBLIC,
    RESIDENT,
    THREAD,
    Lead,
    booked_and_told,
    booked_through_link,
    card_notes,
    details,
    gmail_refused,
    lead,
    outcome,
    pending_cards,
    stored,
    world,
)
from api.src.zillow_leads import handle, lead_links
from api.src.zillow_leads.auto_reply import reply_problems
from api.src.zillow_leads.case import (
    BookedTour,
    Listing,
    ListingData,
    OwnShowing,
    TourSlot,
    allowed_tour_times,
    build_case,
    render_case,
)

THURSDAY = WEDNESDAY + timedelta(days=1)
PLACE = f"{PUBLIC}/units/{UNIT}/lead-booking"
DETAILS = f"{PUBLIC}/units/{UNIT}/lead-booking/details"
HOUR = timedelta(hours=1)
MINUTE = timedelta(minutes=1)


async def go_live(monkeypatch) -> None:
    """Texts and invites to Alex really go, to the stand-ins; everything else is captured."""
    monkeypatch.setenv("SHOWING_TEST_PHONES", ALEX_MOBILE)
    monkeypatch.setenv("SHOWING_TEST_EMAILS", ALEX_EMAIL)
    await store_settings(lead_agent_uses_open_times=True, send_mode="test")


async def with_details(lead: Lead) -> ShowingAttendee:
    """Jordan booked by email for Wednesday at 4 and their details added through the
    link (Alex's number and address), with the confirmation and the invite delivered.
    An hour then passes, so the consent was given before whatever comes next."""
    await booked_and_told(lead)
    token = await lead_links.token_for_thread(THREAD)
    async with lead.world.client() as client:
        answer = await client.post(DETAILS, json=details(token))
    assert answer.status_code == 201, answer.text
    for minutes in (1, 2):
        await lead.world.send_pending(lead.world.now + minutes * MINUTE)
    lead.world.now += HOUR
    _, (place,), _ = await rows()
    assert place.texts_consent_at is not None and place.email == ALEX_EMAIL
    return place


async def move(lead: Lead, start: datetime | None = None, **fields) -> str:
    """An approved reply that moves the showing, sent; the conversation id."""
    conversation_id = await lead.decide(
        outcome(status="rescheduled", start=start or at(17), **fields)
    )
    await lead.decide_on(conversation_id)
    return conversation_id


async def stopped_after_booking(lead: Lead, start: datetime | None = None) -> str:
    """A process booked the move's new place for its reply and stopped before sending."""
    decided = outcome(status="rescheduled", start=start or at(17))
    conversation_id = await lead.decide(decided)
    prepared = await lead_bookings.prepare_reply(
        conversation_id=conversation_id,
        thread_id=THREAD,
        lead=decided.lead,
        tour=decided.tour,
        listed_units=[UNIT],
        now=lead.world.now,
    )
    assert prepared.action == "moved"
    return conversation_id


async def places() -> list[ShowingAttendee]:
    _, attendees, _ = await rows()
    return attendees


async def notices_of(attendee_id: int) -> dict[str, list[ShowingNotice]]:
    _, _, notices = await rows()
    return by_kind([notice for notice in notices if notice.attendee_id == attendee_id])


async def change(attendee_id: int, **values) -> None:
    async with AsyncSessionFactory() as session, session.begin():
        await session.execute(
            update(ShowingAttendee).where(ShowingAttendee.id == attendee_id).values(**values)
        )


def texts_to_the_lead(lead: Lead) -> list[str]:
    return lead.world.texts.to(ALEX_MOBILE)


# ---------------------------------------------------------------------------
# The new place takes over the details
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_moved_showing_keeps_the_leads_details(lead):
    old = await with_details(lead)

    conversation_id = await move(lead)

    before, new = await places()
    assert (before.id, before.status, before.cancel_reason) == (old.id, "cancelled", "prospect")
    assert new.status == "booked" and new.source == "lead_agent"
    assert new.zillow_thread_id == THREAD and new.lead_conversation_id == conversation_id
    assert (new.name, new.mobile, new.email) == ("Jordan Lee", ALEX_MOBILE, ALEX_EMAIL)
    # The consent record as it was given, an hour before the move.
    assert new.texts_accepted is True
    assert new.texts_consent_at == old.texts_consent_at == lead.world.now - HOUR
    assert new.texts_consent_version == old.texts_consent_version
    assert new.texts_consent_page == old.texts_consent_page
    assert new.texts_consent_page.endswith(f"/showings/{UNIT}")
    assert [item["key"] for item in new.requirement_confirmations] == ["lease_start", "pets"]
    assert new.requirement_confirmations == old.requirement_confirmations
    # What says how the old place was completed stays with it.
    assert (new.lead_token, new.client_ip_hash, new.user_agent) == (None, None, None)
    assert new.cancel_token != old.cancel_token
    row, _ = await stored(conversation_id)
    record = row.metadata_["showing_booking"]
    assert (record["action"], record["details_kept"], record["sent"]) == ("moved", True, "sent")


@pytest.mark.asyncio
async def test_a_place_booked_through_the_link_keeps_its_details_when_it_moves(lead):
    await booked_through_link(lead, at(16))
    lead.world.now += HOUR
    (old,) = await places()
    assert old.source == "page"

    await move(lead)

    _, new = await places()
    assert new.source == "lead_agent" and new.status == "booked"
    assert (new.name, new.mobile, new.email) == ("Alex Parker", ALEX_MOBILE, ALEX_EMAIL)
    assert new.texts_consent_at == old.texts_consent_at
    assert new.requirement_confirmations == old.requirement_confirmations


@pytest.mark.asyncio
async def test_a_second_move_keeps_the_first_consent_record(lead):
    old = await with_details(lead)
    await move(lead)
    await lead.world.send_pending(lead.world.now + MINUTE)
    lead.world.now += HOUR

    await move(lead, at(18))

    first, second, third = await places()
    assert [a.status for a in (first, second, third)] == ["cancelled", "cancelled", "booked"]
    assert third.texts_consent_at == old.texts_consent_at
    assert third.requirement_confirmations == old.requirement_confirmations
    (confirmation,) = (await notices_of(third.id))["prospect_confirmation"]
    assert "It replaces your showing on Wed, Apr 14 at 5:00 PM." in confirmation.payload["body"]


@pytest.mark.asyncio
async def test_the_host_sees_the_kept_details(lead):
    await with_details(lead)

    await move(lead)

    _, new = await places()
    (event,) = (await notices_of(new.id))["host_event"]
    assert (
        f"- Jordan Lee, 412-555-0117, {ALEX_EMAIL}\n"
        "  Booked by email from their Zillow inquiry\n"
        f"  Inquiry: http://localhost:5173/team/leads/threads/{THREAD}\n"
    ) in event.payload["description"]
    assert "  Confirmed: No pet dogs will live in the apartment." in event.payload["description"]


@pytest.mark.asyncio
async def test_a_move_into_a_showing_others_booked_keeps_the_details_too(lead):
    async with lead.world.client() as client:
        robin = booking(at(17), name="Robin Lee", mobile="412-555-0118", email=ROBIN_EMAIL)
        assert (await client.post(f"{PUBLIC}/units/{UNIT}/bookings", json=robin)).status_code == 201
    await booked_and_told(lead)
    token = await lead_links.token_for_thread(THREAD)
    async with lead.world.client() as client:
        assert (await client.post(DETAILS, json=details(token))).status_code == 201
    lead.world.now += HOUR

    await move(lead)

    new = (await places())[-1]
    assert (new.mobile, new.email, new.texts_accepted) == (ALEX_MOBILE, ALEX_EMAIL, True)
    kinds = await notices_of(new.id)
    # A join: the host's event is updated, and the residents, told already, hear nothing new.
    assert "host_event" not in kinds and len(kinds["host_event_update"]) == 1
    assert kinds["host_event_update"][0].status == "pending"  # not held behind the text
    assert [n.status for n in kinds["prospect_invite"]] == ["held"]
    _, _, notices = await rows()
    assert len(by_kind(notices)["resident_new_showing"]) == 2  # one for each showing


# ---------------------------------------------------------------------------
# What goes out, and when
# ---------------------------------------------------------------------------


def test_the_moved_wording():
    visit = wording.Visit(
        unit=UNIT,
        address="320 Example St",
        starts_at=at(17),
        ends_at=at(17, 30),
        timezone="America/New_York",
    )
    assert wording.MOVED_VERSION == "v1"
    assert wording.moved_confirmation_text(visit, [at(16)], "https://c.example.com") == (
        "Sernia Capital: your in-person showing has moved to Wed, Apr 14 at 5:00 PM at "
        "320 Example St (unit 320-03). It replaces your showing on Wed, Apr 14 at 4:00 PM.\n"
        "To cancel: https://c.example.com\n"
        "Reply STOP to opt out."
    )
    both = wording.moved_confirmation_text(
        visit, [at(10, day=THURSDAY), at(16)], "https://c.example.com"
    )
    assert (
        "It replaces your showings on Wed, Apr 14 at 4:00 PM and Thu, Apr 15 at 10:00 AM." in both
    )


@pytest.mark.asyncio
async def test_the_moved_text_and_invite_wait_for_the_send_then_go(lead, monkeypatch):
    await go_live(monkeypatch)
    await with_details(lead)
    first = texts_to_the_lead(lead)
    assert len(first) == 1  # the confirmation of the details

    seen: list[tuple[str, str]] = []

    async def slow_send(route, body):
        """While the reply is on its way, even past the hold, nothing goes to the lead."""
        for minutes in (1, 6, 30):
            await lead.world.send_pending(lead.world.now + minutes * MINUTE)
        new = (await places())[-1]
        kinds = await notices_of(new.id)
        seen.extend((kind, n.status) for kind, found in kinds.items() for n in found)
        return handle.Delivery("Email sent.")

    monkeypatch.setattr(handle, "send_reply", AsyncMock(side_effect=slow_send))

    await move(lead)

    assert ("prospect_confirmation", "pending") in seen and ("prospect_invite", "held") in seen
    old, new = await places()
    assert texts_to_the_lead(lead) == first
    confirmation = (await notices_of(new.id))["prospect_confirmation"][0]
    assert confirmation.not_before <= lead.world.now  # released with the send

    await lead.world.send_pending(lead.world.now + MINUTE)
    kinds = await notices_of(new.id)
    assert [n.status for n in kinds["prospect_confirmation"]] == ["sent"]
    assert [n.status for n in kinds["prospect_invite"]] == ["held"]  # behind the delivery
    (text,) = texts_to_the_lead(lead)[1:]
    assert text == (
        "Sernia Capital: your in-person showing has moved to Wed, Apr 14 at 5:00 PM at "
        "320 Example St (unit 320-03). It replaces your showing on Wed, Apr 14 at 4:00 PM.\n"
        f"To cancel: https://serniacapital.com/showings/cancel/{new.cancel_token}\n"
        "Reply STOP to opt out."
    )

    await lead.world.send_pending(lead.world.now + 2 * MINUTE)
    kinds = await notices_of(new.id)
    assert [n.status for n in kinds["prospect_invite"]] == ["sent"]
    assert kinds["prospect_invite"][0].payload["attendees"] == [ALEX_EMAIL]
    assert kinds["prospect_invite"][0].payload["starts_at"] == "2027-04-14T17:00:00-04:00"
    # The old invite is withdrawn, and the new one stands.
    old, new = await places()
    events = {e.event_id: e.cancelled for e in lead.world.calendar.events.values()}
    assert events[old.calendar_event_id] is True and events[new.calendar_event_id] is False
    assert len(texts_to_the_lead(lead)) == 2


@pytest.mark.asyncio
async def test_in_capture_mode_the_moved_text_is_kept_not_sent(lead):
    await with_details(lead)

    await move(lead)
    for minutes in (1, 2):
        await lead.world.send_pending(lead.world.now + minutes * MINUTE)

    _, new = await places()
    kinds = await notices_of(new.id)
    assert [n.status for n in kinds["prospect_confirmation"]] == ["captured"]
    assert [n.status for n in kinds["prospect_invite"]] == ["captured"]
    assert lead.world.texts.sent == []


@pytest.mark.asyncio
async def test_the_reminder_of_a_moved_showing_goes_by_text_not_by_email(lead):
    await with_details(lead)
    await move(lead)
    await lead.world.send_pending(lead.world.now + MINUTE)

    report = await lead.world.send_pending(datetime.combine(TUESDAY, time(18), LOCAL))

    _, new = await places()
    assert (report.prospect_reminders, report.lead_email_reminders) == (1, 0)
    (reminder,) = (await notices_of(new.id))["prospect_reminder"]
    assert "Wed, Apr 14 at 5:00 PM" in reminder.payload["body"]
    assert lead.world.emails.sent == []


# ---------------------------------------------------------------------------
# When the send fails
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_refused_send_cancels_the_moved_place_and_texts_nothing(lead, monkeypatch):
    await go_live(monkeypatch)
    await with_details(lead)
    first = texts_to_the_lead(lead)
    invites = len(lead.world.calendar.events)
    monkeypatch.setattr(handle, "send_reply", AsyncMock(side_effect=gmail_refused()))

    with pytest.raises(HTTPException):
        await move(lead)

    old, new = await places()
    assert old.status == "booked"
    assert (new.status, new.cancel_reason) == ("cancelled", "reply_not_sent")
    kinds = await notices_of(new.id)
    assert {n.status for found in kinds.values() for n in found} == {"cancelled"}
    assert "prospect_cancelled" not in kinds and "prospect_invite_cancel" not in kinds
    for minutes in (1, 6, 60):
        await lead.world.send_pending(lead.world.now + minutes * MINUTE)
    assert texts_to_the_lead(lead) == first
    assert len([e for e in lead.world.calendar.events.values() if not e.cancelled]) == invites


@pytest.mark.asyncio
async def test_an_uncertain_send_keeps_the_moved_place_and_its_notices(lead, monkeypatch):
    await go_live(monkeypatch)
    await with_details(lead)
    monkeypatch.setattr(handle, "send_reply", AsyncMock(side_effect=TimeoutError("maybe sent")))

    with pytest.raises(TimeoutError):
        await move(lead)

    old, new = await places()
    assert (old.status, old.cancel_reason) == ("cancelled", "prospect")
    assert new.status == "booked" and new.attention_reason == lead_bookings.REPLY_UNCERTAIN
    assert new.lead_reply_uncertain_at == lead.world.now and new.lead_reply_sent_at is None
    assert len(texts_to_the_lead(lead)) == 1
    # Held for its five minutes, as the place's other notices are, then it goes.
    await lead.world.send_pending(lead.world.now + MINUTE)
    assert len(texts_to_the_lead(lead)) == 1
    await lead.world.send_pending(lead.world.now + 5 * MINUTE)
    await lead.world.send_pending(lead.world.now + 6 * MINUTE)
    assert "has moved to Wed, Apr 14 at 5:00 PM" in texts_to_the_lead(lead)[1]
    kinds = await notices_of(new.id)
    assert [n.status for n in kinds["prospect_invite"]] == ["sent"]
    assert len(texts_to_the_lead(lead)) == 2  # nothing said the old one was cancelled


@pytest.mark.asyncio
async def test_a_refused_number_marks_the_moved_place_and_it_stands(lead, monkeypatch):
    await go_live(monkeypatch)
    await with_details(lead)
    lead.world.texts.refuse(ALEX_MOBILE, "opted_out")

    await move(lead)
    for minutes in (1, 2):
        await lead.world.send_pending(lead.world.now + minutes * MINUTE)

    _, new = await places()
    assert new.status == "booked" and new.attention_reason == "confirmation_refused"
    kinds = await notices_of(new.id)
    assert [n.status for n in kinds["prospect_confirmation"]] == ["failed"]
    assert [n.status for n in kinds["prospect_invite"]] == ["cancelled"]
    # The reply told the lead, so the host and the residents are told as for any move.
    assert [n.status for n in kinds["host_event"]] == ["captured"]
    showings, _, notices = await rows()
    told = [n for n in by_kind(notices)["resident_new_showing"] if n.showing_id == showings[1].id]
    assert [n.status for n in told] == ["captured"]
    assert showings[1].status == "scheduled"
    # It reads on the Showings section, and the reminder goes by email instead.
    report = await lead.world.send_pending(datetime.combine(TUESDAY, time(18), LOCAL))
    assert (report.prospect_reminders, report.lead_email_reminders) == (0, 1)


@pytest.mark.asyncio
async def test_an_undelivered_text_marks_the_moved_place_and_it_stands(lead, monkeypatch):
    await go_live(monkeypatch)
    await with_details(lead)
    lead.world.texts.report(ALEX_MOBILE, "undelivered")

    await move(lead)
    for minutes in (1, 2):
        await lead.world.send_pending(lead.world.now + minutes * MINUTE)

    _, new = await places()
    assert new.status == "booked" and new.attention_reason == "confirmation_undelivered"
    assert [n.status for n in (await notices_of(new.id))["prospect_invite"]] == ["cancelled"]


@pytest.mark.asyncio
async def test_an_uncertain_place_whose_number_is_refused_is_still_never_cleaned_up(
    lead, monkeypatch
):
    """The refusal replaces the ``reply_uncertain`` mark; the place still counts as one
    whose reply may have gone out."""
    await go_live(monkeypatch)
    await with_details(lead)
    lead.world.texts.refuse(ALEX_MOBILE, "invalid_number")
    monkeypatch.setattr(handle, "send_reply", AsyncMock(side_effect=TimeoutError("maybe sent")))
    conversation_id = await lead.decide(outcome(status="rescheduled", start=at(17)))
    with pytest.raises(TimeoutError):
        await lead.decide_on(conversation_id)
    for minutes in (5, 6):
        await lead.world.send_pending(lead.world.now + minutes * MINUTE)
    assert (await places())[1].attention_reason == "confirmation_refused"

    await lead.decide_on(conversation_id, approved=False)

    assert (await places())[1].status == "booked"


# ---------------------------------------------------------------------------
# A process that stops between the booking and the send
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_stopped_process_never_texts_the_lead(lead, monkeypatch):
    await go_live(monkeypatch)
    await with_details(lead)
    first = texts_to_the_lead(lead)
    await stopped_after_booking(lead)
    alerter = RecordingAlerter()

    for minutes in (1, 6, 16):
        await send_pending(
            now=lead.world.now + minutes * MINUTE,
            texts=lead.world.texts,
            calendar=lead.world.calendar,
            emails=lead.world.emails,
            alerter=alerter,
        )
    report = await lead.world.send_pending(datetime.combine(TUESDAY, time(18), LOCAL))

    old, new = await places()
    assert old.status == new.status == "booked"
    # The old showing's reminder is the only new text; the new place has none.
    (reminder,) = texts_to_the_lead(lead)[len(first) :]
    assert "a reminder of your in-person showing on Wed, Apr 14 at 4:00 PM" in reminder
    assert report.prospect_reminders == 1 and report.lead_email_reminders == 0
    kinds = await notices_of(new.id)
    assert [n.status for n in kinds["prospect_confirmation"]] == ["pending"]
    assert [n.status for n in kinds["prospect_invite"]] == ["held"]
    # The team is told once that the text is waiting; the reply's card is still theirs to decide.
    assert [alert.title for alert in alerter.alerts] == ["Showing message stuck"]
    assert "Prospect's confirmation for unit 320-03" in alerter.alerts[0].body


@pytest.mark.asyncio
async def test_a_text_waiting_for_its_reply_takes_no_room_in_a_run(lead, monkeypatch):
    """It is the oldest due notice, and a run limited to one still delivers the next."""
    await go_live(monkeypatch)
    await with_details(lead)
    await stopped_after_booking(lead)

    report = await outbox.drain(
        now=lead.world.now + 6 * MINUTE,
        texts=lead.world.texts,
        calendar=lead.world.calendar,
        emails=lead.world.emails,
        limit=1,
    )

    assert report.total == 1
    _, new = await places()
    kinds = await notices_of(new.id)
    assert [n.status for n in kinds["prospect_confirmation"]] == ["pending"]


@pytest.mark.asyncio
async def test_an_invite_waiting_for_its_reply_takes_no_room_in_a_run(lead, monkeypatch):
    """Its confirmation is the oldest one holding anything, and a run limited to one
    still releases what another booking's confirmation holds."""
    await go_live(monkeypatch)
    monkeypatch.setenv("SHOWING_TEST_PHONES", f"{ALEX_MOBILE},{ROBIN_MOBILE}")
    await with_details(lead)
    await stopped_after_booking(lead)
    async with lead.world.client() as client:
        robin = booking(at(15), name="Robin Lee", mobile="412-555-0118", email=ROBIN_EMAIL)
        answer = await client.post(f"{PUBLIC}/units/{UNIT}/bookings", json=robin)
    assert answer.status_code == 201, answer.text
    (theirs,) = [place for place in await places() if place.name == "Robin Lee"]
    assert "held" in {n.status for n in (await notices_of(theirs.id))["prospect_invite"]}
    monkeypatch.setattr(holds, "HOLDERS_PER_RUN", 1)

    report = await holds.release_held(now=lead.world.now + MINUTE, texts=lead.world.texts)

    assert report.released > 0
    assert "held" not in {n.status for n in (await notices_of(theirs.id))["prospect_invite"]}


@pytest.mark.asyncio
async def test_a_text_still_waiting_when_the_showing_starts_is_stopped(lead, monkeypatch):
    await go_live(monkeypatch)
    await with_details(lead)
    first = texts_to_the_lead(lead)
    await stopped_after_booking(lead)

    await lead.world.send_pending(at(17) - MINUTE)
    _, new = await places()
    assert [n.status for n in (await notices_of(new.id))["prospect_confirmation"]] == ["pending"]

    for minutes in (0, 1):
        await lead.world.send_pending(at(17) + minutes * MINUTE)

    kinds = await notices_of(new.id)
    (text,) = kinds["prospect_confirmation"]
    assert (text.status, text.last_error) == ("cancelled", delivery_checks.REPLY_NOT_SENT)
    assert [n.status for n in kinds["prospect_invite"]] == ["cancelled"]
    assert not any("has moved to" in body for body in texts_to_the_lead(lead)[len(first) :])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "values",
    [
        {},
        {"lead_reply_sent_at": at(9)},
        {"lead_reply_uncertain_at": at(9)},
        {"lead_token": "AbCdEfGhIjKlMnOpQrStUv"},
        {"source": "page"},
        {"lead_conversation_id": None},
    ],
)
async def test_the_unsettled_rule_reads_the_same_in_sql(lead, values):
    await with_details(lead)
    await stopped_after_booking(lead)
    _, new = await places()
    if values:
        await change(new.id, **values)

    async with AsyncSessionFactory() as session:
        place = await session.get_one(ShowingAttendee, new.id)
        in_sql = await session.scalar(
            select(delivery_checks.reply_unsettled_clause()).where(ShowingAttendee.id == new.id)
        )

    assert in_sql is delivery_checks.reply_unsettled(place) is (not values)


@pytest.mark.asyncio
async def test_approving_the_leftover_card_sends_the_moved_text(lead, monkeypatch):
    await go_live(monkeypatch)
    await with_details(lead)
    conversation_id = await stopped_after_booking(lead)
    await lead.world.send_pending(lead.world.now + 6 * MINUTE)
    lead.world.now += 10 * MINUTE

    await lead.decide_on(conversation_id)
    await lead.world.send_pending(lead.world.now + MINUTE)

    old, new = await places()
    assert (old.status, new.status) == ("cancelled", "booked")
    assert new.texts_consent_at == old.texts_consent_at
    assert "has moved to Wed, Apr 14 at 5:00 PM" in texts_to_the_lead(lead)[-1]
    row, _ = await stored(conversation_id)
    record = row.metadata_["showing_booking"]
    assert (record["action"], record["details_kept"]) == ("moved", True)


@pytest.mark.asyncio
@pytest.mark.parametrize("how", ["rejected", "edited", "gmail refuses"])
async def test_a_leftover_place_with_details_goes_as_any_unsent_one_and_silently(
    lead, monkeypatch, how
):
    await go_live(monkeypatch)
    await with_details(lead)
    first = texts_to_the_lead(lead)
    conversation_id = await stopped_after_booking(lead)
    await lead.world.send_pending(lead.world.now + 6 * MINUTE)

    if how == "rejected":
        await lead.decide_on(conversation_id, approved=False)
    elif how == "edited":
        await lead.decide_on(conversation_id, body=EDITED)
    else:
        monkeypatch.setattr(handle, "send_reply", AsyncMock(side_effect=gmail_refused()))
        with pytest.raises(HTTPException):
            await lead.decide_on(conversation_id)
    await lead.world.send_pending(lead.world.now + HOUR)

    old, new = await places()
    assert old.status == "booked"
    assert (new.status, new.cancel_reason) == ("cancelled", "reply_not_sent")
    assert texts_to_the_lead(lead) == first
    assert "prospect_cancelled" not in await notices_of(new.id)


@pytest.mark.asyncio
async def test_the_team_cancelling_an_unsent_place_texts_the_lead_nothing(lead):
    """They never heard of it. Nor would a unit going off the market."""
    await with_details(lead)
    await stopped_after_booking(lead)
    showings, _, _ = await rows()
    async with AsyncSessionFactory() as session:
        effect = await cancelling.off_market_effect(session, UNIT, WEDNESDAY, now=lead.world.now)
    assert (effect.people, effect.people_texted) == (2, 1)

    await cancelling.cancel_showing(showings[1].id, now=lead.world.now, texts=lead.world.texts)

    _, new = await places()
    assert (new.status, new.cancel_reason) == ("cancelled", "team")
    assert "prospect_cancelled_by_team" not in await notices_of(new.id)


@pytest.mark.asyncio
async def test_a_sent_reply_then_the_leftover_card_rejected_keeps_the_moved_place(
    lead, monkeypatch
):
    await with_details(lead)
    conversation_id = await lead.decide(outcome(status="rescheduled", start=at(17)))
    saving = handle.save_agent_conversation
    monkeypatch.setattr(
        handle, "save_agent_conversation", AsyncMock(side_effect=RuntimeError("db gone"))
    )
    with pytest.raises(RuntimeError):
        await lead.decide_on(conversation_id)
    monkeypatch.setattr(handle, "save_agent_conversation", saving)

    await lead.decide_on(conversation_id, approved=False)

    assert [a.status for a in await places()] == ["cancelled", "booked"]


# ---------------------------------------------------------------------------
# No cancellation text for a move; a real cancellation still has one
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_old_place_is_cancelled_with_no_text_to_the_lead(lead):
    old = await with_details(lead)

    await move(lead)
    for minutes in (1, 2):
        await lead.world.send_pending(lead.world.now + minutes * MINUTE)

    showings, _, notices = await rows()
    kinds = by_kind(notices)
    assert "prospect_cancelled" not in kinds and "prospect_cancelled_by_team" not in kinds
    # Everything else about the cancellation is as for any other.
    (withdrawn,) = kinds["prospect_invite_cancel"]
    assert (withdrawn.attendee_id, withdrawn.recipient) == (old.id, ALEX_EMAIL)
    assert withdrawn.status == "captured"
    (resident,) = kinds["resident_cancelled"]
    assert (resident.showing_id, resident.recipient) == (showings[0].id, RESIDENT)
    (host,) = kinds["host_event_cancel"]
    assert host.showing_id == showings[0].id
    assert [s.status for s in showings] == ["cancelled", "scheduled"]


@pytest.mark.asyncio
async def test_the_host_is_still_texted_when_a_move_leaves_inside_2_hours(lead):
    await with_details(lead)
    lead.world.now = at(14, 30)

    await move(lead, at(16, day=THURSDAY))

    _, _, notices = await rows()
    kinds = by_kind(notices)
    (late,) = kinds["host_late_cancel"]
    assert "Jordan Lee cancelled your showing" in late.payload["body"]
    assert "prospect_cancelled" not in kinds


@pytest.mark.asyncio
async def test_a_move_to_a_time_already_booked_texts_no_cancellation_either(lead):
    """The lead booked the new time through their link; the reply confirms it, and
    the old place goes without a text."""
    await with_details(lead)
    await booked_through_link(lead, at(17))

    conversation_id = await move(lead)

    row, _ = await stored(conversation_id)
    assert row.metadata_["showing_booking"]["action"] == "already_booked"
    old, new = await places()
    assert (old.status, new.status, new.source) == ("cancelled", "booked", "page")
    _, _, notices = await rows()
    assert "prospect_cancelled" not in by_kind(notices)


@pytest.mark.asyncio
@pytest.mark.parametrize("decided", ["reply", "no_reply"])
async def test_a_real_cancellation_still_texts_the_lead(lead, decided):
    old = await with_details(lead)

    conversation_id = await lead.decide(outcome(decided, status="cancelled"))
    if decided == "reply":
        await lead.decide_on(conversation_id)

    (place,) = await places()
    assert (place.status, place.cancel_reason) == ("cancelled", "prospect")
    (text,) = (await notices_of(old.id))["prospect_cancelled"]
    assert text.recipient == ALEX_MOBILE
    assert "your showing on Wed, Apr 14 at 4:00 PM" in text.payload["body"]


@pytest.mark.asyncio
async def test_cancelling_a_moved_showing_texts_the_lead_about_the_new_time(lead):
    await with_details(lead)
    await move(lead)
    await lead.world.send_pending(lead.world.now + MINUTE)

    await lead.decide(outcome("no_reply", status="cancelled"))

    _, new = await places()
    (text,) = (await notices_of(new.id))["prospect_cancelled"]
    assert "your showing on Wed, Apr 14 at 5:00 PM" in text.payload["body"]


# ---------------------------------------------------------------------------
# When the details are not kept
# ---------------------------------------------------------------------------


def assert_provisional(place: ShowingAttendee) -> None:
    assert place.status == "booked" and place.source == "lead_agent"
    assert (place.name, place.mobile, place.email) == ("Jordan Lee", JORDAN_MOBILE, None)
    assert not place.texts_accepted and place.texts_consent_at is None
    assert place.texts_consent_version is None and place.requirement_confirmations == []


@pytest.mark.asyncio
async def test_a_move_from_a_provisional_place_is_as_before(lead):
    await booked_and_told(lead)

    conversation_id = await move(lead)

    old, new = await places()
    assert old.status == "cancelled"
    assert_provisional(new)
    kinds = await notices_of(new.id)
    assert set(kinds) == {"host_event"}  # no text and no invite
    assert all(n.status == "pending" for n in kinds["host_event"])
    row, _ = await stored(conversation_id)
    assert row.metadata_["showing_booking"]["details_kept"] is False
    for minutes in (1, 2):
        await lead.world.send_pending(lead.world.now + minutes * MINUTE)
    assert lead.world.texts.sent == []
    # The reminder by email applies, as for any provisional place.
    report = await lead.world.send_pending(datetime.combine(TUESDAY, time(18), LOCAL))
    assert (report.prospect_reminders, report.lead_email_reminders) == (0, 1)


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", ["confirmation_undelivered", "confirmation_refused"])
async def test_a_place_with_a_wrong_number_moves_as_a_provisional_one(lead, reason):
    old = await with_details(lead)
    await change(old.id, attention_reason=reason)

    await move(lead)

    old, new = await places()
    assert old.status == "cancelled"
    assert_provisional(new)
    assert set(await notices_of(new.id)) == {"host_event"}
    _, _, notices = await rows()
    assert "prospect_cancelled" not in by_kind(notices)


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", ["confirmation_not_sent", "reply_uncertain"])
async def test_another_mark_on_the_old_place_does_not_stop_the_details(lead, reason):
    old = await with_details(lead)
    await change(old.id, attention_reason=reason)

    await move(lead)

    _, new = await places()
    assert (new.mobile, new.email, new.texts_accepted) == (ALEX_MOBILE, ALEX_EMAIL, True)
    assert new.attention_reason is None


@pytest.mark.asyncio
@pytest.mark.parametrize("missing", [{"email": None}, {"texts_accepted": False}])
async def test_details_that_are_not_all_there_are_not_kept(lead, missing):
    old = await with_details(lead)
    await change(old.id, **missing)

    await move(lead)

    assert_provisional((await places())[1])


@pytest.mark.asyncio
async def test_a_place_changed_while_the_new_one_is_booked_holds_the_reply(lead, monkeypatch):
    """The lead corrects their number between the read and the booking: nothing is
    booked or sent with the old one, and the card can be approved again."""
    old = await with_details(lead)
    find_for = resident_notices.find_for

    async def corrected_meanwhile(*args):
        await change(old.id, mobile=ROBIN_MOBILE)
        return await find_for(*args)

    monkeypatch.setattr(resident_notices, "find_for", corrected_meanwhile)
    conversation_id = await lead.decide(outcome(status="rescheduled", start=at(17)))
    sends = handle.send_reply.await_count

    result = await lead.decide_on(conversation_id)

    assert result["status"] == "pending_approval" and handle.send_reply.await_count == sends
    assert lead_bookings.PLACE_CHANGED in (await card_notes(conversation_id))[-1]
    assert [a.status for a in await places()] == ["booked"]

    monkeypatch.setattr(resident_notices, "find_for", find_for)
    result = await lead.decide_on(conversation_id)
    assert result["status"] == "completed"
    assert (await places())[1].mobile == ROBIN_MOBILE


@pytest.mark.asyncio
async def test_a_place_of_an_unsent_reply_gives_no_details_to_the_next_move(lead):
    """A place kept from a move whose reply never went: the lead never heard of it, so
    the next move takes its details, and its text the replaced time, from the one they know."""
    await with_details(lead)
    await stopped_after_booking(lead)

    await move(lead, at(18))

    known, unsent, new = await places()
    assert [a.status for a in (known, unsent, new)] == ["cancelled", "cancelled", "booked"]
    assert new.texts_consent_at == known.texts_consent_at
    (confirmation,) = (await notices_of(new.id))["prospect_confirmation"]
    assert "It replaces your showing on Wed, Apr 14 at 4:00 PM." in confirmation.payload["body"]
    # The unsent place's own text never goes.
    assert {n.status for n in (await notices_of(unsent.id))["prospect_confirmation"]} == {
        "cancelled"
    }


def test_a_reply_is_unsettled_only_for_a_place_it_books_and_until_it_is_sent_or_uncertain():
    now = at(9)
    place = {"source": "lead_agent", "lead_conversation_id": "c1"}

    def unsettled(**fields) -> bool:
        return delivery_checks.reply_unsettled(ShowingAttendee(**{**place, **fields}))

    assert unsettled()
    assert unsettled(texts_accepted=True, texts_consent_at=now)  # details kept from a move
    assert not unsettled(lead_reply_sent_at=now)
    assert not unsettled(lead_reply_uncertain_at=now)
    assert not unsettled(lead_token="AbCdEfGhIjKlMnOpQrStUv")  # completed through the link
    assert not unsettled(source="page", lead_conversation_id=None)
    assert not unsettled(source="team", lead_conversation_id=None)

    sent = ShowingAttendee(**place, mobile=ALEX_MOBILE, texts_accepted=True, lead_reply_sent_at=now)
    unsent = ShowingAttendee(**place, mobile=ALEX_MOBILE, texts_accepted=True)
    assert delivery_checks.can_text(sent) and not delivery_checks.can_text(unsent)


# ---------------------------------------------------------------------------
# The lead's own link once their details are in
# ---------------------------------------------------------------------------


async def read_place(lead: Lead, token: str, unit: str = UNIT):
    async with lead.world.client() as client:
        return await client.post(f"{PUBLIC}/units/{unit}/lead-booking", json={"lead_token": token})


@pytest.mark.asyncio
async def test_the_link_says_they_are_booked_once_the_details_are_in(lead):
    await with_details(lead)
    token = await lead_links.token_for_thread(THREAD)

    answer = await read_place(lead, token)

    assert answer.status_code == 200
    # When and where, and nothing else: no name, number, host or cancel token.
    assert answer.json() == {
        "unit": UNIT,
        "address": "320 Example St",
        "starts_at": "2027-04-14T20:00:00Z",
        "ends_at": "2027-04-14T20:30:00Z",
        "timezone": "America/New_York",
        "details_added": True,
        "texted": False,
    }
    assert answer.headers["cache-control"] == "no-store"


@pytest.mark.asyncio
async def test_the_link_says_the_text_went_only_once_it_did(lead, monkeypatch):
    """Captured: the page must not say that a text with the cancel link was sent."""
    await with_details(lead)
    token = await lead_links.token_for_thread(THREAD)
    assert (await read_place(lead, token)).json()["texted"] is False
    async with AsyncSessionFactory() as session, session.begin():
        await session.execute(
            update(ShowingNotice)
            .where(ShowingNotice.kind == "prospect_confirmation")
            .values(status="sent")
        )

    assert (await read_place(lead, token)).json()["texted"] is True


@pytest.mark.asyncio
async def test_a_place_booked_through_the_link_reads_as_booked_too(lead):
    await booked_through_link(lead, at(16))
    token = await lead_links.token_for_thread(THREAD)

    answer = await read_place(lead, token)

    assert answer.status_code == 200 and answer.json()["details_added"] is True


@pytest.mark.asyncio
async def test_the_link_shows_the_new_time_only_once_the_move_is_sent(lead):
    await with_details(lead)
    token = await lead_links.token_for_thread(THREAD)
    # Earlier than the showing they know of, so it would come first.
    conversation_id = await stopped_after_booking(lead, at(15, 30))
    assert (await read_place(lead, token)).json()["starts_at"] == "2027-04-14T20:00:00Z"

    await lead.decide_on(conversation_id)

    answer = (await read_place(lead, token)).json()
    assert (answer["starts_at"], answer["details_added"]) == ("2027-04-14T19:30:00Z", True)


@pytest.mark.asyncio
async def test_a_provisional_place_still_asks_for_the_details(lead):
    """Also beside a place that has them: the form completes the one without."""
    await booked_and_told(lead)
    token = await lead_links.token_for_thread(THREAD)
    async with lead.world.client() as client:
        robin = booking(
            at(15), name="Robin Lee", mobile="412-555-0118", email=ROBIN_EMAIL, lead_token=token
        )
        assert (await client.post(f"{PUBLIC}/units/{UNIT}/bookings", json=robin)).status_code == 201

    answer = (await read_place(lead, token)).json()
    assert (answer["starts_at"], answer["details_added"]) == ("2027-04-14T20:00:00Z", False)

    async with lead.world.client() as client:
        done = await client.post(DETAILS, json=details(token))
    assert done.status_code == 201, done.text
    assert done.json()["booking"]["starts_at"] == "2027-04-14T20:00:00Z"


@pytest.mark.asyncio
async def test_the_link_shows_the_form_again_after_the_showing_is_cancelled_or_past(lead):
    await with_details(lead)
    token = await lead_links.token_for_thread(THREAD)
    lead.world.now = at(16, 15)  # started
    assert (await read_place(lead, token)).status_code == 404
    lead.world.now = at(9, day=TUESDAY)
    async with lead.world.client() as client:
        place = (await places())[0]
        await client.post(f"{PUBLIC}/bookings/cancel", json={"cancel_token": place.cancel_token})

    answer = await read_place(lead, token)

    assert (answer.status_code, answer.json()["detail"]["code"]) == (404, "no_booking")


@pytest.mark.asyncio
async def test_another_threads_token_another_unit_or_the_switch_off_shows_no_place(lead):
    await with_details(lead)
    mine = await lead_links.token_for_thread(THREAD)
    other = await lead_links.token_for_thread(OTHER_THREAD)
    lead.world.other_listings = [
        lead.world.listing.model_copy(update={"unit": "320-05", "address": "320 Example St"})
    ]

    answers = [
        await read_place(lead, other),
        await read_place(lead, mine, "320-05"),
        await read_place(lead, "x" * 22),
    ]
    await store_settings(lead_agent_uses_open_times=False)
    answers.append(await read_place(lead, mine))

    assert [a.status_code for a in answers] == [404] * 4
    assert {a.json()["detail"]["code"] for a in answers} == {"no_booking"}


@pytest.mark.asyncio
async def test_no_answer_shows_anyone_else_the_host_or_a_cancel_token(lead):
    await with_details(lead)
    async with lead.world.client() as client:
        casey = booking(at(16), name="Casey Park", mobile="412-555-0119", email="casey@example.com")
        assert (await client.post(f"{PUBLIC}/units/{UNIT}/bookings", json=casey)).status_code == 201
    await move(lead)
    token = await lead_links.token_for_thread(THREAD)

    async with lead.world.client() as client:
        read = await client.post(PLACE, json={"lead_token": token})
        again = await client.post(DETAILS, json=details(token))
        unit = await client.get(f"{PUBLIC}/units/{UNIT}")

    assert (read.status_code, again.status_code, unit.status_code) == (200, 409, 200)
    tokens = [a.cancel_token for a in await places()]
    for answer in (read, again, unit):
        private = ["Jane", JANE, "Casey", "0119", "Jordan", "0117", ALEX_EMAIL, RESIDENT, *tokens]
        for word in [*private, "cancel_token"]:
            assert word not in answer.text, (word, answer.text)


# ---------------------------------------------------------------------------
# The lead's own showing is not offered back to them
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_leads_own_showing_is_not_offered_back(lead):
    await booked_through_link(lead, at(16))
    # Someone else's showing at 5, with room.
    async with lead.world.client() as client:
        robin = booking(at(17), name="Robin Lee", mobile="412-555-0118", email=ROBIN_EMAIL)
        assert (await client.post(f"{PUBLIC}/units/{UNIT}/bookings", json=robin)).status_code == 201

    await lead.decide(outcome("no_reply", status="confirmed"))

    (case,) = handle.respond.call_args.args
    assert case.own_showings == [
        OwnShowing(unit=UNIT, start=at(16), status="booked", details_added=True)
    ]
    assert case.booked_tours == [BookedTour(unit=UNIT, start=at(17))]
    starts = {slot.start for slot in case.offerable_slots}
    assert at(16) not in starts and at(17) in starts
    text = render_case(case)
    booked = text.split("## Tours already booked")[1].split("##")[0]
    assert "5:00 PM" in booked and "4:00 PM" not in booked
    own = text.split("## This lead's own showings")[1].split("##")[0]
    assert "Wednesday, April 14 at 4:00 PM, booked, details added" in own
    # The automatic-send check still lets a reply state it.
    assert at(16) in allowed_tour_times(case, UNIT)
    reply = outcome(offered_slots=[at(16).isoformat()])
    assert reply_problems(case, reply) == []
    assert reply_problems(case, outcome(offered_slots=[at(19).isoformat()])) != []

    # Another lead is offered it as before.
    await lead.decide(outcome("no_reply", status="none"), OTHER_THREAD)
    (other,) = handle.respond.call_args.args
    assert BookedTour(unit=UNIT, start=at(16)) in other.booked_tours


@pytest.mark.asyncio
async def test_with_the_switch_off_nothing_is_left_out(lead):
    await booked_through_link(lead, at(16))
    await store_settings(lead_agent_uses_open_times=False)

    await lead.decide(outcome("no_reply", status="none"))

    (case,) = handle.respond.call_args.args
    assert case.own_showings is None and case.booked_tours == [] and case.offerable_slots == []


def own_case(own: list[OwnShowing] | None):
    from api.src.tests.test_zillow_lead_bookings import lead_thread

    return build_case(
        lead_thread(THREAD),
        ListingData(
            timezone="America/New_York",
            listings=[Listing(unit=UNIT), Listing(unit="412-01")],
        ),
        now=at(9, day=TUESDAY),
        slots=[
            TourSlot(start=at(16), unit=UNIT),
            TourSlot(start=at(16), unit="412-01"),
            TourSlot(start=at(17), unit=UNIT),
        ],
        booked_tours=[
            BookedTour(unit=UNIT, start=at(16)),
            BookedTour(unit="412-01", start=at(16)),
            BookedTour(unit=UNIT, start=at(17)),
        ],
        own_showings=own,
    )


def test_only_the_leads_booked_time_at_that_unit_is_left_out():
    mine = OwnShowing(unit=UNIT.lower(), start=at(16), status="booked")
    case = own_case([mine])
    assert [(s.unit, s.start) for s in case.offerable_slots] == [
        ("412-01", at(16)),
        (UNIT, at(17)),
    ]
    assert [(t.unit, t.start) for t in case.booked_tours] == [("412-01", at(16)), (UNIT, at(17))]
    assert case.own_showings == [mine]

    # A cancelled showing's time may be offered again, and with none nothing changes.
    cancelled = mine.model_copy(update={"status": "cancelled", "cancelled_by": "team"})
    for own in ([cancelled], [], None):
        full = own_case(own)
        assert len(full.offerable_slots) == 3 and len(full.booked_tours) == 3


def test_what_a_reply_may_offer_does_not_depend_on_where_the_own_showing_is_listed():
    mine = OwnShowing(unit=UNIT, start=at(16), status="booked")
    case = own_case([mine])
    listed = case.model_copy(
        update={
            "offerable_slots": [*case.offerable_slots, TourSlot(start=at(16), unit=UNIT)],
            "booked_tours": [*case.booked_tours, BookedTour(unit=UNIT, start=at(16))],
        }
    )
    for unit in (UNIT, "412-01", None, "Somewhere else"):
        assert allowed_tour_times(case, unit) == allowed_tour_times(listed, unit)


@pytest.mark.asyncio
async def test_the_card_of_a_held_move_is_unchanged(lead):
    """What the approver reads before a move is the same sentence, with details or not."""
    await with_details(lead)
    open_times.forget_free_busy()

    conversation_id = await lead.decide(outcome(status="rescheduled", start=at(17)), auto=True)

    (card,) = await pending_cards(conversation_id)
    assert card["args"]["team_notes"] == [
        "Sending this moves their showing of 320-03 from Wed, Apr 14 at 4:00 PM "
        "to Wed, Apr 14 at 5:00 PM.",
        "This reply moves a showing, so a person sends it.",
    ]
