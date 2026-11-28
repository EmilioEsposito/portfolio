"""The lead inbox: each Zillow lead thread the lead agent has handled, for the team.

One row per Gmail thread in the shared mailbox (``lead_thread_id`` on the lead
agent's conversations), and one page per thread: who the lead is and what
Zillow's form said, the emails in order, the thread's showings, what the agent
did on each run, what waits for a person, and the thread's own booking link.

It reads what is already stored, and asks Gmail only what the store cannot know:

- Each run of the agent is one conversation, and saves the case it was decided
  on (``metadata.case``, from ``case.case_record``), which holds the thread as
  Gmail had it then. The newest run's thread, plus any reply sent after it was
  built (an approval's ``sent_body``, or ``auto_delivery.body``), is the email
  history; one captured outside production is shown, marked captured.
- A reply written in Gmail (the usual answer to an escalation) or in a Sernia
  AI chat reaches the store only with the lead's next email. So the list reads
  its newest rows' email counts and latest emails from Gmail in one batch of
  metadata reads (``thread.thread_summaries``, kept a minute), and a lead's page
  loads the thread itself when Gmail has more emails than the newest run saw.
  When Gmail does not answer, the stored history stands in.
- The showings are the booking service's attendee rows tied to the thread
  (``showing_attendees.zillow_thread_id``), less a place booked for a reply
  that was never sent, which the lead never heard of.
- What waits for a person is read by the team's pending rule
  (``agent_conversations.models.pending_approvals``), so the inbox and the
  team home agree.

The one write: the lead page asks the booking service for the thread's links
(``lead_tours.lead_booking_for``), which makes the thread's random token the
first time, as the general agent's ``showings_read`` does.

Everything is read from fields the lead agent has saved since it went live, so
threads from before this module need nothing new.
"""

import asyncio
import re
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any, Literal
from urllib.parse import quote
from zoneinfo import ZoneInfo

import logfire
from fastapi import APIRouter, Depends, HTTPException, Path, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.src.agent_conversations.identity import AGENT_NAME
from api.src.agent_conversations.models import AgentConversation, pending_approvals
from api.src.database.database import AsyncSessionFactory
from api.src.google.common.config import SHARED_EXTERNAL_EMAIL
from api.src.rental_listings import service as listings_service
from api.src.rental_listings.checks import RentalDataInvalid
from api.src.rental_listings.schema import RentalListing
from api.src.showing_bookings import lead_tours, open_times
from api.src.showing_bookings.models import Showing, ShowingAttendee
from api.src.showing_bookings.settings import (
    ShowingBookingSettings,
    current_environment,
    public_booking_on,
    unit_open,
)
from api.src.showing_bookings.settings import read_settings as read_booking_settings
from api.src.utils import team_links
from api.src.utils.clerk import verify_serniacapital_user
from api.src.zillow_leads.handle import ASK_TEAM, SEND_REPLY
from api.src.zillow_leads.identity import HANDLER
from api.src.zillow_leads.listing_data import as_listing_data
from api.src.zillow_leads.thread import (
    FORM_HEADING,
    ThreadSummary,
    load_lead_thread,
    thread_summaries,
)

router = APIRouter(prefix="/zillow-leads", dependencies=[Depends(verify_serniacapital_user)])

# A Gmail thread id as the API gives it (16 hex digits), with room to spare.
THREAD_ID = r"^[A-Za-z0-9_-]{1,64}$"
# The thread in the shared mailbox's Gmail. ``authuser`` picks that account
# when the browser is signed in to several; the fragment opens the thread.
GMAIL_THREAD_URL = "https://mail.google.com/mail/u/?authuser={mailbox}#all/{thread}"
# Zillow's subject for a new inquiry, which Gmail's list shows:
# "Casey is requesting information about 12 Example St #3, Pittsburgh, PA, 15224".
_INQUIRY = re.compile(
    r"^(?:(?:re|fwd?):\s*)*(?P<name>.+?) is requesting information about (?P<address>.+?)\s*$",
    re.I,
)
_FACT = re.compile(r"^-\s*(?P<label>[^:]+):\s*(?P<value>.*)$")
# Zillow ends a relayed message with its own tracking id, which is not the lead's words.
_RELAY_ID = re.compile(
    r"\s*\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\s*$", re.I
)
# Form facts the header already shows another way: the name, and the listing's address.
HEADER_SHOWS = {"name", "listing"}
SNIPPET_LENGTH = 160
DEFAULT_LIMIT = 100

Status = Literal[
    "draft_waiting",
    "needs_team",
    "handed_off",
    "replied",
    "draft_rejected",
    "no_reply_needed",
    "handled",
]
RunResult = Literal[
    "waiting",
    "needs_team",
    "handed_off",
    "sent",
    "sent_edited",
    "sent_automatically",
    "captured",
    "rejected",
    "handled",
    "no_reply",
    "closed",
]


# --------------------------------------------------------------------------
# Response models
# --------------------------------------------------------------------------


class FormFact(BaseModel):
    """One line of what Zillow's form added to a lead's email, such as "Move in"."""

    label: str
    value: str


class LeadEmail(BaseModel):
    """One email in the thread, theirs or ours, oldest first."""

    from_sernia: bool
    sent_at: datetime
    subject: str
    # The words of the email, without what Zillow's form added.
    text: str
    form: list[FormFact] = Field(default_factory=list)
    # Ours only: "inbox" when a person approved it from a card, "automatic" when
    # the agent sent it alone, "captured" when it was kept outside production and
    # never sent (``handle.send_reply``); None for one read from Gmail.
    sent_by: Literal["inbox", "automatic", "captured"] | None = None


class ShowingSummary(BaseModel):
    """The thread's showing that matters most: the next one booked, else the last."""

    state: Literal["booked", "held", "cancelled"]
    unit: str
    starts_at: datetime
    # For a held one, whether they came: "attended", "no_show" or "unknown".
    attendance: str | None = None
    # For a cancelled one, the booking service's reason ("prospect", "team", ...).
    cancelled_by: str | None = None


class LeadShowing(BaseModel):
    """One place the thread holds, or held, in a showing."""

    attendee_id: int
    showing_id: int
    unit: str
    address: str
    starts_at: datetime
    status: Literal["booked", "cancelled"]
    past: bool
    # The name given when booking (a partner or friend may book for the lead).
    booked_by: str
    # How: "page" (the booking link), "team", or "lead_agent" (a reply booked it).
    source: str
    booked_at: datetime | None
    # False for the provisional place a reply booked, until the lead adds their
    # details through their link; the text reminder and confirmation email wait for them.
    details_added: bool
    cancelled_by: str | None = None
    cancelled_at: datetime | None = None
    attendance: str
    # The showing on the Booking settings tab.
    showings_path: str


class ShowingChange(BaseModel):
    """A showing of the thread's booked or cancelled after a waiting card was written.

    The card's case did not know it, so its draft may now say the wrong thing:
    a draft asking the lead to book, written before they booked.
    """

    kind: Literal["booked", "cancelled"]
    unit: str
    starts_at: datetime
    # The name given when booking.
    booked_by: str
    # When it was booked or cancelled.
    at: datetime
    cancelled_by: str | None = None


class WaitingCard(BaseModel):
    """A card in one of the thread's conversations that waits for a person."""

    conversation_id: str
    tool_call_id: str
    kind: Literal["draft", "needs_team"]
    tool_name: str
    # The card as stored: a draft has to, subject, body, why and team_notes; the
    # other kind has question, why and team_notes.
    args: dict[str, Any]
    since: datetime
    chat_path: str
    # The thread's showings booked or cancelled after the card's case was built.
    showing_changes: list[ShowingChange] = Field(default_factory=list)


class AgentRun(BaseModel):
    """One run of the lead agent on the thread, and what became of it."""

    conversation_id: str
    chat_path: str
    at: datetime
    emails_seen: int
    action: str  # reply, no_reply or escalate
    reason: str = ""
    question: str | None = None
    team_notes: list[str] = Field(default_factory=list)
    result: RunResult
    decided_by: str | None = None
    decided_at: datetime | None = None
    handed_to: str | None = None


class BookingLink(BaseModel):
    """The thread's own booking link for one unit, or why there is none now."""

    unit: str
    address: str
    url: str | None = None
    no_link_because: str | None = None


class LeadThreadRow(BaseModel):
    """A thread as the list shows it, laid out like Gmail's list of the label."""

    thread_id: str
    # The name before "is requesting information about", as Gmail's list shows it.
    first_name: str
    name: str
    subject: str
    unit: str | None = None
    address: str = ""
    email_count: int
    latest_from_sernia: bool
    latest_at: datetime | None = None
    snippet: str = ""
    # The newest of: an email, a run or decision, a showing booked or cancelled.
    activity_at: datetime
    status: Status
    # When the oldest card still waiting was written.
    waiting_since: datetime | None = None
    # A waiting draft was written before a showing of the thread was booked or
    # cancelled, so it may no longer fit (``ShowingChange``).
    draft_outdated: bool = False
    showing: ShowingSummary | None = None


class LeadThreadList(BaseModel):
    threads: list[LeadThreadRow]
    has_more: bool


class LeadThreadPage(BaseModel):
    thread: LeadThreadRow
    gmail_url: str
    phone: str | None = None
    facts: list[FormFact]
    emails: list[LeadEmail]
    # When the emails were read from Gmail: the newest run's time, or now when
    # Gmail had emails the run did not see and the page read the thread itself.
    emails_read_at: datetime | None = None
    emails_live: bool = False
    # Whether Gmail answered at all; False means the stored history is shown unchecked.
    emails_checked: bool = False
    showings: list[LeadShowing]
    waiting: list[WaitingCard]
    runs: list[AgentRun]
    booking_links: list[BookingLink]
    booking_links_problem: str | None = None
    # The newest run's conversation, which a hand-off to Sernia AI starts from.
    latest_conversation_id: str
    # The Sernia AI chat the thread was last handed to, if it was.
    handed_to: str | None = None


# --------------------------------------------------------------------------
# Reading the stored runs
# --------------------------------------------------------------------------


@dataclass
class Run:
    """One lead conversation, as much of it as the inbox reads."""

    id: str
    created_at: datetime
    updated_at: datetime
    thread_id: str
    outcome: dict[str, Any]
    approval: list[dict[str, Any]]
    auto_delivery: dict[str, Any]
    hand_off: dict[str, Any]
    # The subjects of the emails the run answered, saved by the queue.
    subjects: list[str] = field(default_factory=list)
    # The saved case's thread, listings and build time; only read where needed.
    case: dict[str, Any] = field(default_factory=dict)
    pending: list[dict[str, Any]] = field(default_factory=list)


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _when(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def _meta():
    return AgentConversation.metadata_


def _lead_conversations():
    meta = _meta()
    return (
        select(
            AgentConversation.id,
            AgentConversation.created_at,
            AgentConversation.updated_at,
            meta["lead_thread_id"].as_string().label("thread_id"),
            meta["outcome"].label("outcome"),
            meta["approval"].label("approval"),
            meta["auto_delivery"].label("auto_delivery"),
            meta["hand_off"].label("hand_off"),
            meta["subjects"].label("subjects"),
        )
        .where(
            AgentConversation.agent_name == AGENT_NAME,
            meta["handler"].as_string() == HANDLER,
            meta["lead_thread_id"].as_string().is_not(None),
        )
        .order_by(AgentConversation.created_at, AgentConversation.id)
    )


def _run(row: Any) -> Run:
    return Run(
        id=row.id,
        created_at=row.created_at,
        updated_at=row.updated_at or row.created_at,
        thread_id=row.thread_id,
        outcome=_dict(row.outcome),
        approval=[_dict(item) for item in _list(row.approval)],
        auto_delivery=_dict(row.auto_delivery),
        hand_off=_dict(row.hand_off),
        subjects=[str(item) for item in _list(row.subjects) if item],
    )


async def _read_runs(session: AsyncSession, thread_id: str | None = None) -> list[Run]:
    query = _lead_conversations()
    if thread_id is not None:
        query = query.where(_meta()["lead_thread_id"].as_string() == thread_id)
    return [_run(row) for row in (await session.execute(query)).all()]


async def _read_cases(session: AsyncSession, runs: list[Run]) -> None:
    """Fill in the saved case's thread, listings and build time of ``runs``."""
    if not runs:
        return
    meta = _meta()
    rows = await session.execute(
        select(
            AgentConversation.id,
            meta[("case", "thread", "messages")].label("messages"),
            meta[("case", "listings")].label("listings"),
            meta[("case", "now")].as_string().label("now"),
        ).where(AgentConversation.id.in_([run.id for run in runs]))
    )
    by_id = {run.id: run for run in runs}
    for row in rows.all():
        by_id[row.id].case = {
            "messages": [_dict(item) for item in _list(row.messages)],
            "listings": [_dict(item) for item in _list(row.listings)],
            "now": _when(row.now),
        }


async def _read_pending(session: AsyncSession, runs: list[Run]) -> None:
    waiting = await pending_approvals(session, [run.id for run in runs])
    for run in runs:
        run.pending = waiting.get(run.id, [])


async def _read_showings(
    session: AsyncSession, thread_ids: list[str]
) -> dict[str, list[tuple[ShowingAttendee, Showing]]]:
    if not thread_ids:
        return {}
    rows = await session.execute(
        select(ShowingAttendee, Showing)
        .join(Showing, Showing.id == ShowingAttendee.showing_id)
        .where(
            ShowingAttendee.zillow_thread_id.in_(thread_ids),
            # A place booked for a reply that was never sent: the lead never heard of it.
            ShowingAttendee.cancel_reason.is_distinct_from("reply_not_sent"),
        )
        .order_by(Showing.starts_at, ShowingAttendee.id)
    )
    found: dict[str, list[tuple[ShowingAttendee, Showing]]] = {}
    for attendee, showing in rows.all():
        found.setdefault(attendee.zillow_thread_id, []).append((attendee, showing))
    return found


# --------------------------------------------------------------------------
# What the runs say
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Sent:
    body: str
    at: datetime
    by: Literal["inbox", "automatic", "captured"]


def sent_replies(run: Run) -> list[Sent]:
    """The replies this run's cards sent: approved from a card, or sent automatically.
    One captured outside production is listed too, as captured."""
    sent: list[Sent] = []
    for record in run.approval:
        at = _when(record.get("at"))
        body = record.get("sent_body")
        if record.get("tool_name") == SEND_REPLY and record.get("approved") and body and at:
            sent.append(
                Sent(body=body, at=at, by="captured" if record.get("captured") else "inbox")
            )
    delivery = run.auto_delivery
    at = _when(delivery.get("at"))
    if delivery.get("status") in ("sent", "captured") and delivery.get("body") and at:
        by = "captured" if delivery["status"] == "captured" else "automatic"
        sent.append(Sent(body=delivery["body"], at=at, by=by))
    return sent


@dataclass(frozen=True)
class Result:
    result: RunResult
    by: str | None = None
    at: datetime | None = None


def run_result(run: Run) -> Result:
    """What became of one run: what waits, or how its card was decided."""
    if any(card.get("tool_name") == SEND_REPLY for card in run.pending):
        return Result("waiting")
    if run.pending:
        return Result("needs_team")
    if run.hand_off:
        return Result("handed_off", run.hand_off.get("by"), _when(run.hand_off.get("at")))
    if run.auto_delivery.get("status") == "sent":
        return Result("sent_automatically", None, _when(run.auto_delivery.get("at")))
    if run.auto_delivery.get("status") == "captured":
        return Result("captured", None, _when(run.auto_delivery.get("at")))
    for record in reversed(run.approval):
        by, at = record.get("by"), _when(record.get("at"))
        if record.get("tool_name") == SEND_REPLY:
            if not record.get("approved"):
                return Result("rejected", by, at)
            if record.get("captured"):
                return Result("captured", by, at)
            return Result("sent_edited" if record.get("edited") else "sent", by, at)
        if record.get("tool_name") == ASK_TEAM:
            return Result("handled", by, at)
    if run.outcome.get("action") == "no_reply":
        return Result("no_reply")
    return Result("closed")


_STATUS_FOR_RESULT: dict[str, Status] = {
    "handed_off": "handed_off",
    "sent": "replied",
    "sent_edited": "replied",
    "sent_automatically": "replied",
    "rejected": "draft_rejected",
    "no_reply": "no_reply_needed",
}


def thread_status(runs: list[Run]) -> Status:
    """Where the thread stands for the team: what waits first, else how the last run ended."""
    cards = [card for run in runs for card in run.pending]
    if any(card.get("tool_name") == SEND_REPLY for card in cards):
        return "draft_waiting"
    if cards:
        return "needs_team"
    return _STATUS_FOR_RESULT.get(run_result(runs[-1]).result, "handled")


def split_form(body: str) -> tuple[str, list[FormFact]]:
    """An email's own words, and the facts Zillow's form added under its heading."""
    words, heading, form = body.partition(FORM_HEADING)
    words = _RELAY_ID.sub("", words.strip())
    if not heading:
        return words, []
    facts = []
    for line in form.splitlines():
        match = _FACT.match(line.strip())
        if match and match["value"].strip():
            facts.append(FormFact(label=match["label"].strip(), value=match["value"].strip()))
    return words, facts


def emails(runs: list[Run], live: list[dict[str, Any]] | None = None) -> list[LeadEmail]:
    """The thread as the newest run read it, then the replies sent after that.

    ``live`` is the thread as Gmail has it now (``thread.load_lead_thread``),
    read when Gmail holds emails the newest run did not see. The replies a card
    or the automatic send sent are then found in it and marked as such.
    """
    latest = runs[-1]
    found: list[LeadEmail] = []
    for message in latest.case.get("messages", []) if live is None else live:
        sent_at = _when(message.get("sent_at"))
        if sent_at is None:
            continue
        text, form = split_form(str(message.get("body") or ""))
        found.append(
            LeadEmail(
                from_sernia=bool(message.get("from_sernia")),
                sent_at=sent_at,
                subject=str(message.get("subject") or ""),
                text=text,
                form=form,
            )
        )
    subject = _reply_subject(found)
    if live is not None:
        replies = [reply for run in runs for reply in sent_replies(run)]
        return _mark_sent(found, replies, subject)
    read_at = latest.case.get("now") or latest.created_at
    for run in runs:
        for reply in sent_replies(run):
            # A captured reply is in no thread Gmail returned, so it is always added.
            if reply.at > read_at or reply.by == "captured":
                found.append(_reply_email(reply, subject))
    return sorted(found, key=lambda email: email.sent_at)


def _reply_email(reply: Sent, subject: str) -> LeadEmail:
    return LeadEmail(
        from_sernia=True,
        sent_at=reply.at,
        subject=subject,
        text=reply.body.strip(),
        sent_by=reply.by,
    )


# How far apart a reply's recorded send and Gmail's copy of it may be.
SAME_SEND = timedelta(minutes=5)


def _mark_sent(found: list[LeadEmail], replies: list[Sent], subject: str) -> list[LeadEmail]:
    """Mark the emails in a live thread that a card or the automatic send sent.

    A reply captured outside production never reached Gmail: it is added on its
    own, marked captured, and never claims an email someone sent from Gmail.
    """
    for reply in replies:
        if reply.by == "captured":
            found.append(_reply_email(reply, subject))
            continue
        match = next(
            (
                email
                for email in found
                if email.from_sernia
                and email.sent_by is None
                and abs(email.sent_at - reply.at) <= SAME_SEND
            ),
            None,
        )
        if match is not None:
            match.sent_by = reply.by
    return sorted(found, key=lambda email: email.sent_at)


def _reply_subject(found: list[LeadEmail]) -> str:
    subject = next((email.subject for email in reversed(found) if not email.from_sernia), "")
    return subject if subject.lower().startswith("re:") or not subject else f"Re: {subject}"


def snippet(text: str) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= SNIPPET_LENGTH else flat[: SNIPPET_LENGTH - 1].rstrip() + "…"


def form_facts(found: list[LeadEmail]) -> list[FormFact]:
    """Every fact Zillow's form gave across the lead's emails, the first value of each."""
    facts: dict[str, FormFact] = {}
    for email in found:
        for fact in email.form:
            facts.setdefault(fact.label.casefold(), fact)
    return list(facts.values())


def _street(address: str) -> str:
    """An address's first line, compared loosely: "12 Example St Apt 3" is "12 example st #3"."""
    street = address.split(",")[0].casefold()
    street = re.sub(r"\b(?:apt|apartment|unit|suite|ste)\b\.?\s*", "#", street)
    street = re.sub(r"#\s*", "#", street)
    return " ".join(re.sub(r"[^\w#]+", " ", street).split())


def _listing_for(
    listings: list[dict[str, Any]], unit: str | None, address: str
) -> dict[str, Any] | None:
    """The case's listing the lead asked about: by unit name, by the agent's unit, or the
    subject's address. None when nothing matches."""
    wanted = {value for value in (_street(unit or ""), _street(address)) if value}
    for listing in listings:
        name = str(listing.get("unit") or "")
        if unit and name.casefold() == unit.strip().casefold():
            return listing
        if wanted and _street(str(listing.get("address") or "")) in wanted:
            return listing
    if len(listings) == 1 and not address:
        return listings[0]
    return None


def _first_subject(runs: list[Run], found: list[LeadEmail]) -> str:
    for email in found:
        if email.subject:
            return email.subject
    return next((subject for run in runs for subject in run.subjects), "")


def _lead_name(runs: list[Run], facts: list[FormFact], inquiry_name: str) -> str:
    for run in reversed(runs):
        name = str(_dict(run.outcome.get("lead")).get("name") or "").strip()
        if name:
            return name
    by_label = {fact.label.casefold(): fact.value for fact in facts}
    return by_label.get("name") or inquiry_name or "Zillow lead"


def showing_views(rows: list[tuple[ShowingAttendee, Showing]], now: datetime) -> list[LeadShowing]:
    views = []
    for attendee, showing in rows:
        booked = attendee.status == "booked" and showing.status != "cancelled"
        views.append(
            LeadShowing(
                attendee_id=attendee.id,
                showing_id=showing.id,
                unit=showing.unit,
                address=showing.address,
                starts_at=showing.starts_at,
                status="booked" if booked else "cancelled",
                past=showing.starts_at <= now,
                booked_by=attendee.name,
                source=attendee.source,
                booked_at=attendee.created_at,
                details_added=not (
                    attendee.source == "lead_agent" and attendee.texts_consent_at is None
                ),
                cancelled_by=None if booked else attendee.cancel_reason or showing.cancel_reason,
                cancelled_at=None if booked else attendee.cancelled_at or showing.cancelled_at,
                attendance=attendee.attendance,
                showings_path=team_links.showing_path(showing.id),
            )
        )
    return views


def showing_summary(views: list[LeadShowing]) -> ShowingSummary | None:
    upcoming = [view for view in views if view.status == "booked" and not view.past]
    if upcoming:
        first = min(upcoming, key=lambda view: view.starts_at)
        return ShowingSummary(state="booked", unit=first.unit, starts_at=first.starts_at)
    held = [view for view in views if view.status == "booked"]
    if held:
        last = max(held, key=lambda view: view.starts_at)
        return ShowingSummary(
            state="held", unit=last.unit, starts_at=last.starts_at, attendance=last.attendance
        )
    if views:
        last = max(views, key=lambda view: view.cancelled_at or view.starts_at)
        return ShowingSummary(
            state="cancelled",
            unit=last.unit,
            starts_at=last.starts_at,
            cancelled_by=last.cancelled_by,
        )
    return None


def _activity(runs: list[Run], found: list[LeadEmail], views: list[LeadShowing]) -> datetime:
    moments = [run.updated_at for run in runs] + [email.sent_at for email in found]
    for view in views:
        moments += [moment for moment in (view.booked_at, view.cancelled_at) if moment]
    return max(moments)


def thread_row(
    runs: list[Run], views: list[LeadShowing], live: list[dict[str, Any]] | None = None
) -> tuple[LeadThreadRow, list[LeadEmail], list[FormFact]]:
    """The list's row for one thread, with the emails and form facts it was read from."""
    found = emails(runs, live)
    facts = form_facts(found)
    subject = _first_subject(runs, found)
    inquiry = _INQUIRY.match(subject)
    inquiry_name = inquiry["name"].strip() if inquiry else ""
    subject_address = inquiry["address"].strip() if inquiry else ""
    name = _lead_name(runs, facts, inquiry_name)
    lead = _dict(runs[-1].outcome.get("lead"))
    listing = _listing_for(runs[-1].case.get("listings", []), lead.get("unit"), subject_address)
    unit = str(listing["unit"]) if listing and listing.get("unit") else None
    if unit is None and views:
        unit = views[-1].unit
    latest = found[-1] if found else None
    waiting = [run.created_at for run in runs if run.pending]
    outdated = any(
        showing_changes(views, run.case.get("now") or run.created_at)
        for run in runs
        if any(card.get("tool_name") == SEND_REPLY for card in run.pending)
    )
    return (
        LeadThreadRow(
            thread_id=runs[-1].thread_id,
            first_name=inquiry_name or name.split()[0],
            name=name,
            subject=subject,
            unit=unit,
            address=str((listing or {}).get("address") or "") or subject_address,
            email_count=len(found),
            latest_from_sernia=bool(latest and latest.from_sernia),
            latest_at=latest.sent_at if latest else None,
            snippet=snippet(latest.text) if latest else "",
            activity_at=_activity(runs, found, views),
            status=thread_status(runs),
            waiting_since=min(waiting) if waiting else None,
            draft_outdated=outdated,
            showing=showing_summary(views),
        ),
        found,
        facts,
    )


def showing_changes(views: list[LeadShowing], since: datetime) -> list[ShowingChange]:
    """What happened to the thread's showings after ``since``, in the order it happened."""
    changes = []
    for view in views:
        if view.booked_at and view.booked_at > since:
            changes.append(
                ShowingChange(
                    kind="booked",
                    unit=view.unit,
                    starts_at=view.starts_at,
                    booked_by=view.booked_by,
                    at=view.booked_at,
                )
            )
        if view.cancelled_at and view.cancelled_at > since:
            changes.append(
                ShowingChange(
                    kind="cancelled",
                    unit=view.unit,
                    starts_at=view.starts_at,
                    booked_by=view.booked_by,
                    at=view.cancelled_at,
                    cancelled_by=view.cancelled_by,
                )
            )
    return sorted(changes, key=lambda change: change.at)


def waiting_cards(runs: list[Run], views: list[LeadShowing]) -> list[WaitingCard]:
    return [
        WaitingCard(
            conversation_id=run.id,
            tool_call_id=str(card.get("tool_call_id") or ""),
            kind="draft" if card.get("tool_name") == SEND_REPLY else "needs_team",
            tool_name=str(card.get("tool_name") or ""),
            args=_dict(card.get("args")),
            since=run.created_at,
            chat_path=team_links.chat_path(run.id),
            # Measured from when the case was built: the draft knew nothing later.
            showing_changes=showing_changes(views, run.case.get("now") or run.created_at),
        )
        for run in runs
        for card in run.pending
    ]


def agent_runs(runs: list[Run]) -> list[AgentRun]:
    """Each run, newest first."""
    views = []
    for run in reversed(runs):
        result = run_result(run)
        views.append(
            AgentRun(
                conversation_id=run.id,
                chat_path=team_links.chat_path(run.id),
                at=run.created_at,
                emails_seen=len(run.case.get("messages", [])),
                action=str(run.outcome.get("action") or "unknown"),
                reason=str(run.outcome.get("reason") or ""),
                question=run.outcome.get("question"),
                team_notes=[str(note) for note in _list(run.outcome.get("team_notes"))],
                result=result.result,
                decided_by=result.by,
                decided_at=result.at,
                handed_to=run.hand_off.get("to"),
            )
        )
    return views


def gmail_url(thread_id: str) -> str:
    return GMAIL_THREAD_URL.format(
        mailbox=quote(SHARED_EXTERNAL_EMAIL, safe=""), thread=quote(thread_id, safe="")
    )


# --------------------------------------------------------------------------
# The thread's own booking links
# --------------------------------------------------------------------------


def no_link_because(
    settings: ShowingBookingSettings, listing: RentalListing, today: date, has_place: bool
) -> str | None:
    """Why a unit's booking page would not answer this lead now, for the team."""
    if not open_times.on_offer(listing, today):
        return f"Off the market from {listing.off_market_on:%b %-d}, so it has no booking page."
    if not public_booking_on(settings, current_environment()):
        return "Public booking is switched off on the Booking settings tab."
    if not has_place and not unit_open(settings, listing.unit):
        return f"{listing.unit} is not ticked live on the Booking settings tab."
    return None


async def booking_links(
    thread_id: str, case_units: list[str], now: datetime
) -> tuple[list[BookingLink], str | None]:
    """Each unit's booking link with the thread's token, for the units the case offered.

    Every unit on offer when the case offered none. Never raises: a problem is
    returned as the second value, for the page to show in place of the links.
    """
    try:
        async with AsyncSessionFactory() as session:
            listings = await listings_service.read_listings(session)
            rules = await listings_service.read_showing_rules(session)
            settings = await read_booking_settings(session)
    except RentalDataInvalid as error:
        return [], f"The rental listing data fails its checks: {error}."
    except Exception:
        logfire.exception("lead inbox: booking links unreadable", thread_id=thread_id)
        return [], "The booking links could not be read just now. Reload to try again."
    today = now.astimezone(ZoneInfo(rules.timezone)).date()
    on_offer = [listing for listing in listings if open_times.on_offer(listing, today)]
    found = await lead_tours.lead_booking_for(
        lead_tours.BOOKING_TOUR_SOURCE, thread_id, as_listing_data(on_offer, rules), now
    )
    if found is None:
        return [], "The booking links could not be read just now. Reload to try again."
    wanted = {unit.casefold() for unit in case_units}
    chosen = [listing for listing in listings if listing.unit.casefold() in wanted] or on_offer
    booked_at = {own.unit.casefold() for own in found.own_showings if own.status == "booked"}
    links = []
    for listing in chosen:
        url = found.booking_links.get(listing.unit)
        links.append(
            BookingLink(
                unit=listing.unit,
                address=listing.address,
                url=url,
                no_link_because=None
                if url
                else no_link_because(settings, listing, today, listing.unit.casefold() in booked_at)
                or "No booking page answers for this unit now.",
            )
        )
    return links, None


# --------------------------------------------------------------------------
# What Gmail has that the runs did not see
# --------------------------------------------------------------------------

# A reply written in Gmail (an escalation's answer, say) reaches the stored runs
# only with the lead's next email. So the list asks Gmail, in one batch of
# metadata reads, how many emails its newest rows have, and a lead's page loads
# the thread itself when Gmail has more than the runs saw. Stored data stands in
# whenever Gmail does not answer.
GMAIL_ROWS = 30
GMAIL_TIMEOUT_SECONDS = 8
SUMMARY_TTL = timedelta(seconds=60)
_summary_cache: dict[str, tuple[datetime, ThreadSummary]] = {}


async def gmail_summaries(thread_ids: list[str], now: datetime) -> dict[str, ThreadSummary]:
    """Gmail's count and latest email for each thread, kept a minute; {} when Gmail fails."""
    fresh = {
        thread_id: cached
        for thread_id in thread_ids
        if (entry := _summary_cache.get(thread_id)) and now - entry[0] < SUMMARY_TTL
        for cached in [entry[1]]
    }
    missing = [thread_id for thread_id in thread_ids if thread_id not in fresh]
    if missing:
        try:
            found = await asyncio.wait_for(thread_summaries(missing), timeout=GMAIL_TIMEOUT_SECONDS)
        except Exception:
            logfire.warn("lead inbox: Gmail not read, stored emails shown", threads=len(missing))
            found = {}
        for thread_id, summary in found.items():
            _summary_cache[thread_id] = (now, summary)
        fresh.update(found)
    return fresh


def with_gmail(row: LeadThreadRow, summary: ThreadSummary | None) -> LeadThreadRow:
    """The row with Gmail's count and latest email, when Gmail has emails the runs did not see."""
    if summary is None or summary.count <= row.email_count:
        return row
    return row.model_copy(
        update={
            "email_count": summary.count,
            "latest_at": summary.latest_at,
            "latest_from_sernia": summary.latest_from_sernia,
            "snippet": snippet(_RELAY_ID.sub("", summary.latest_snippet)),
            "activity_at": max(row.activity_at, summary.latest_at),
        }
    )


async def live_thread(thread_id: str) -> list[dict[str, Any]] | None:
    """The thread's emails as Gmail has them now, in the saved case's shape; None on failure."""
    try:
        thread = await asyncio.wait_for(load_lead_thread(thread_id), timeout=GMAIL_TIMEOUT_SECONDS)
    except Exception:
        logfire.warn("lead inbox: Gmail thread not read, stored emails shown")
        return None
    if thread is None:
        return None
    return [message.model_dump(mode="json") for message in thread.messages]


# --------------------------------------------------------------------------
# Routes
# --------------------------------------------------------------------------


@router.get("/threads", response_model=LeadThreadList)
async def list_threads(
    limit: int = Query(DEFAULT_LIMIT, ge=1, le=500),
) -> LeadThreadList:
    """Every lead thread, newest activity first: one row per Gmail thread."""
    now = datetime.now(UTC)
    async with AsyncSessionFactory() as session:
        runs = await _read_runs(session)
        by_thread: dict[str, list[Run]] = {}
        for run in runs:
            by_thread.setdefault(run.thread_id, []).append(run)
        await _read_pending(session, runs)
        await _read_cases(session, [thread_runs[-1] for thread_runs in by_thread.values()])
        showings = await _read_showings(session, list(by_thread))
    rows = [
        thread_row(thread_runs, showing_views(showings.get(thread_id, []), now))[0]
        for thread_id, thread_runs in by_thread.items()
    ]
    rows.sort(key=lambda row: row.activity_at, reverse=True)
    newest = [row.thread_id for row in rows[:GMAIL_ROWS]]
    gmail = await gmail_summaries(newest, now)
    rows = [with_gmail(row, gmail.get(row.thread_id)) for row in rows]
    rows.sort(key=lambda row: row.activity_at, reverse=True)
    return LeadThreadList(threads=rows[:limit], has_more=len(rows) > limit)


@router.get("/threads/{thread_id}", response_model=LeadThreadPage)
async def read_thread(thread_id: str = Path(pattern=THREAD_ID)) -> LeadThreadPage:
    """One lead thread: the lead, the emails, the showings, the runs and what waits."""
    now = datetime.now(UTC)
    async with AsyncSessionFactory() as session:
        runs = await _read_runs(session, thread_id)
        if not runs:
            raise HTTPException(
                404, "No lead thread with that id. It may not have reached the lead agent."
            )
        await _read_pending(session, runs)
        await _read_cases(session, runs)
        showings = await _read_showings(session, [thread_id])
    views = showing_views(showings.get(thread_id, []), now)
    row, found, facts = thread_row(runs, views)
    live = None
    summary = (await gmail_summaries([thread_id], now)).get(thread_id)
    if summary is not None and summary.count > len(found):
        live = await live_thread(thread_id)
        if live is not None:
            row, found, facts = thread_row(runs, views, live)
    case_units = [
        str(listing.get("unit"))
        for listing in runs[-1].case.get("listings", [])
        if listing.get("unit")
    ]
    links, problem = await booking_links(thread_id, case_units, now)
    lead = _dict(runs[-1].outcome.get("lead"))
    handed = next((run.hand_off.get("to") for run in reversed(runs) if run.hand_off), None)
    return LeadThreadPage(
        thread=row,
        gmail_url=gmail_url(thread_id),
        phone=lead.get("phone") or None,
        facts=[fact for fact in facts if fact.label.casefold() not in HEADER_SHOWS],
        emails=found,
        emails_read_at=now if live is not None else runs[-1].case.get("now") or runs[-1].created_at,
        emails_live=live is not None,
        emails_checked=summary is not None,
        showings=views,
        waiting=waiting_cards(runs, views),
        runs=agent_runs(runs),
        booking_links=links,
        booking_links_problem=problem,
        latest_conversation_id=runs[-1].id,
        handed_to=handed,
    )
