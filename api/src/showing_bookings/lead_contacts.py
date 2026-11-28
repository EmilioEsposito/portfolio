"""A Quo Lead contact for each person whose mobile we have.

Two sources, each queued as a job in ``lead_contact_jobs``:

- a booking with a mobile: booked on the page, by the team, or a lead adding
  their details, and a corrected mobile too. ``queue_bookings`` finds every
  attendee of a showing from 7 days ago on whose mobile has no job yet, so the
  bookings made before this existed are queued on its first run. The contact
  is for the person whose number it is, whoever booked.
- a Zillow lead whose thread gave a phone number: the lead agent's outcome
  (``queue_zillow_lead``, called from ``zillow_leads/handle.py``), with the
  name and unit the thread gave. No email: a lead's address in the thread is
  Zillow's relay, never stored.

Each contact follows the team's ``quo-contact`` skill (sernia-knowledge,
``.claude/skills/quo-contact/SKILL.md``):

- first name ``Lead 324-11 Jonah``, last name the rest of the name or ``.``;
  role ``Lead``; company the property's street number (``324``);
- the mobile in E.164 labelled ``mobile``; an email labelled ``Email``;
- the Property and Unit # custom fields; tags ``324``, and ``Zillow Lead`` when
  the person came from a Zillow thread; never the external id.

How a job is worked (``sync``), the number looked up in Quo first:

- any contact holding the number that is not a Lead (a Tenant, Prior Tenant,
  Vendor, Partner, staff, or one with no role) is left alone, and none is made;
- a Lead holding it only gets what it lacks: an email when it has none, a last
  name where it has none or ``.``, and the tags. It is never renamed and its
  unit is never changed;
- otherwise a new Lead contact is made.

A booking job waits ``SETTLE`` after it is queued, and is skipped when the
booking's confirmation text found the number wrong
(``delivery_checks.WRONG_NUMBER``) or its mobile has changed since (the new one
has its own job). Quo out of reach, rate limited or failing is tried again
later, up to ``MAX_ATTEMPTS``; any other refusal fails at once. A job never
touches the booking, and a failure is logged, never raised. A job's phone, name
and email are emptied once it is done or has failed.

``scheduled_run`` is the job every minute, registered at startup only when
background automation is on. Outside production the writes go to an in-memory
stand-in (``contact_store``), so nothing reaches the real Quo; tests install
their own.
"""

import hashlib
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Protocol

import logfire
from sqlalchemy import String, cast, exists, func, literal, select, update
from sqlalchemy.dialects.postgresql import insert

from api.src.database.database import AsyncSessionFactory
from api.src.schedulers.job_info import JobInfo
from api.src.showing_bookings import residents
from api.src.showing_bookings.delivery_checks import WRONG_NUMBER
from api.src.showing_bookings.models import LeadContactJob, Showing, ShowingAttendee
from api.src.showing_bookings.quo_lead_contacts import ContactsUnavailable, QuoLeadContacts
from api.src.showing_bookings.schema import normalize_email, normalize_us_mobile
from api.src.showing_bookings.settings import current_environment

JOB_ID = "quo-lead-contacts"
MAX_ATTEMPTS = 5
RUN_LIMIT = 20
# A booking is worked this long after it is queued, so a confirmation text that
# finds the number wrong usually stops it first.
SETTLE = timedelta(minutes=10)
# While a run works a job, no other run takes it.
LEASE = timedelta(minutes=10)
# Bookings for showings from this long ago on are queued.
LOOKBACK = timedelta(days=7)

ROLE = "Lead"
ZILLOW_TAG = "Zillow Lead"
RELAY_DOMAIN = "@convo.zillow.com"
# The team's Quo custom fields (the quo-contact skill's keys).
PROPERTY_FIELD = "67a69fc2ea4fe3a7edd09276"
UNIT_FIELD = "67e1f12cd6d6910515ec7ca2"
TAGS_FIELD = "6827a195fe60ba0130f30b92"


class LeadContactStore(Protocol):
    async def with_phone(self, phone: str) -> list[dict]:
        """Every contact holding ``phone``, as Quo has them now."""
        ...

    async def get(self, contact_id: str) -> dict:
        """One contact as Quo has it now."""
        ...

    async def create(self, payload: dict) -> str:
        """Make a contact from ``defaultFields`` and ``customFields``; its id."""
        ...

    async def update(self, contact_id: str, payload: dict) -> None:
        """Replace the contact's fields with ``payload``'s, lists whole."""
        ...


class StandInLeadContacts:
    """A ``LeadContactStore`` in memory, outside production and in tests.

    Holds contacts as Quo answers them. ``unavailable`` makes the next calls raise
    ``ContactsUnavailable`` (retryable unless "refused").
    """

    def __init__(self) -> None:
        self.contacts: dict[str, dict] = {}
        self.created: list[str] = []
        self.updated: list[str] = []
        self.unavailable: str | None = None

    def add(self, contact: dict) -> str:
        contact_id = contact.get("id") or f"stand-in-{len(self.contacts) + 1}"
        self.contacts[contact_id] = {**contact, "id": contact_id}
        return contact_id

    def _fail(self) -> None:
        if self.unavailable:
            raise ContactsUnavailable(
                f"Stand-in Quo is {self.unavailable}.", retryable=self.unavailable != "refused"
            )

    async def with_phone(self, phone: str) -> list[dict]:
        self._fail()
        return [
            contact
            for contact in self.contacts.values()
            if any(
                entry.get("value") == phone
                for entry in contact.get("defaultFields", {}).get("phoneNumbers") or []
            )
        ]

    async def get(self, contact_id: str) -> dict:
        self._fail()
        return self.contacts[contact_id]

    async def create(self, payload: dict) -> str:
        self._fail()
        contact_id = self.add(_as_quo_answers(payload))
        self.created.append(contact_id)
        return contact_id

    async def update(self, contact_id: str, payload: dict) -> None:
        self._fail()
        self.contacts[contact_id] = {**_as_quo_answers(payload), "id": contact_id}
        self.updated.append(contact_id)


def _as_quo_answers(payload: dict) -> dict:
    names = {PROPERTY_FIELD: "Property", UNIT_FIELD: "Unit #", TAGS_FIELD: "Tags"}
    return {
        "defaultFields": dict(payload.get("defaultFields") or {}),
        "customFields": [
            {**entry, "name": names.get(entry["key"], entry.get("name"))}
            for entry in payload.get("customFields") or []
        ],
    }


_STAND_IN = StandInLeadContacts()


def contact_store() -> LeadContactStore:
    """Quo in production; the in-memory stand-in everywhere else. Tests replace it."""
    if current_environment() == "production":
        return QuoLeadContacts()
    return _STAND_IN


# ---------------------------------------------------------------------------
# The contact, by the quo-contact skill
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Person:
    """Who a Lead contact is for, as the skill writes them."""

    phone: str
    first: str
    last: str
    email: str | None
    property: str
    unit: str
    tags: tuple[str, ...] = field(default_factory=tuple)

    @property
    def first_name(self) -> str:
        return f"Lead {self.property}-{self.unit} {self.first}".strip()


@dataclass(frozen=True)
class Claimed:
    """A job taken for working, with what it needs."""

    id: int
    attempts: int
    phone: str | None
    name: str | None
    email: str | None
    unit: str | None
    zillow_thread_id: str | None
    # A booking whose number was found wrong, or has changed since it was queued.
    stale: bool = False


def person_of(job: Claimed) -> Person | None:
    """The job's person, or None when its number or unit does not read."""
    phone = normalize_us_mobile(job.phone or "")
    parts = residents.split_unit(job.unit or "")
    if phone is None or parts is None:
        return None
    prop, number = parts
    if number.isdigit():
        number = number.zfill(2)
    first, _, rest = " ".join((job.name or "").split()).partition(" ")
    email = normalize_email(job.email or "")
    if email and email.endswith(RELAY_DOMAIN):
        email = None
    tags = (ZILLOW_TAG, prop) if job.zillow_thread_id else (prop,)
    return Person(phone, first, rest or ".", email, prop, number, tags)


def new_contact(person: Person) -> dict:
    """The payload of a new Lead contact."""
    default = {
        "firstName": person.first_name,
        "lastName": person.last,
        "role": ROLE,
        "company": person.property,
        "phoneNumbers": [{"name": "mobile", "value": person.phone}],
    }
    if person.email:
        default["emails"] = [{"name": "Email", "value": person.email}]
    return {
        "defaultFields": default,
        "customFields": [
            {"key": PROPERTY_FIELD, "value": person.property},
            {"key": UNIT_FIELD, "value": person.unit},
            {"key": TAGS_FIELD, "value": list(person.tags)},
        ],
    }


def _entries(items: list[dict] | None) -> list[dict]:
    """A list's entries as Quo takes them back: kept whole, with their ids."""
    return [
        {name: entry[name] for name in ("id", "name", "value") if name in entry}
        for entry in items or []
    ]


def filled_in(contact: dict, person: Person) -> dict | None:
    """The payload that gives an existing Lead what it lacks, or None when it lacks
    nothing. Every list goes back whole: Quo's update replaces them."""
    default = contact.get("defaultFields") or {}
    emails = _entries(default.get("emails"))
    custom = [
        {name: entry[name] for name in ("id", "key", "value") if name in entry}
        for entry in contact.get("customFields") or []
    ]
    changed = False
    last = default.get("lastName")
    if (last or "").strip() in ("", ".") and person.last != ".":
        last, changed = person.last, True
    if person.email and not any(entry.get("value") for entry in emails):
        emails.append({"name": "Email", "value": person.email})
        changed = True
    tags = next((entry for entry in custom if entry.get("key") == TAGS_FIELD), None)
    if tags is None:
        tags = {"key": TAGS_FIELD, "value": []}
        custom.append(tags)
    current = list(tags.get("value") or [])
    missing = [tag for tag in person.tags if tag not in current]
    if missing:
        tags["value"], changed = current + missing, True
    if not changed:
        return None
    if not tags["value"]:
        custom.remove(tags)
    kept = {
        name: default[name]
        for name in ("firstName", "company", "role")
        if default.get(name) is not None
    }
    return {
        "defaultFields": {
            **kept,
            "lastName": last,
            "emails": emails,
            "phoneNumbers": _entries(default.get("phoneNumbers")),
        },
        "customFields": custom,
    }


@dataclass(frozen=True)
class Synced:
    result: str  # "created", "updated", "unchanged" or "left_alone"
    contact_id: str | None


async def sync(person: Person, store: LeadContactStore, *, known: str | None = None) -> Synced:
    """Create or fill in ``person``'s Lead contact. Raises ``ContactsUnavailable``.

    ``known`` is a contact an earlier job made or found for this number: used when
    Quo's list does not show the number yet, so a contact is never made twice.
    """
    holding = await store.with_phone(person.phone)
    if not holding and known:
        try:
            earlier = await store.get(known)
        except ContactsUnavailable as exc:
            if exc.retryable:
                raise
            earlier = None  # deleted since: make it again
        numbers = (earlier or {}).get("defaultFields", {}).get("phoneNumbers") or []
        if any(entry.get("value") == person.phone for entry in numbers):
            holding = [{**earlier, "id": known}]
    if any(
        ((contact.get("defaultFields") or {}).get("role") or "").strip().casefold()
        != ROLE.casefold()
        for contact in holding
    ):
        return Synced("left_alone", None)
    if holding:
        lead = sorted(holding, key=lambda contact: contact.get("createdAt") or "")[0]
        fresh = await store.get(lead["id"])
        payload = filled_in(fresh, person)
        if payload is None:
            return Synced("unchanged", lead["id"])
        await store.update(lead["id"], payload)
        return Synced("updated", lead["id"])
    return Synced("created", await store.create(new_contact(person)))


# ---------------------------------------------------------------------------
# Jobs
# ---------------------------------------------------------------------------


def digest(phone: str) -> str:
    """A short digest of a number for a job's key, as Postgres's ``md5`` gives it."""
    return hashlib.md5(phone.encode()).hexdigest()[:16]


async def queue_bookings(*, now: datetime) -> int:
    """Queue every attendee of a showing from ``LOOKBACK`` ago on whose mobile has no
    job yet. Returns how many were queued."""
    key = (
        literal("attendee:")
        + cast(ShowingAttendee.id, String)
        + literal(":")
        + func.left(func.md5(ShowingAttendee.mobile), 16)
    )
    rows = (
        select(
            key,
            ShowingAttendee.id,
            ShowingAttendee.zillow_thread_id,
            Showing.unit,
            Showing.starts_at,
            ShowingAttendee.mobile,
            ShowingAttendee.name,
            ShowingAttendee.email,
            literal(now + SETTLE),
            literal(now),
        )
        .join(Showing, Showing.id == ShowingAttendee.showing_id)
        .where(
            ShowingAttendee.mobile.is_not(None),
            Showing.starts_at >= now - LOOKBACK,
            ~exists().where(LeadContactJob.key == key),
        )
    )
    statement = (
        insert(LeadContactJob)
        .from_select(
            [
                "key",
                "attendee_id",
                "zillow_thread_id",
                "unit",
                "starts_at",
                "phone",
                "name",
                "email",
                "next_attempt_at",
                "created_at",
            ],
            rows,
        )
        .on_conflict_do_nothing(index_elements=["key"])
        .returning(LeadContactJob.id)
    )
    async with AsyncSessionFactory() as session, session.begin():
        queued = len((await session.execute(statement)).all())
    if queued:
        logfire.info("lead contacts: bookings queued", count=queued)
    return queued


async def queue_zillow_lead(
    thread_id: str, *, name: str | None, phone: str | None, unit: str | None, now: datetime
) -> None:
    """Queue a Zillow lead's contact from the lead agent's outcome. Never raises."""
    try:
        mobile = normalize_us_mobile(phone or "")
        if mobile is None or residents.split_unit(unit or "") is None:
            return
        async with AsyncSessionFactory() as session, session.begin():
            await session.execute(
                insert(LeadContactJob)
                .values(
                    key=f"thread:{thread_id}:{digest(mobile)}",
                    zillow_thread_id=thread_id,
                    unit=unit,
                    phone=mobile,
                    name=name,
                    next_attempt_at=now,
                    created_at=now,
                )
                .on_conflict_do_nothing(index_elements=["key"])
            )
    except Exception:
        logfire.exception("lead contacts: Zillow lead not queued", thread_id=thread_id)


@dataclass
class RunReport:
    created: int = 0
    updated: int = 0
    unchanged: int = 0
    left_alone: int = 0
    skipped: int = 0
    retrying: int = 0
    failed: int = 0

    @property
    def total(self) -> int:
        return sum(vars(self).values())


async def run_due(
    *, now: datetime, store: LeadContactStore | None = None, limit: int = RUN_LIMIT
) -> RunReport:
    """Work every job due by ``now``, oldest first."""
    store = store if store is not None else contact_store()
    report = RunReport()
    async with AsyncSessionFactory() as session:
        due = (
            await session.scalars(
                select(LeadContactJob.id)
                .where(LeadContactJob.status == "pending", LeadContactJob.next_attempt_at <= now)
                .order_by(LeadContactJob.id)
                .limit(limit)
            )
        ).all()
    for job_id in due:
        outcome = await _work(job_id, now=now, store=store)
        if outcome:
            setattr(report, outcome, getattr(report, outcome) + 1)
    if report.total:
        logfire.info("lead contacts worked", **vars(report))
    return report


async def _claim(job_id: int, now: datetime) -> Claimed | None:
    """Take one due job, count the attempt and lease it, in one committed transaction."""
    async with AsyncSessionFactory() as session, session.begin():
        job = await session.scalar(
            select(LeadContactJob)
            .where(
                LeadContactJob.id == job_id,
                LeadContactJob.status == "pending",
                LeadContactJob.next_attempt_at <= now,
            )
            .with_for_update(skip_locked=True)
        )
        if job is None:
            return None
        stale = False
        if job.attendee_id is not None:
            attendee = await session.get(ShowingAttendee, job.attendee_id)
            stale = (
                attendee is None
                or attendee.mobile != job.phone
                or attendee.attention_reason in WRONG_NUMBER
            )
        job.attempts += 1
        job.next_attempt_at = now + LEASE
        return Claimed(
            id=job.id,
            attempts=job.attempts,
            phone=job.phone,
            name=job.name,
            email=job.email,
            unit=job.unit,
            zillow_thread_id=job.zillow_thread_id,
            stale=stale,
        )


async def _known_contact(phone: str) -> str | None:
    """The Quo contact an earlier job made or found for ``phone``, if any."""
    async with AsyncSessionFactory() as session:
        return await session.scalar(
            select(LeadContactJob.contact_id)
            .where(
                LeadContactJob.key.endswith(f":{digest(phone)}"),
                LeadContactJob.contact_id.is_not(None),
            )
            .order_by(LeadContactJob.id.desc())
            .limit(1)
        )


async def _work(job_id: int, *, now: datetime, store: LeadContactStore) -> str | None:
    job = await _claim(job_id, now)
    if job is None:
        return None
    person = None if job.stale else person_of(job)
    if person is None:
        values: dict = {"status": "done", "result": "skipped"}
    else:
        try:
            synced = await sync(person, store, known=await _known_contact(person.phone))
            values = {"status": "done", "result": synced.result, "contact_id": synced.contact_id}
        except ContactsUnavailable as exc:
            if exc.retryable and job.attempts < MAX_ATTEMPTS:
                values = {
                    "next_attempt_at": now + timedelta(minutes=2**job.attempts),
                    "last_error": exc.detail[:500],
                }
            else:
                values = {"status": "failed", "last_error": exc.detail[:500]}
        except Exception as exc:
            logfire.exception("lead contacts: job failed", job_id=job_id)
            values = {"status": "failed", "last_error": f"{type(exc).__name__}: {exc}"[:500]}
    if "status" in values:
        # Done or failed: the person's details are no longer needed here.
        values |= {"phone": None, "name": None, "email": None, "finished_at": now}
    async with AsyncSessionFactory() as session, session.begin():
        await session.execute(
            update(LeadContactJob).where(LeadContactJob.id == job_id).values(**values)
        )
    if values.get("status") == "failed":
        logfire.error("lead contacts: Quo contact not written", job_id=job_id)
        return "failed"
    return values.get("result") or "retrying"


async def scheduled_run() -> None:
    """The job every minute: queue new bookings, then work what is due."""
    now = datetime.now(UTC)
    await queue_bookings(now=now)
    await run_due(now=now)


# Shown on the team's Scheduler page (api/src/schedulers/job_info.py).
JOB_INFO = JobInfo(
    name="Showings: Quo Lead contacts",
    summary="Makes or fills in a Quo Lead contact for each person booked with a mobile "
    "and each Zillow lead who gave a phone number.",
    description=(
        "Queues everyone booked for a showing with a mobile, and works each queued "
        "person: it looks the number up in Quo, leaves alone any contact that is not a "
        "Lead (a tenant, vendor, partner or staff), fills in only what an existing Lead "
        "lacks (an email, a last name, the tags), and otherwise makes a Lead contact as "
        "the team's quo-contact skill writes them. Quo out of reach is tried again later, "
        "up to five times. It never sends a text and never changes a booking."
        "\n\n"
        "Paused or removed, no Lead contacts are made; the queued people wait."
    ),
)


def register_job() -> None:
    """Schedule ``scheduled_run`` every minute. Called at startup when background
    automation is on (``api/index.py``)."""
    from api.src.apscheduler_service.service import get_scheduler, upsert_job

    upsert_job(
        get_scheduler(),
        info=JOB_INFO,
        id=JOB_ID,
        func=scheduled_run,
        trigger="interval",
        seconds=60,
        max_instances=1,
        coalesce=True,
    )
