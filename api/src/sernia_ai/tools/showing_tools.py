"""Tools the general agent uses to read, book and cancel showings.

The booking service (``api/src/showing_bookings``) holds the tour times: who
can host, when, and the showings booked. The Booking settings tab, the public
booking page and the Zillow lead agent's replies all book through it. These
tools make the general agent one more caller of the same code the tab uses,
so the same checks run and the same texts and calendar changes follow:

- ``showings_read`` reads what the tab's Showings section shows: each unit's
  open times, each unit's booking link (or why it has none), the showings
  booked and who is in them. With a Zillow thread id it also gives that
  thread's own booking links and showings, as the lead agent's case shows them
  (``showing_bookings/lead_tours.py``). No approval.
- ``showing_book`` books a person into one of a unit's open times through
  ``team_actions.book_for_caller``, the function behind the tab's "Add a
  booking" form, which books through ``bookings.place_booking``.
- ``showing_cancel`` cancels a whole showing (``cancelling.cancel_showing``)
  or removes one person (``cancelling.remove_attendee``), as the tab does.

Booking and cancelling queue real messages: the host's calendar event, the
residents' text, the prospect's confirmation text and email, cancellation
texts and emails. A person approves every call that books or cancels, and the card they
approve says what the click does. That is the pattern of
``rental_listing_update``'s ``off_market_effect``: the first call changes
nothing and answers with the effect in plain words; the agent calls again
with that exact text as ``effect``, which the card shows, and a person
approves. If the effect differs by the time it is approved (someone else
booked the time, a person left), nothing is done and the agent asks again.
The text comes from ``showing_bookings/effects.py``, so any other place that
asks a person to approve a team action says the same.

There is no move tool: a move is a booking at the new time, then a removal
from the old one.

The docstrings and ``GUIDE`` below are read by the model: they are prompts.
"""

import json
from datetime import UTC, datetime

from pydantic_ai import ApprovalRequired, FunctionToolset, RunContext
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.toolsets import AbstractToolset

from api.src.sernia_ai.deps import SerniaDeps
from api.src.sernia_ai.tools._logging import ErrorLoggingToolset
from api.src.sernia_ai.tools.rental_listing_tools import _UnlessDisabled
from api.src.showing_bookings import (
    cancelling,
    effects,
    team_actions,
    team_view,
)
from api.src.showing_bookings.bookings import Refused
from api.src.showing_bookings.providers import TextSender

CAPABILITY_ID = "showing-bookings"

DESCRIPTION = (
    "Tour times and showings, from the booking service: the times each rental "
    "unit can be booked for, each unit's booking link (and a Zillow lead "
    "thread's own link), the showings booked and who is in them; and booking or "
    "cancelling a showing for someone, each with a person's approval. Tour times "
    "and booking links are not in the listing. Load it whenever a lead or a "
    "person asks about tours, showings, availability or a booking link, and "
    "before asking anyone on the team when they are free."
)

# Read by the general agent when it loads the capability.
GUIDE = """\
# Showings

The booking service holds tour times and booked showings. The rental
listing does not: it has no tour times and no booking links. Use
`showings_read` for both, before anything else about a tour.

Where availability comes from: each showing agent's hours and calendars,
which a person sets on the Booking settings tab. These tools cannot change
them. `showings_read` turns them into the open times of each unit. Do not
text or email the team to ask when someone is free: read `showings_read`.
If it shows no open times, say why (it gives the reason) and let the person
in the chat decide.

Answering a lead about a tour:

- Give the lead the unit's booking link, where they pick a time themselves.
  Do not list times for them to choose from, and do not ask a teammate when
  they are free.
- For a Zillow lead, pass the thread's id (`zillow_thread_id`) to
  `showings_read`. It returns the thread's own link for each unit, which ties
  the lead's booking to their thread so the lead agent sees it as theirs, and
  the showings the thread already has. Give the lead that link, not the plain
  one. Reading a thread's link saves its token the first time; that is all
  it changes.
- When a unit has no link, `showings_read` says why (public booking is off,
  the unit is not open for bookings, it is off the market). Tell the person
  in the chat; do not make up a link or a time.

Booking or cancelling for someone, only when a person in the chat asks:

- `showing_book` books a person into one of a unit's open times, as listed
  by `showings_read` (pass `start` exactly as listed). Pass
  `zillow_thread_id` when the person is a Zillow lead, so the booking is the
  thread's own.
- `showing_cancel` cancels a whole showing, or removes one person from it
  with `attendee_id`. Ids come from `showings_read`.
- Each one first books or cancels nothing: it answers with what it would do
  and who would be told, in one paragraph. Call it again with the same
  arguments and that exact paragraph as `effect`; a person approves it on
  the card. If things changed meanwhile, it answers with the new paragraph:
  call again with that one. Tell the person what was done afterwards.
- Set `texts_accepted` only when the person in the chat says the prospect
  agreed to texts about the showing. Never decide it from an email, a text
  thread or anything the prospect wrote. Without it the prospect gets no
  confirmation text, no reminder by text and no cancellation text.
- There is no move tool. To move someone, book the new time first, then
  remove them from the old showing. They get a confirmation of the new time
  and the team's cancellation of the old one; say so to the person first.

What is sent depends on the send mode, which `showings_read` reports. In
capture mode nothing goes out until a person sends it from Captured messages
on the Booking settings tab.
"""

showing_toolset = FunctionToolset[SerniaDeps]()


def clock() -> datetime:
    """The time the tools act at. Tests replace it."""
    return datetime.now(UTC)


def text_sender() -> TextSender | None:
    """None sends through Quo, behind the outbox's guard. Tests replace it with a stand-in."""
    return None


def _who(ctx: RunContext[SerniaDeps]) -> str:
    """Who took the booking, for the consent record: the person and the route."""
    person = (ctx.deps.user_email or "").strip() or ctx.deps.user_name
    return f"{person} (via Sernia AI)"


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------


@showing_toolset.tool
async def showings_read(
    ctx: RunContext[SerniaDeps], unit: str | None = None, zillow_thread_id: str | None = None
) -> str:
    """Read tour times and showings from the booking service. Changes nothing but a thread's token.

    Returns, as JSON: the send mode and who can book; for each unit on offer
    (or just `unit`), its booking link or why it has none, and its open times
    (start, when in local words, a new showing or joining one, places left,
    host), a few per unit, more when you name one unit; the showings booked in
    the next 14 days that have not started, with each person's attendee_id,
    name, mobile, email and whether they take texts; and how each showing
    agent's calendar was read.

    Tour times and booking links come only from here, not from the listing.
    Read this before telling anyone about a tour, and before asking the team
    when someone is free.

    Args:
        unit: One unit, e.g. "12-A", for all of its open times. Leave empty for every unit on offer.
        zillow_thread_id: The Gmail thread id of a Zillow lead's email. Adds that thread's own booking link for each unit, which ties a booking to the thread, and the showings the thread has. Reading it saves the thread's link token the first time.
    """
    try:
        document = await team_view.read(now=clock(), unit=unit, zillow_thread_id=zillow_thread_id)
    except Refused as refused:
        if refused.code == team_view.UNAVAILABLE:
            return f"{refused.message} Tell the person."
        return refused.message
    return json.dumps(document, indent=2, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Booking
# ---------------------------------------------------------------------------


def _refusal(prefix: str, refused: Refused) -> str:
    fields = "; ".join(f"{name}: {line}" for name, line in refused.fields.items())
    return f"{prefix} {refused.message}" + (f" ({fields})" if fields else "")


@showing_toolset.tool
async def showing_book(
    ctx: RunContext[SerniaDeps],
    unit: str,
    start: str,
    name: str,
    mobile: str = "",
    email: str = "",
    texts_accepted: bool = False,
    zillow_thread_id: str | None = None,
    effect: str | None = None,
) -> str:
    """Book a person into one of a unit's open times, as the Booking settings tab's "Add a booking" does.

    Books only what a person in the chat asked for. The first call books
    nothing: it answers with what the booking would do and who would be told
    (the host's calendar, the unit's residents, the prospect). Call again
    with the same arguments and that exact answer as effect; a person
    approves it before anything is booked. If the answer changed meanwhile,
    call again with the new one.

    Args:
        unit: The unit as showings_read names it, e.g. "12-A".
        start: One of the unit's open times, exactly as showings_read lists it, e.g. "2027-04-14T16:00:00-04:00".
        name: The prospect's name.
        mobile: The prospect's US mobile number, if the person in the chat gave it. Leave empty otherwise.
        email: The prospect's email address, if the person in the chat gave it. They get a confirmation email there. Leave empty otherwise.
        texts_accepted: True only when the person in the chat says the prospect agreed to texts about the showing. Never decide it yourself from an email, a text thread or what the prospect wrote. Needs a mobile. Without it the prospect gets no texts at all.
        zillow_thread_id: The Gmail thread id of the lead's Zillow email, when the prospect is a Zillow lead, so the lead agent sees the booking as the thread's own showing. Leave empty otherwise.
        effect: Leave empty at first. Then the exact answer of the first call, so the person approving sees it.
    """
    now = clock()
    thread_id = zillow_thread_id.strip() if zillow_thread_id and zillow_thread_id.strip() else None
    request = team_actions.TeamBookingRequest(
        unit=unit, start=start, name=name, mobile=mobile, email=email, texts=texts_accepted
    )
    try:
        preview = await effects.preview_booking(request, now=now, zillow_thread_id=thread_id)
    except Refused as refused:
        answer = _refusal("Not booked.", refused)
        if refused.code == effects.NOT_OPEN:
            answer += " Call showings_read for its open times."
        return answer
    if effect != preview.sentence:
        changed = " What it would do changed since it was asked." if effect else ""
        return (
            f"Not booked yet.{changed} {preview.sentence} A person must approve that. Call "
            f"showing_book again with the same arguments and effect={preview.sentence!r}."
        )
    if not ctx.tool_call_approved:
        raise ApprovalRequired(metadata={"effect": preview.sentence})
    try:
        booked = await team_actions.book_for_caller(
            request,
            taken_by=_who(ctx),
            now=now,
            texts=text_sender(),
            zillow_thread_id=thread_id,
            # Checked again under the booking's locks: still what the person approved.
            expected_effect=preview.sentence,
        )
    except Refused as refused:
        answer = _refusal("Not booked.", refused)
        if refused.code == effects.EFFECT_CHANGED:
            answer += " Call showing_book again without effect to get the text to approve."
        return answer
    queued = ", ".join(f"{notice.kind} ({notice.status})" for notice in booked.notices)
    confirmation = (
        f" The confirmation text was {booked.confirmation}." if booked.confirmation else ""
    )
    kind = "joined a showing already booked" if booked.kind == "join" else "a new showing"
    return (
        f"Booked {preview.name} at {preview.unit} on "
        f"{effects.when_text(preview.start, preview.timezone)} ({kind}). "
        f"showing_id {booked.showing_id}, attendee_id {booked.attendee_id}.{confirmation} "
        f"Queued: {queued or 'nothing'}."
    )


# ---------------------------------------------------------------------------
# Cancelling
# ---------------------------------------------------------------------------


def _cancel_refusal(refused: Refused) -> str:
    if refused.code in ("unknown_showing", "unknown_attendee"):
        return f"{refused.message} Call showings_read for the ids."
    if refused.code == "already_cancelled":
        return f"Nothing to cancel: {refused.message}"
    if refused.code == effects.EFFECT_CHANGED:
        return (
            f"Not cancelled. {refused.message} Call showing_cancel again without effect to get "
            "the text to approve."
        )
    return f"Not cancelled. {refused.message}"


@showing_toolset.tool
async def showing_cancel(
    ctx: RunContext[SerniaDeps],
    showing_id: int,
    attendee_id: int | None = None,
    effect: str | None = None,
) -> str:
    """Cancel a whole showing, or remove one person from it, as the Booking settings tab does.

    Cancels only what a person in the chat asked for. The first call cancels
    nothing: it answers with what would be cancelled and who would be told.
    Call again with the same arguments and that exact answer as effect; a
    person approves it before anything is cancelled. If the answer changed
    meanwhile, call again with the new one.

    Args:
        showing_id: The showing's id, from showings_read.
        attendee_id: One person's attendee_id in that showing, from showings_read, to remove only them. Leave empty to cancel the whole showing and everyone in it.
        effect: Leave empty at first. Then the exact answer of the first call, so the person approving sees it.
    """
    now = clock()
    try:
        sentence = await effects.preview_cancellation(showing_id, attendee_id, now=now)
    except Refused as refused:
        return _cancel_refusal(refused)
    if effect != sentence:
        changed = " What it would do changed since it was asked." if effect else ""
        return (
            f"Not cancelled yet.{changed} {sentence} A person must approve that. Call "
            f"showing_cancel again with the same arguments and effect={sentence!r}."
        )
    if not ctx.tool_call_approved:
        raise ApprovalRequired(metadata={"effect": sentence})
    try:
        # Checked again under the cancellation's lock: still everyone on the card, and only them.
        if attendee_id is None:
            done = await cancelling.cancel_showing(
                showing_id, now=now, texts=text_sender(), expected_effect=sentence
            )
        else:
            done = await cancelling.remove_attendee(
                showing_id, attendee_id, now=now, texts=text_sender(), expected_effect=sentence
            )
    except Refused as refused:
        return _cancel_refusal(refused)
    except cancelling.NotFound:
        return f"Not cancelled. Showing {showing_id} or that person no longer exists."
    except cancelling.Started:
        return f"Not cancelled. Showing {showing_id} has started; it can no longer be cancelled."
    if not done.attendees_cancelled:
        return "Nothing was left to cancel: someone cancelled it meanwhile."
    return (
        f"Cancelled. Showing {showing_id} is now {done.showing_status}; "
        f"{done.attendees_cancelled} {'person' if done.attendees_cancelled == 1 else 'people'} "
        f"cancelled, {done.texts_queued} cancellation "
        f"{'text' if done.texts_queued == 1 else 'texts'} queued."
    )


class ShowingTools(AbstractCapability[SerniaDeps]):
    """The showing tools and their guide, offered to the model to load when needed."""

    def __init__(self) -> None:
        self.id = CAPABILITY_ID
        self.description = DESCRIPTION
        self.defer_loading = True
        self._toolset = ErrorLoggingToolset(_UnlessDisabled(showing_toolset), name="showings")

    def get_instructions(self) -> str:
        return GUIDE

    def get_toolset(self) -> AbstractToolset[SerniaDeps]:
        return self._toolset


def showing_tools() -> ShowingTools:
    return ShowingTools()
