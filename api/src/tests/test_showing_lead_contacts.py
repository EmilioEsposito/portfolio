"""Quo Lead contacts for the people we have a mobile for (``lead_contacts.py``).

Quo is a stand-in throughout (``StandInLeadContacts``, or ``httpx.MockTransport``
for the live writer's own answers). Names and numbers are made up.
"""

import json
from datetime import timedelta

import httpx
import pytest
from sqlalchemy import select, update

from api.src.database.database import AsyncSessionFactory
from api.src.open_phone.service import invalidate_contact_cache
from api.src.sernia_ai.config import QUO_INTERNAL_COMPANY
from api.src.showing_bookings import lead_contacts, quo_lead_contacts, residents
from api.src.showing_bookings.fakes import StandInContacts, contact
from api.src.showing_bookings.lead_contacts import (
    PROPERTY_FIELD,
    TAGS_FIELD,
    UNIT_FIELD,
    Claimed,
    StandInLeadContacts,
    filled_in,
    new_contact,
    person_of,
)
from api.src.showing_bookings.models import LeadContactJob, ShowingAttendee
from api.src.showing_bookings.quo_lead_contacts import ContactsUnavailable, QuoLeadContacts
from api.src.tests.showing_public import ALEX_EMAIL, ALEX_MOBILE, ROBIN_MOBILE, UNIT, booking
from api.src.tests.test_showing_reminders import book, live
from api.src.tests.test_showing_reminders import world as reminder_world
from api.src.tests.test_zillow_lead_handle import conversation_id as lead_conversation_id

world = reminder_world
conversation_id = lead_conversation_id

PHONE = "+14125550131"
THREAD = "thread-lead-contacts-1"


def claimed(**fields) -> Claimed:
    base = dict(
        id=1,
        attempts=1,
        phone=PHONE,
        name="Quinn Example",
        email="quinn@example.com",
        unit="324-11",
        zillow_thread_id=THREAD,
    )
    return Claimed(**(base | fields))


def custom(payload: dict) -> dict:
    return {entry["key"]: entry["value"] for entry in payload["customFields"]}


def lead(**default) -> dict:
    """An existing Lead contact as Quo answers it."""
    return {
        "id": "CTlead",
        "createdAt": "2026-06-02T12:53:06.209Z",
        "defaultFields": {
            "firstName": "Lead 320-02 Quinn",
            "lastName": ".",
            "company": "320",
            "role": "Lead",
            "emails": [],
            "phoneNumbers": [{"id": "PH1", "name": "mobile", "value": PHONE}],
            **default,
        },
        "customFields": [
            {"id": "CF1", "key": PROPERTY_FIELD, "name": "Property", "value": "320"},
            {"id": "CF2", "key": UNIT_FIELD, "name": "Unit #", "value": "02"},
            {"id": "CF3", "key": TAGS_FIELD, "name": "Tags", "value": ["320"]},
        ],
    }


# ---------------------------------------------------------------------------
# The contact, by the quo-contact skill
# ---------------------------------------------------------------------------


def test_a_new_lead_contact_is_written_as_the_skill_says():
    payload = new_contact(person_of(claimed()))

    assert payload["defaultFields"] == {
        "firstName": "Lead 324-11 Quinn",
        "lastName": "Example",
        "role": "Lead",
        "company": "324",
        "phoneNumbers": [{"name": "mobile", "value": PHONE}],
        "emails": [{"name": "Email", "value": "quinn@example.com"}],
    }
    assert custom(payload) == {
        PROPERTY_FIELD: "324",
        UNIT_FIELD: "11",
        TAGS_FIELD: ["Zillow Lead", "324"],
    }
    assert "externalId" not in json.dumps(payload)


def test_what_the_booking_does_not_say_is_left_out_or_marked():
    person = person_of(
        claimed(
            name="  quinn  ", email="lead+abc@convo.zillow.com", unit="324-3", zillow_thread_id=None
        )
    )
    payload = new_contact(person)

    assert payload["defaultFields"]["firstName"] == "Lead 324-03 quinn"
    assert payload["defaultFields"]["lastName"] == "."
    assert "emails" not in payload["defaultFields"]  # never Zillow's relay address
    assert custom(payload)[TAGS_FIELD] == ["324"]  # not from a Zillow thread
    assert person_of(claimed(phone="+442071838750")) is None  # not a US mobile
    assert person_of(claimed(unit="not a unit")) is None


def test_an_existing_lead_gets_only_what_it_lacks_and_keeps_every_list():
    payload = filled_in(lead(), person_of(claimed()))

    default = payload["defaultFields"]
    assert default["firstName"] == "Lead 320-02 Quinn"  # never renamed
    assert (default["lastName"], default["company"], default["role"]) == ("Example", "320", "Lead")
    assert default["emails"] == [{"name": "Email", "value": "quinn@example.com"}]
    assert default["phoneNumbers"] == [{"id": "PH1", "name": "mobile", "value": PHONE}]
    assert payload["customFields"] == [
        {"id": "CF1", "key": PROPERTY_FIELD, "value": "320"},
        {"id": "CF2", "key": UNIT_FIELD, "value": "02"},  # its unit is never changed
        {"id": "CF3", "key": TAGS_FIELD, "value": ["320", "Zillow Lead", "324"]},
    ]


def test_a_lead_that_lacks_nothing_is_not_written():
    complete = lead(
        lastName="Example", emails=[{"id": "EM1", "name": "Email", "value": "other@example.com"}]
    )
    complete["customFields"][2]["value"] = ["Zillow Lead", "324", "320"]

    assert filled_in(complete, person_of(claimed())) is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "role, company",
    [
        ("Tenant", "324"),
        ("Prior Tenant", ""),
        ("Vendor", "Acme Plumbing"),
        ("Partner", "Example LLC"),
        (None, ""),  # someone already in Quo, with no role
        ("Property Manager", QUO_INTERNAL_COMPANY),  # staff
    ],
)
async def test_a_number_held_by_anyone_but_a_lead_is_left_alone(role, company):
    store = StandInLeadContacts()
    other = contact("Pat Example", PHONE, prop="324", unit="11", company=company, role=role)
    store.add(other)
    store.add(lead())

    synced = await lead_contacts.sync(person_of(claimed()), store)

    assert synced == lead_contacts.Synced("left_alone", None)
    assert (store.created, store.updated) == ([], [])


@pytest.mark.asyncio
async def test_a_new_number_is_made_and_a_second_time_finds_it():
    store = StandInLeadContacts()
    person = person_of(claimed())

    first = await lead_contacts.sync(person, store)
    again = await lead_contacts.sync(person, store)

    assert first.result == "created" and again == lead_contacts.Synced(
        "unchanged", first.contact_id
    )
    assert store.contacts[first.contact_id]["defaultFields"]["firstName"] == "Lead 324-11 Quinn"


@pytest.mark.asyncio
async def test_an_existing_lead_is_filled_in_not_duplicated():
    store = StandInLeadContacts()
    store.add(lead())

    synced = await lead_contacts.sync(person_of(claimed()), store)

    assert synced == lead_contacts.Synced("updated", "CTlead")
    assert store.created == []
    assert store.contacts["CTlead"]["defaultFields"]["lastName"] == "Example"


# ---------------------------------------------------------------------------
# Jobs
# ---------------------------------------------------------------------------


async def jobs() -> list[LeadContactJob]:
    async with AsyncSessionFactory() as session:
        return list(await session.scalars(select(LeadContactJob).order_by(LeadContactJob.id)))


@pytest.mark.asyncio
async def test_a_booking_with_a_mobile_becomes_a_lead_contact_once(world):
    await live()
    await book(world)
    store = StandInLeadContacts()

    assert await lead_contacts.queue_bookings(now=world.now) == 1
    assert await lead_contacts.queue_bookings(now=world.now) == 0
    (job,) = await jobs()
    assert (job.phone, job.unit, job.status) == (ALEX_MOBILE, UNIT, "pending")
    assert job.key == f"attendee:{job.attendee_id}:{lead_contacts.digest(ALEX_MOBILE)}"

    # It waits for the confirmation text's report first.
    assert (await lead_contacts.run_due(now=world.now, store=store)).total == 0
    report = await lead_contacts.run_due(now=world.now + lead_contacts.SETTLE, store=store)

    assert report.created == 1
    (made,) = store.contacts.values()
    assert made["defaultFields"]["firstName"] == "Lead 320-03 Alex"
    assert made["defaultFields"]["emails"] == [{"name": "Email", "value": ALEX_EMAIL}]
    (job,) = await jobs()
    assert (job.status, job.result, job.contact_id) == ("done", "created", made["id"])
    # The person's details are not kept once the job is done.
    assert (job.phone, job.name, job.email) == (None, None, None)
    assert await lead_contacts.queue_bookings(now=world.now) == 0


@pytest.mark.asyncio
async def test_a_number_found_wrong_or_changed_is_skipped_and_the_new_one_queued(world):
    await live()
    await book(world)
    await book(world, booking(name="Robin Lee", mobile=ROBIN_MOBILE, email="robin@example.com"))
    await lead_contacts.queue_bookings(now=world.now)
    async with AsyncSessionFactory() as session:
        alex, robin = await session.scalars(select(ShowingAttendee).order_by(ShowingAttendee.id))
        await session.execute(
            update(ShowingAttendee)
            .where(ShowingAttendee.id == alex.id)
            .values(attention_reason="confirmation_undelivered")
        )
        await session.execute(
            update(ShowingAttendee)
            .where(ShowingAttendee.id == robin.id)
            .values(mobile="+14125550132")
        )
        await session.commit()
    store = StandInLeadContacts()

    report = await lead_contacts.run_due(now=world.now + lead_contacts.SETTLE, store=store)

    assert (report.skipped, store.created) == (2, [])
    assert await lead_contacts.queue_bookings(now=world.now) == 1  # Robin's new number
    report = await lead_contacts.run_due(now=world.now + 2 * lead_contacts.SETTLE, store=store)
    assert report.created == 1


@pytest.mark.asyncio
async def test_quo_out_of_reach_is_tried_again_and_a_refusal_fails_without_touching_the_booking(
    world,
):
    await live()
    await book(world)
    await lead_contacts.queue_bookings(now=world.now)
    store = StandInLeadContacts()
    store.unavailable = "out of reach"
    due = world.now + lead_contacts.SETTLE

    report = await lead_contacts.run_due(now=due, store=store)

    (job,) = await jobs()
    assert (report.retrying, job.status, job.attempts) == (1, "pending", 1)
    assert job.next_attempt_at == due + timedelta(minutes=2)
    assert job.phone == ALEX_MOBILE  # still needed for the next try

    store.unavailable = "refused"
    await lead_contacts.run_due(now=job.next_attempt_at, store=store)
    (job,) = await jobs()
    assert (job.status, job.attempts, job.phone) == ("failed", 2, None)
    assert "refused" in job.last_error
    async with AsyncSessionFactory() as session:
        (attendee,) = await session.scalars(select(ShowingAttendee))
    assert (attendee.status, attendee.mobile) == ("booked", ALEX_MOBILE)


@pytest.mark.asyncio
async def test_retries_stop_after_the_last_attempt(world):
    await live()
    await book(world)
    await lead_contacts.queue_bookings(now=world.now)
    store = StandInLeadContacts()
    store.unavailable = "out of reach"
    now = world.now + lead_contacts.SETTLE
    for _ in range(lead_contacts.MAX_ATTEMPTS):
        await lead_contacts.run_due(now=now, store=store)
        (job,) = await jobs()
        now = job.next_attempt_at

    assert (job.status, job.attempts) == ("failed", lead_contacts.MAX_ATTEMPTS)


@pytest.mark.asyncio
async def test_a_showing_long_past_is_not_queued(world):
    await live()
    await book(world)
    later = world.now + timedelta(days=2) + lead_contacts.LOOKBACK
    assert await lead_contacts.queue_bookings(now=later) == 0


@pytest.mark.asyncio
async def test_a_zillow_lead_with_a_number_is_queued_once_with_no_email(world):
    for _ in range(2):  # the lead agent runs on each new email in the thread
        await lead_contacts.queue_zillow_lead(
            THREAD, name="Quinn Example", phone="(412) 555-0131", unit="324-11", now=world.now
        )
    await lead_contacts.queue_zillow_lead(
        "thread-2", name="No Number", phone=None, unit="324-11", now=world.now
    )
    await lead_contacts.queue_zillow_lead(
        "thread-3", name="No Unit", phone=PHONE, unit=None, now=world.now
    )
    store = StandInLeadContacts()

    report = await lead_contacts.run_due(now=world.now, store=store)

    assert report.created == 1
    (made,) = store.contacts.values()
    assert made["defaultFields"]["firstName"] == "Lead 324-11 Quinn"
    assert "emails" not in made["defaultFields"]
    assert custom(made)[TAGS_FIELD] == ["Zillow Lead", "324"]
    (job,) = await jobs()
    assert job.key == f"thread:{THREAD}:{lead_contacts.digest(PHONE)}"


@pytest.mark.asyncio
async def test_queuing_a_zillow_lead_never_raises(monkeypatch):
    def broken():
        raise RuntimeError("database down")

    monkeypatch.setattr(lead_contacts, "AsyncSessionFactory", broken)
    await lead_contacts.queue_zillow_lead(THREAD, name="J", phone=PHONE, unit="324-11", now=None)


def test_outside_production_contacts_go_to_the_stand_in(monkeypatch):
    monkeypatch.delenv("RAILWAY_ENVIRONMENT_NAME", raising=False)
    assert isinstance(lead_contacts.contact_store(), StandInLeadContacts)
    monkeypatch.setenv("RAILWAY_ENVIRONMENT_NAME", "portfolio-pr-480")
    assert isinstance(lead_contacts.contact_store(), StandInLeadContacts)
    monkeypatch.setenv("RAILWAY_ENVIRONMENT_NAME", "production")
    assert isinstance(lead_contacts.contact_store(), QuoLeadContacts)


# ---------------------------------------------------------------------------
# The live writer's reading of Quo's answers
# ---------------------------------------------------------------------------


class Quo:
    def __init__(self, *answers: httpx.Response | Exception):
        self.answers = list(answers)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url="https://api.openphone.com", transport=httpx.MockTransport(self)
        )


@pytest.mark.asyncio
async def test_the_writer_creates_and_updates_as_quo_documents(monkeypatch):
    quo = Quo(
        httpx.Response(201, json={"data": {"id": "CTnew"}}),
        httpx.Response(200, json={"data": {"id": "CTnew"}}),
    )
    monkeypatch.setattr(quo_lead_contacts, "_client", quo.client)
    payload = new_contact(person_of(claimed()))

    assert await QuoLeadContacts().create(payload) == "CTnew"
    await QuoLeadContacts().update("CTnew", payload)

    create, change = quo.requests
    assert (create.method, create.url.path) == ("POST", "/v1/contacts")
    assert (change.method, change.url.path) == ("PATCH", "/v1/contacts/CTnew")
    assert json.loads(create.content) == payload


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "answer, retryable",
    [
        (httpx.Response(429, json={"message": "Too many requests"}), True),
        (httpx.Response(503, text="unavailable"), True),
        (httpx.ConnectError("no route"), True),
        (httpx.ReadTimeout("no answer"), True),
        (httpx.Response(400, json={"message": "Invalid custom field"}), False),
    ],
)
async def test_the_writer_says_when_trying_later_may_work(monkeypatch, answer, retryable):
    monkeypatch.setattr(quo_lead_contacts, "_client", Quo(answer).client)
    with pytest.raises(ContactsUnavailable) as refused:
        await QuoLeadContacts().create({"defaultFields": {}, "customFields": []})
    assert refused.value.retryable is retryable


@pytest.mark.asyncio
async def test_the_writer_looks_a_number_up_afresh(monkeypatch):
    from api.src.open_phone import service

    held = contact("Quinn Example", PHONE, prop="324", unit="11", role="Lead")
    quo = Quo(
        httpx.Response(200, json={"data": [held]}),
        httpx.Response(200, json={"data": [held]}),
    )
    monkeypatch.setattr(quo_lead_contacts, "_client", quo.client)
    monkeypatch.setattr(service, "_contact_cache", [], raising=False)
    try:
        assert await QuoLeadContacts().with_phone(PHONE) == [held]
        assert await QuoLeadContacts().with_phone(PHONE) == [held]
        assert len(quo.requests) == 2  # never the five-minute cache
    finally:
        invalidate_contact_cache()


def test_the_live_writer_refuses_to_run_in_tests():
    with pytest.raises(quo_lead_contacts.LiveQuoInTests):
        quo_lead_contacts._client()


# ---------------------------------------------------------------------------
# The resident lookup: a contact that names a unit without living there
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("role", ["Lead", "Prior Tenant", "Vendor", "Partner", "lead"])
def test_a_lead_or_other_non_resident_without_lease_dates_leaves_a_unit_vacant(role):
    contacts = StandInContacts()
    contacts.add("Lead 320-03 Quinn", PHONE, unit=UNIT, lease_start=None, lease_end=None, role=role)

    assert residents.residents_in(contacts.added, UNIT, world_day()) == residents.Found()


@pytest.mark.parametrize("role", [None, "", "Tenant"])
def test_anyone_else_without_lease_dates_is_still_a_problem(role):
    contacts = StandInContacts()
    contacts.add("Morgan Ellis", PHONE, unit=UNIT, lease_start=None, lease_end=None, role=role)

    found = residents.residents_in(contacts.added, UNIT, world_day())

    assert found == residents.Found(problem="lease_dates_missing")


def test_a_lead_beside_a_current_tenant_changes_nothing():
    contacts = StandInContacts()
    contacts.add("Lead 320-03 Quinn", PHONE, unit=UNIT, lease_start=None, role="Lead")
    contacts.add("Riley Chen", ALEX_MOBILE, unit=UNIT)

    found = residents.residents_in(contacts.added, UNIT, world_day())

    assert found == residents.Found(mobiles=(ALEX_MOBILE,))


def world_day():
    from api.src.tests.showing_public import WEDNESDAY

    return WEDNESDAY


# ---------------------------------------------------------------------------
# The lead agent's outcome
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("phone, queued", [("412-555-0131", True), (None, False)])
async def test_the_lead_agents_outcome_with_a_number_queues_its_contact(
    conversation_id, monkeypatch, phone, queued
):
    from unittest.mock import AsyncMock

    from api.src.tests.showing_helpers import clear_showing_data
    from api.src.tests.test_zillow_lead_handle import message, outcome, thread
    from api.src.zillow_leads import handle

    await clear_showing_data()
    lead = {"name": "Quinn Example", "phone": phone, "unit": "324-11"}
    monkeypatch.setattr(handle, "load_lead_thread", AsyncMock(return_value=thread(message(0))))
    monkeypatch.setattr(handle, "respond", AsyncMock(return_value=outcome(lead=lead)))
    try:
        await handle.handle_thread("T", conversation_id=conversation_id, metadata={})
        keys = [job.key for job in await jobs()]
        assert keys == ([f"thread:T:{lead_contacts.digest(PHONE)}"] if queued else [])
    finally:
        await clear_showing_data()


class LaggingQuo(StandInLeadContacts):
    """Quo whose list does not show a contact made a moment ago."""

    async def with_phone(self, phone: str) -> list[dict]:
        return []


@pytest.mark.asyncio
async def test_a_second_job_for_a_number_uses_the_contact_the_first_made(world):
    await live()
    await book(world)
    for thread_id in (THREAD, "thread-2"):
        await lead_contacts.queue_zillow_lead(
            thread_id, name="Alex Parker", phone=ALEX_MOBILE, unit=UNIT, now=world.now
        )
    store = LaggingQuo()

    await lead_contacts.queue_bookings(now=world.now)
    report = await lead_contacts.run_due(now=world.now + lead_contacts.SETTLE, store=store)

    assert (report.created, len(store.contacts)) == (1, 1)
    assert {job.contact_id for job in await jobs()} == set(store.created)
