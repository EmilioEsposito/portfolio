"""Where a live case gets its listings and showing rules: the rental units tables.

``api/src/rental_units`` holds every unit as it stands now, and the showing
rules. This module reads them once per lead and turns them into the
``UnitData`` the case builder takes (``case.py``):

- a unit's public facts become its fact sheet, which the agent may repeat;
- its internal notes go into the case under their own label, for the agent
  to decide with and never repeat (see ``prompts/policy.md``);
- a unit with a rented date stops being offered from that date.

Tour times and booked tours are not here. They come from a ``TourSource``
(``tours.py``).

Data that cannot be read, or that fails the checks in ``rental_units``, is not
used at all: ``load`` returns nothing with the reason, and ``handle.py`` hands
the lead to a person. With no units saved yet there is no problem to report:
the agent runs with no listings and escalates on its own.

The evals do not come through here. They read a season's unit data file.
"""

from dataclasses import dataclass, field

import logfire

from api.src.database.database import AsyncSessionFactory
from api.src.rental_units import service
from api.src.rental_units.schema import RentalUnit, ShowingRules
from api.src.sernia_ai.zillow_leads.case import Listing, ShowingWindow, UnitData


@dataclass(frozen=True)
class Loaded:
    """Unit data as read, or the reason there is none."""

    data: UnitData = field(default_factory=UnitData)
    problem: str | None = None


def as_unit_data(units: list[RentalUnit], rules: ShowingRules) -> UnitData:
    """Units and rules in the shape the case builder takes."""
    return UnitData(
        timezone=rules.timezone,
        listings=[
            Listing(
                unit=unit.unit,
                address=unit.address,
                rent=unit.rent,
                lease_start_earliest=unit.lease_start_earliest,
                lease_start_latest=unit.lease_start_latest,
                virtual_tours=unit.virtual_tours,
                rented_on=unit.rented_on,
                booking_url=unit.booking_url,
                facts="\n".join(unit.public_facts),
                internal_notes="\n".join(unit.internal_notes),
                preferred_times=unit.preferred_times,
            )
            for unit in units
        ],
        booking_url=rules.booking_url,
        showing_window=ShowingWindow(
            earliest_start=rules.earliest_start, latest_start=rules.latest_start
        ),
        blackout_dates=rules.blackout_dates,
    )


async def load() -> Loaded:
    """Unit data as it stands now, for one lead's case. Never raises."""
    try:
        async with AsyncSessionFactory() as session:
            units = await service.read_units(session)
            rules = await service.read_showing_rules(session)
    except Exception as error:  # the database is down, or a row fails its checks
        logfire.exception(
            "zillow lead: unit data is not usable, every lead goes to a person: {problem}",
            problem=str(error),
        )
        return Loaded(problem=str(error) or type(error).__name__)
    if not units:
        logfire.warn("zillow lead: no rental units saved, the agent has no listings")
    return Loaded(data=as_unit_data(units, rules))
