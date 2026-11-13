"""Where a live case gets its tour times and booked tours.

Unit data (``unit_data.py``) holds what a person decides and that changes
slowly: the units, the showing window, blackout dates. Which times are
actually open, and which showings are already booked, change by the hour and
belong to whatever keeps the calendars. That is a ``TourSource``:

    tours = await tours_for_case(source, operating, now)
    case = build_case(thread, operating, now=now,
                      slots=tours.open_times, booked_tours=tours.booked)

The booking system (issue #368) will be the real source: open times from the
showing window minus busy time on the shared calendars, and the showings it
has booked. Until it exists the source is ``NoTours``, so a live case lists no
times and no booked tours, and the policy has the agent give the unit's
booking link or, without one, say we will check and get back.

Whatever a source returns is passed through the fixed rules in code before it
reaches the case (``within_showing_rules``): the notice period, the showing
window and blackout dates hold even if a source gets them wrong. A source that
fails gives no times. A time we cannot vouch for is never offered.

The evals do not come through here. A scenario's tour times come from the
weekly pattern in the season's operating file (``case.offerable_slots``).
"""

from datetime import datetime
from typing import Protocol

import logfire
from pydantic import BaseModel, Field

from api.src.sernia_ai.zillow_leads.case import (
    BookedTour,
    OperatingData,
    TourSlot,
    within_showing_rules,
)


class Tours(BaseModel):
    """What is known about showings at one moment."""

    # Times a new showing could start. ``agent`` is who would host; it is
    # internal and never shown to the lead.
    open_times: list[TourSlot] = Field(default_factory=list)
    # Showings already booked, so further leads can be offered the same time.
    booked: list[BookedTour] = Field(default_factory=list)


class TourSource(Protocol):
    """Something that knows the open tour times and the booked tours."""

    async def tours(self, operating: OperatingData, now: datetime) -> Tours:
        """Open times and booked tours as of ``now``.

        ``operating`` carries the showing window, blackout dates and listings
        the answer should respect. Raise when the answer is not known (a
        calendar that cannot be read, say); never guess that a time is free.
        """
        ...


class NoTours:
    """The source until the booking system exists: nothing open, nothing booked."""

    async def tours(self, operating: OperatingData, now: datetime) -> Tours:
        return Tours()


async def tours_for_case(source: TourSource, operating: OperatingData, now: datetime) -> Tours:
    """What ``source`` knows, kept to the fixed rules. Never raises."""
    try:
        found = await source.tours(operating, now)
    except Exception:
        logfire.exception("zillow lead: tour source failed, the case lists no tour times")
        return Tours()
    return Tours(
        open_times=sorted(
            (slot for slot in found.open_times if within_showing_rules(slot.start, operating, now)),
            key=lambda slot: (slot.start, slot.agent),
        ),
        booked=[tour for tour in found.booked if tour.start.tzinfo is not None],
    )
