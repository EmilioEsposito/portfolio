"""Made-up scenarios for the booking service's part of a case.

Real scenarios come from past threads and stay in the ignored ``data/``
folder. These are invented, with made-up names, units, times and tokens, so
they live in the repository: each is a ``Scenario`` and a ``CaseEdit`` in the
evals' usual form, run against ``LISTINGS``, and they cover what the case
holds only while the lead agent uses the booking service: per-lead booking
links, the lead's own showings (booked or cancelled), a showing others booked
that the lead can join, and times open at one unit only.

``expected`` is what a person would want and why; it is for whoever reads a
run, not a check. ``good_reply`` is a reply that keeps every exact rule, so
the deterministic checks can be run on each case without a model.

Write them as files the ``run`` command reads:

    uv run python -m api.src.zillow_leads.evals synthetic --out api/src/zillow_leads/evals/data/synthetic
"""

import json
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel

from api.src.zillow_leads.case import BookedTour, Listing, ListingData, OwnShowing, TourSlot
from api.src.zillow_leads.evals.scenarios import CaseEdit, Scenario
from api.src.zillow_leads.thread import LeadMessage, LeadThread

ZONE = ZoneInfo("America/New_York")
# A Tuesday afternoon; the showings below are on the Wednesday and Thursday after.
ASKED = datetime(2027, 4, 13, 14, 0, tzinfo=ZONE)
BASE = "https://serniacapital.com/showings"
SIGNATURE = "\n\nSernia Capital Team"

LISTINGS = ListingData(
    timezone="America/New_York",
    listings=[
        Listing(
            unit="320-03",
            address="320 Example St, Unit 3",
            rent=1450,
            facts="Two bedrooms\nStreet parking\nLaundry in the basement",
        ),
        Listing(
            unit="412-01",
            address="412 Example Ave, Unit 1",
            rent=1600,
            facts="One bedroom\nOff-street parking for one car",
        ),
    ],
)


def _at(day: int, hour: int, minute: int = 0) -> datetime:
    """A local time on April ``day``, 2027."""
    return datetime.combine(ASKED.date().replace(day=day), time(hour, minute), ZONE)


def links(token: str) -> dict[str, str]:
    return {listing.unit: f"{BASE}/{listing.unit}?t={token}" for listing in LISTINGS.listings}


def _thread(thread_id: str, *messages: tuple[int, bool, str]) -> LeadThread:
    """Messages as (minutes before ``ASKED``, from Sernia, body)."""
    return LeadThread(
        mailbox="all@serniacapital.com",
        thread_id=thread_id,
        messages=[
            LeadMessage(
                message_id=f"{thread_id}-{number}",
                rfc_message_id=f"<{thread_id}-{number}@example.com>",
                sent_at=ASKED - timedelta(minutes=minutes),
                from_address="all@serniacapital.com" if ours else "noreply@mail.zillow.com",
                reply_to=None if ours else f"{thread_id}@convo.zillow.com",
                from_sernia=ours,
                subject="320 Example St, Unit 3 inquiry",
                body=body,
            )
            for number, (minutes, ours, body) in enumerate(messages)
        ],
    )


class Synthetic(BaseModel):
    """One made-up scenario and what a person would want from it."""

    scenario: Scenario
    edit: CaseEdit
    expected: Literal["reply", "no_reply", "escalate"]
    why: str
    good_reply: str = ""
    good_offered: list[datetime] = []
    good_unit: str | None = None


# Every scenario's open times: 320-03 and 412-01 share Wednesday at 4:00;
# 5:00 on Wednesday is open at 412-01 only; Thursday at 11:00 at 320-03 only.
OPEN = [
    TourSlot(start=_at(14, 16), unit="320-03"),
    TourSlot(start=_at(14, 16), unit="412-01"),
    TourSlot(start=_at(14, 17), unit="412-01"),
    TourSlot(start=_at(15, 11), unit="320-03"),
]


def scenarios() -> list[Synthetic]:
    alex, sam, jo, lee, kim = (
        "Zq3xV9mK2pL7sT4wY8bN1c",
        "Hd8rW2nQ5vJ1kF6tG9mX3a",
        "Pe4bL7cR1yN8uT2wM5zK9d",
        "Ua6gS3hD8jF1kL4qW7eR2t",
        "Tb2nM8vC5xZ1aQ7wE4rY6u",
    )
    booked_alex = OwnShowing(unit="320-03", start=_at(14, 16), status="booked")
    return [
        Synthetic(
            scenario=Scenario(
                thread=_thread(
                    "synthetic-own-late",
                    (
                        180,
                        False,
                        "Hi, I'm interested in 320 Example St. Is it still available?",
                    ),
                    (150, True, f"Hello,\n\nYes. Book a showing here: {links(alex)['320-03']}"),
                    (5, False, "Booked for tomorrow at 4. I might be ten minutes late, sorry!"),
                )
            ),
            edit=CaseEdit(
                unit_offered=OPEN,
                booked=[BookedTour(unit="320-03", start=_at(14, 16))],
                booking_links=links(alex),
                own_showings=[booked_alex],
                note="Made up: the lead booked through their own link.",
            ),
            expected="no_reply",
            why="The lead is booked; a late arrival needs nothing from us.",
        ),
        Synthetic(
            scenario=Scenario(
                thread=_thread(
                    "synthetic-own-parking",
                    (180, False, "Is 320 Example St available?"),
                    (150, True, f"Hello,\n\nYes. Book a showing here: {links(sam)['320-03']}"),
                    (5, False, "Thanks, I booked. Is there parking near the building?"),
                )
            ),
            edit=CaseEdit(
                unit_offered=OPEN,
                booked=[BookedTour(unit="320-03", start=_at(14, 16))],
                booking_links=links(sam),
                own_showings=[booked_alex.model_copy()],
                note="Made up: the lead is booked for Wednesday at 4:00.",
            ),
            expected="reply",
            why=(
                "Answer the parking question from the facts. The lead is already booked, so "
                "do not offer them the showing they are in."
            ),
            good_reply="Hello,\n\nYes, there is street parking near the building." + SIGNATURE,
        ),
        Synthetic(
            scenario=Scenario(
                thread=_thread(
                    "synthetic-join",
                    (5, False, "Can I see 320 Example St tomorrow afternoon?"),
                )
            ),
            edit=CaseEdit(
                unit_offered=OPEN,
                booked=[BookedTour(unit="320-03", start=_at(14, 16))],
                booking_links=links(jo),
                own_showings=[],
                note="Made up: someone else booked Wednesday at 4:00 and there is room.",
            ),
            expected="reply",
            why="Offer the showing already booked at 4:00 tomorrow, with the lead's own link.",
            good_reply=(
                "Hello,\n\nA showing is already set for tomorrow, Wednesday, at 4:00 PM. "
                f"Book your place here: {links(jo)['320-03']}" + SIGNATURE
            ),
            good_offered=[_at(14, 16)],
            good_unit="320-03",
        ),
        Synthetic(
            scenario=Scenario(
                thread=_thread(
                    "synthetic-other-unit",
                    (5, False, "Could I see 412 Example Ave on Wednesday at 5?"),
                )
            ),
            edit=CaseEdit(
                unit_offered=OPEN,
                booking_links=links(lee),
                own_showings=[],
                note="Made up: Wednesday at 5:00 is open at 412-01 only.",
            ),
            expected="reply",
            why="5:00 on Wednesday is open at 412-01; give the lead's own 412-01 link.",
            good_reply=(
                "Hello,\n\nWednesday at 5:00 PM works for 412 Example Ave. "
                f"Book it here: {links(lee)['412-01']}" + SIGNATURE
            ),
            good_offered=[_at(14, 17)],
            good_unit="412-01",
        ),
        Synthetic(
            scenario=Scenario(
                thread=_thread(
                    "synthetic-cancelled-by-team",
                    (180, False, "Is 320 Example St available?"),
                    (150, True, f"Hello,\n\nYes. Book a showing here: {links(kim)['320-03']}"),
                    (5, False, "Are we still on for Thursday at 11?"),
                )
            ),
            edit=CaseEdit(
                unit_offered=OPEN,
                booking_links=links(kim),
                own_showings=[
                    OwnShowing(
                        unit="320-03",
                        start=_at(15, 11),
                        status="cancelled",
                        cancelled_by="team",
                    )
                ],
                note="Made up: the team cancelled the lead's Thursday showing.",
            ),
            expected="reply",
            why=(
                "The team cancelled it; say so plainly and give the lead's own link to book "
                "another time. Do not confirm Thursday."
            ),
            good_reply=(
                "Hello,\n\nSorry, the Thursday 11:00 AM showing was cancelled. "
                f"You can book another time here: {links(kim)['320-03']}" + SIGNATURE
            ),
        ),
    ]


def write(out: Path) -> list[Path]:
    """The scenarios, their edits and the listing data, as the ``run`` command reads them."""
    out.mkdir(parents=True, exist_ok=True)
    items = scenarios()
    files = {
        "scenarios.jsonl": "".join(item.scenario.model_dump_json() + "\n" for item in items),
        "case-edits.json": json.dumps(
            {
                item.scenario.scenario_id: item.edit.model_dump(mode="json", exclude_none=True)
                for item in items
            },
            indent=2,
        ),
        "listings.json": LISTINGS.model_dump_json(indent=2),
        "expected.json": json.dumps(
            {
                item.scenario.scenario_id: {"expected": item.expected, "why": item.why}
                for item in items
            },
            indent=2,
        ),
    }
    for name, text in files.items():
        (out / name).write_text(text)
    return [out / name for name in files]
