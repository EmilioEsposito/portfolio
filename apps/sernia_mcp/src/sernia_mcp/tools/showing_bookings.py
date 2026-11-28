"""MCP tools for showings, matching the Sernia agent's.

The same three tools and parameter names as
``api/src/sernia_ai/tools/showing_tools.py``:

- ``showings_read`` reads tour times, booking links and the showings booked.
- ``showing_book`` books a person into one of a unit's open times.
- ``showing_cancel`` cancels a whole showing, or removes one person from it.

Booking and cancelling reach people outside the team: the prospect's
confirmation or cancellation text and calendar invite, the residents' text,
the host's calendar. So each returns an Approve / Reject card and does nothing
until a person clicks Approve (``tools/approvals.py`` says how a card works).

The card shows one paragraph: what the click does and who is told. The API
writes it (``api/src/showing_bookings/effects.py``), and it is the same
paragraph the Sernia agent's approval card shows, so the two cannot drift.
On Approve it is sent back with the request; if it no longer describes what
would happen (someone else took the time, a person left), the API does
nothing and the card says to ask again.

One difference from the agent's tools: no ``effect`` parameter. The agent's
chat card shows only a call's arguments, so its model passes the paragraph
back as an argument. A card here shows it directly.

There is no tool for another host or for who came. Neither harness has one;
a person does those on the Booking settings tab.

The docstrings are read by the model: they are prompts.
"""

from __future__ import annotations

import json
from typing import Any

from fastmcp.exceptions import ToolError
from fastmcp.server.dependencies import get_access_token
from mcp.types import ToolAnnotations
from prefab_ui import PrefabApp
from prefab_ui.components import (
    Card,
    CardContent,
    CardFooter,
    CardHeader,
    CardTitle,
    Column,
    Heading,
    Text,
)

from sernia_mcp.core import showing_bookings as core
from sernia_mcp.core.errors import CoreError
from sernia_mcp.server import mcp
from sernia_mcp.tools.approvals import (
    _decision_buttons,
    _queue,
    _require_cards,
    _take,
    approvals_app,
)

# The read changes nothing about a showing. With a Zillow thread id it saves that
# thread's booking link token the first time, so it is not marked read-only.
_READS = ToolAnnotations(
    read_only_hint=False, destructive_hint=False, idempotent_hint=True, open_world_hint=False
)


def _caller() -> str:
    """The signed-in user's Clerk token, which the API checks and books under."""
    token = get_access_token()
    if token is None or not token.token:
        raise ToolError("Showings need a signed-in user.")
    return token.token


@mcp.tool(annotations=_READS)
async def showings_read(unit: str | None = None, zillow_thread_id: str | None = None) -> str:
    """Read tour times and showings from the booking service. Changes nothing but a thread's token.

    Returns, as JSON: the send mode and whether public booking is on; for each
    unit on offer (or just `unit`), its booking link or why it has none, and
    its open times (start, when in local words, a new showing or joining one,
    places left, host), a few per unit, more when you name one unit; the
    showings booked in the next 14 days that have not started, with each
    person's attendee_id, name, mobile, email and whether they take texts; and
    how each showing agent's calendar was read.

    Tour times and booking links come only from here, not from the listing.
    Read this before telling anyone about a tour, and before asking the team
    when someone is free. To answer a lead about a tour, give them the unit's
    booking link, where they pick a time themselves; do not list times for
    them to choose from. When a unit has no link the answer says why: tell the
    person, and do not make up a link or a time.

    Args:
        unit: One unit, e.g. "12-A", for all of its open times. Leave empty for every unit on offer.
        zillow_thread_id: The Gmail thread id of a Zillow lead's email. Adds that thread's own booking link for each unit, which ties a booking to the thread, and the showings the thread has. Give a Zillow lead that link, not the plain one. Reading it saves the thread's link token the first time.
    """
    try:
        document = await core.read_showings(_caller(), unit, zillow_thread_id)
    except CoreError as error:
        raise ToolError(f"showings_read: {error}") from error
    return json.dumps(document, indent=2, ensure_ascii=False)


def _card(title: str, effect: str, **buttons: Any) -> PrefabApp:
    """A card that says what Approve does, in the API's own paragraph."""
    with Card(css_class="max-w-xl") as view:
        with CardHeader():
            CardTitle(title)
        with CardContent():
            with Column(gap=2):
                Heading("What this does", level=4)
                Text(effect)
        with CardFooter():
            _decision_buttons(**buttons)
    return PrefabApp(view=view, state={"decided": False})


# -----------------------------------------------------------------------------
# Booking
# -----------------------------------------------------------------------------


@approvals_app.ui()
async def showing_book(
    unit: str,
    start: str,
    name: str,
    mobile: str = "",
    email: str = "",
    texts_accepted: bool = False,
    zillow_thread_id: str | None = None,
) -> PrefabApp:
    """Book a person into one of a unit's open times, as the Booking settings tab's "Add a booking" does.

    Returns an Approve / Reject card that says what the booking does and who
    is told (the host's calendar, the unit's residents, the prospect). Nothing
    is booked until the person clicks Approve. Only MCP clients that can show
    the card (Claude on the web, desktop or phone) can book; any other client
    is refused and nothing is queued. Book only what the person asked for.

    There is no move tool. To move someone, book the new time first, then
    remove them from the old showing with showing_cancel. They get a
    confirmation of the new time and the team's cancellation of the old one;
    say so to the person first.

    Args:
        unit: The unit as showings_read names it, e.g. "12-A".
        start: One of the unit's open times, exactly as showings_read lists it, e.g. "2027-04-14T16:00:00-04:00".
        name: The prospect's name.
        mobile: The prospect's US mobile number, if the person gave it. Leave empty otherwise.
        email: The prospect's email address, if the person gave it. They get a calendar invite there. Leave empty otherwise.
        texts_accepted: True only when the person says the prospect agreed to texts about the showing. Never decide it yourself from an email, a text thread or what the prospect wrote. Needs a mobile. Without it the prospect gets no confirmation, reminder or cancellation by text.
        zillow_thread_id: The Gmail thread id of the lead's Zillow email, when the prospect is a Zillow lead, so the lead agent sees the booking as the thread's own showing. Leave empty otherwise.
    """
    _require_cards("Booking a showing")
    booking = {
        "unit": unit,
        "start": start,
        "name": name,
        "mobile": mobile,
        "email": email,
        "texts_accepted": texts_accepted,
        "zillow_thread_id": (zillow_thread_id or "").strip() or None,
    }
    try:
        effect = await core.preview_booking(_caller(), booking)
    except CoreError as error:
        raise ToolError(f"Not booked. {error}") from error
    pending_id = _queue("showing_book", booking=booking, effect=effect)
    return _card(
        "Book this showing?",
        effect,
        confirm_tool="_confirm_showing_book",
        pending_id=pending_id,
        approve="Approve & Book",
        done="Booked",
        failed="The booking did not complete",
        rejected="Nothing was booked",
    )


@approvals_app.tool()
async def _confirm_showing_book(pending_id: str, decision: str) -> str:
    """Called only by the booking card's buttons. Raises unless the decision was carried out."""
    request = _take(pending_id, "showing_book", "booking")
    if decision != "approve":
        return "Nothing was booked."
    try:
        return await core.book(_caller(), request["booking"], request["effect"])
    except core.EffectChangedError as changed:
        now = f"It would now: {changed.effect}" if changed.effect else str(changed)
        raise ToolError(
            f"Not booked. What it would do changed after this card was shown. {now} "
            "Ask for a new card."
        ) from changed
    except CoreError as error:
        raise ToolError(f"Not booked. {error}") from error


# -----------------------------------------------------------------------------
# Cancelling
# -----------------------------------------------------------------------------


@approvals_app.ui()
async def showing_cancel(showing_id: int, attendee_id: int | None = None) -> PrefabApp:
    """Cancel a whole showing, or remove one person from it, as the Booking settings tab does.

    Returns a card that says what would be cancelled and who would be told.
    Nothing is cancelled until the person clicks the button that cancels.
    Only MCP clients that can show the card (Claude on the web, desktop or
    phone) can cancel; any other client is refused and nothing is queued.
    Cancel only what the person asked for.

    Args:
        showing_id: The showing's id, from showings_read.
        attendee_id: One person's attendee_id in that showing, from showings_read, to remove only them. Leave empty to cancel the whole showing and everyone in it.
    """
    _require_cards("Cancelling a showing")
    try:
        effect = await core.preview_cancellation(_caller(), showing_id, attendee_id)
    except CoreError as error:
        raise ToolError(f"Not cancelled. {error}") from error
    pending_id = _queue(
        "showing_cancel", showing_id=showing_id, attendee_id=attendee_id, effect=effect
    )
    whole = attendee_id is None
    return _card(
        "Cancel this showing?" if whole else "Remove this person from the showing?",
        effect,
        confirm_tool="_confirm_showing_cancel",
        pending_id=pending_id,
        approve="Cancel the showing" if whole else "Remove them",
        approve_variant="destructive",
        done="Cancelled" if whole else "Removed",
        failed="The cancellation did not complete",
        reject="Keep it",
        reject_variant="secondary",
        rejected="Nothing was cancelled",
    )


@approvals_app.tool()
async def _confirm_showing_cancel(pending_id: str, decision: str) -> str:
    """Called only by the cancellation card's buttons. Raises unless the decision was carried out."""
    request = _take(pending_id, "showing_cancel", "cancellation")
    if decision != "approve":
        return "Nothing was cancelled."
    try:
        return await core.cancel(
            _caller(), request["showing_id"], request["attendee_id"], request["effect"]
        )
    except core.EffectChangedError as changed:
        now = f"It would now: {changed.effect}" if changed.effect else str(changed)
        raise ToolError(
            f"Not cancelled. What it would do changed after this card was shown. {now} "
            "Ask for a new card."
        ) from changed
    except CoreError as error:
        raise ToolError(f"Not cancelled. {error}") from error
