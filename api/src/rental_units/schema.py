"""What a rental unit and the showing rules look like, wherever they are stored.

These are the shapes code depends on. The database rows (``models.py``) are
read into them and every save goes through them, so a value of the wrong type
or an unknown field never reaches a reader.
"""

from datetime import date, datetime, time
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Weekday = Literal["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
_WEEKDAYS: tuple[Weekday, ...] = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


class PreferredWindow(BaseModel):
    """Start times we would like a showing to land on: a nudge, never a limit."""

    model_config = ConfigDict(extra="forbid")

    days: list[Weekday]
    start: time
    end: time  # the last preferred start time, included

    def covers(self, local: datetime) -> bool:
        return _WEEKDAYS[local.weekday()] in self.days and self.start <= local.time() <= self.end


class RentalUnit(BaseModel):
    """One unit as it stands now: what it is, what is on offer, and what we know.

    Two kinds of free text, kept apart because they are used differently:

    - ``public_facts`` may be repeated to anyone: what we would print on a
      listing.
    - ``internal_notes`` are for making decisions and are not repeated
      outside the team: a resident who wants two days' notice, a lease start
      that can move under a condition. Only low-stakes notes belong here.
      Anything that would matter if it leaked (an access code, a phone number)
      is refused by ``checks.py`` in both.
    """

    model_config = ConfigDict(extra="forbid")

    unit: str
    address: str = ""
    rent: int | None = None
    lease_start_earliest: date | None = None
    lease_start_latest: date | None = None
    virtual_tours: bool = False
    # From this date the unit is taken and is no longer offered.
    rented_on: date | None = None
    # Where a prospect books a showing of this unit themselves.
    booking_url: str = ""
    preferred_times: list[PreferredWindow] = Field(default_factory=list)
    public_facts: list[str] = Field(default_factory=list)
    internal_notes: list[str] = Field(default_factory=list)


class ShowingRules(BaseModel):
    """The limits on showings that apply to every unit."""

    model_config = ConfigDict(extra="forbid")

    timezone: str = "America/New_York"
    # The earliest and latest time of day a showing may start.
    earliest_start: time = time(9, 0)
    latest_start: time = time(18, 0)
    blackout_dates: list[date] = Field(default_factory=list)
    # The booking page for any unit without its own link.
    booking_url: str = ""


class Change(BaseModel):
    """One saved change, as the history shows it."""

    subject: str  # the unit's name, or "showing_rules"
    changed_at: datetime
    changed_by: str
    source: str | None
    snapshot: dict  # the unit, or the rules, as they were after the change
