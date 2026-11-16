"""Made-up scenarios for the booking service's part of a case.

Real scenarios come from past threads and stay in the ignored ``data/``
folder. These are invented, with made-up names, units, times and tokens, so
they live in the repository: each is a ``Scenario`` and a ``CaseEdit`` in the
evals' usual form, run against ``LISTINGS``, and they cover what the case
holds only while the lead agent uses the booking service: per-lead booking
links, the lead's own showings (booked or cancelled), a showing others booked
that the lead can join, and times open at one unit only. They also cover
the policy for a lead's own showing: confirming a time by email, moving and
cancelling it, reconfirming it with and without the lead's details in, a lead who would rather not give a mobile number, and one case
built as production builds it with the switch off, which must be answered as
before the booking service existed.

``expected`` is the action a person would want, and ``why`` what a person or
the judge should look for. ``booking``, when set, is what sending the decision
should do to the lead's showings, and ``details_line`` whether the reply
should carry the line offering a text reminder. ``run --expected`` checks those
three by code (``checks.ExpectedOutcome``). ``good_reply`` (with ``good_tour``)
is a decision that keeps every exact rule, so the deterministic checks can be
run on each case without a model.

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
from api.src.zillow_leads.evals.checks import Booking
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
    booking: Booking | None = None
    details_line: bool | None = None
    good_reply: str = ""
    good_offered: list[datetime] = []
    good_unit: str | None = None
    good_tour: dict = {}


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
    max_, ash, pat, rio, dee, eli = (
        "Kc5vB9nM2xL8qW4eR7tY1u",
        "Wf3gH6jK9lZ2xC5vB8nM1q",
        "Rj7kL1zX4cV8bN2mQ5wE9t",
        "Ym4nB7vC1xZ5aS8dF2gH6j",
        "Qa9sD2fG5hJ8kL1zX4cV7b",
        "Nm6bV3cX9zL2kJ5hG8fD1s",
    )
    # Booked through the lead's own link: details in. A place a confirming
    # reply booked is provisional until the lead adds them.
    booked_alex = OwnShowing(unit="320-03", start=_at(14, 16), status="booked", details_added=True)
    provisional = booked_alex.model_copy(update={"details_added": False})
    return [
        Synthetic(
            scenario=Scenario(
                thread=_thread(
                    "synthetic-reconfirm-details-added",
                    (180, False, "Is 320 Example St available?"),
                    (150, True, f"Hello,\n\nYes. Book a showing here: {links(dee)['320-03']}"),
                    (5, False, "Just checking we're still on for tomorrow at 4?"),
                )
            ),
            edit=CaseEdit(
                unit_offered=OPEN,
                booked=[BookedTour(unit="320-03", start=_at(14, 16))],
                booking_links=links(dee),
                own_showings=[booked_alex.model_copy()],
                note="Made up: the lead booked through their link, with their details.",
            ),
            expected="reply",
            why=(
                "Confirm the existing time, 'Wednesday, April 14 at 4:00 PM', and nothing else. "
                "No details line: the lead's details are in, and their link would only show "
                "the booking form again."
            ),
            booking=Booking(),
            details_line=False,
            good_reply=(
                "Hello,\n\nYes, you're confirmed for Wednesday, April 14 at 4:00 PM at "
                "320 Example St, Unit 3." + SIGNATURE
            ),
            good_unit="320-03",
            good_tour={"status": "confirmed", "start": _at(14, 16).isoformat()},
        ),
        Synthetic(
            scenario=Scenario(
                thread=_thread(
                    "synthetic-reconfirm-provisional",
                    (300, False, "Could I see 320 Example St tomorrow at 4 PM?"),
                    (
                        270,
                        True,
                        "Hello,\n\nYou're confirmed for Wednesday, April 14 at 4:00 PM at "
                        "320 Example St, Unit 3.\n\nTo get a text reminder and a calendar "
                        f"invite, add your details here: {links(eli)['320-03']}",
                    ),
                    (5, False, "Can you confirm my showing tomorrow at 4?"),
                )
            ),
            edit=CaseEdit(
                unit_offered=OPEN,
                booked=[BookedTour(unit="320-03", start=_at(14, 16))],
                booking_links=links(eli),
                own_showings=[provisional],
                note="Made up: an email reply booked the place; the lead has not added details.",
            ),
            expected="reply",
            why=(
                "Confirm the existing time once, 'Wednesday, April 14 at 4:00 PM', with the "
                "details line and the lead's own 320-03 link, since the details are not in yet."
            ),
            booking=Booking(),
            details_line=True,
            good_reply=(
                "Hello,\n\nYes, you're confirmed for Wednesday, April 14 at 4:00 PM at "
                "320 Example St, Unit 3.\n\n"
                "To get a text reminder and a calendar invite, add your details here: "
                f"{links(eli)['320-03']}" + SIGNATURE
            ),
            good_unit="320-03",
            good_tour={"status": "confirmed", "start": _at(14, 16).isoformat()},
        ),
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
            why=(
                "The lead is booked; a late arrival needs nothing from us. The tour stays "
                "confirmed: not escalated, not recorded as cancelled."
            ),
            booking=Booking(),
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
            booking=Booking(),
            details_line=False,
            good_reply="Hello,\n\nYes, there is street parking near the building." + SIGNATURE,
            good_unit="320-03",
            good_tour={"status": "confirmed", "start": _at(14, 16).isoformat()},
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
            booking=Booking(),
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
            booking=Booking(),
            good_reply=(
                "Hello,\n\nSorry, the Thursday 11:00 AM showing was cancelled. "
                f"You can book another time here: {links(kim)['320-03']}" + SIGNATURE
            ),
        ),
        Synthetic(
            scenario=Scenario(
                thread=_thread(
                    "synthetic-confirm-by-email",
                    (5, False, "Hi, could I see 320 Example St tomorrow at 4 PM?"),
                )
            ),
            edit=CaseEdit(
                unit_offered=OPEN,
                booking_links=links(max_),
                own_showings=[],
                note="Made up: the lead names a time open at 320-03 and is not booked yet.",
            ),
            expected="reply",
            why=(
                "Accept 4:00 tomorrow: confirm it once as 'Wednesday, April 14 at 4:00 PM' with "
                "the address, then the details line with the lead's own 320-03 link. Record the "
                "tour as confirmed at that time for 320-03. No other times, no booking link "
                "offered as a way to book."
            ),
            booking=Booking(action="book", unit="320-03", start=_at(14, 16)),
            details_line=True,
            good_reply=(
                "Hello,\n\nYou're confirmed for Wednesday, April 14 at 4:00 PM at "
                "320 Example St, Unit 3.\n\n"
                f"To get a text reminder and a calendar invite, add your details here: "
                f"{links(max_)['320-03']}" + SIGNATURE
            ),
            good_unit="320-03",
            good_tour={"status": "confirmed", "start": _at(14, 16).isoformat()},
        ),
        Synthetic(
            scenario=Scenario(
                thread=_thread(
                    "synthetic-move",
                    (180, False, "Is 320 Example St available?"),
                    (150, True, f"Hello,\n\nYes. Book a showing here: {links(ash)['320-03']}"),
                    (
                        5,
                        False,
                        "Something came up tomorrow. Could we move my showing to Thursday at 11?",
                    ),
                )
            ),
            edit=CaseEdit(
                unit_offered=OPEN,
                booked=[BookedTour(unit="320-03", start=_at(14, 16))],
                booking_links=links(ash),
                own_showings=[booked_alex.model_copy()],
                note="Made up: the lead is booked for Wednesday at 4:00; Thursday 11:00 is open.",
            ),
            expected="reply",
            why=(
                "Thursday at 11:00 is open at 320-03: confirm the move, stating 'Thursday, April "
                "15 at 11:00 AM' once, with the details line. Record the tour as rescheduled (or "
                "confirmed) at that time for 320-03, and a team note with the old and new times."
            ),
            booking=Booking(action="move", unit="320-03", start=_at(15, 11)),
            details_line=True,
            good_reply=(
                "Hello,\n\nNo problem. Your showing is now Thursday, April 15 at 11:00 AM at "
                "320 Example St, Unit 3.\n\n"
                f"To get a text reminder and a calendar invite, add your details here: "
                f"{links(ash)['320-03']}" + SIGNATURE
            ),
            good_unit="320-03",
            good_tour={"status": "rescheduled", "start": _at(15, 11).isoformat()},
        ),
        Synthetic(
            scenario=Scenario(
                thread=_thread(
                    "synthetic-cancel",
                    (180, False, "Is 320 Example St available?"),
                    (150, True, f"Hello,\n\nYes. Book a showing here: {links(pat)['320-03']}"),
                    (5, False, "Sorry, I can't make it tomorrow after all. Please cancel."),
                )
            ),
            edit=CaseEdit(
                unit_offered=OPEN,
                booked=[BookedTour(unit="320-03", start=_at(14, 16))],
                booking_links=links(pat),
                own_showings=[booked_alex.model_copy()],
                note="Made up: the lead is booked for Wednesday at 4:00 and cancels.",
            ),
            expected="reply",
            why=(
                "Acknowledge the cancellation once and give the lead's own 320-03 link in case "
                "they want another time. Record the tour as cancelled for 320-03. No details "
                "line, no new times offered."
            ),
            booking=Booking(action="cancel", unit="320-03"),
            details_line=False,
            good_reply=(
                "Hello,\n\nThanks for letting us know, your showing is cancelled. If you would "
                f"like another time, you can book one here: {links(pat)['320-03']}" + SIGNATURE
            ),
            good_unit="320-03",
            good_tour={"status": "cancelled"},
        ),
        Synthetic(
            scenario=Scenario(
                thread=_thread(
                    "synthetic-no-mobile",
                    (
                        5,
                        False,
                        "Can I see 320 Example St on Thursday at 11? I'd rather not give out "
                        "my cell number, is that OK?",
                    ),
                )
            ),
            edit=CaseEdit(
                unit_offered=OPEN,
                booking_links=links(rio),
                own_showings=[],
                note="Made up: Thursday 11:00 is open at 320-03; the lead offers no mobile.",
            ),
            expected="reply",
            why=(
                "Confirm Thursday, April 15 at 11:00 AM and say no number is needed. The "
                "details line may follow, but nothing may ask for a mobile number or make one "
                "a condition of the showing."
            ),
            booking=Booking(action="book", unit="320-03", start=_at(15, 11)),
            details_line=True,
            good_reply=(
                "Hello,\n\nThat's fine, no phone number is needed. You're confirmed for "
                "Thursday, April 15 at 11:00 AM at 320 Example St, Unit 3.\n\n"
                f"To get a text reminder and a calendar invite, add your details here: "
                f"{links(rio)['320-03']}" + SIGNATURE
            ),
            good_unit="320-03",
            good_tour={"status": "confirmed", "start": _at(15, 11).isoformat()},
        ),
        Synthetic(
            scenario=Scenario(
                thread=_thread(
                    "synthetic-without-booking-service",
                    (5, False, "Hi, could I see 320 Example St tomorrow at 4 PM?"),
                )
            ),
            edit=CaseEdit(
                offered=[_at(14, 16), _at(15, 11)],
                note=(
                    "Made up: built as production builds a case with the switch off: times "
                    "with no unit, no per-lead links, no own-showings section."
                ),
            ),
            expected="reply",
            why=(
                "Answered as before the booking service existed: confirm Wednesday, April 14 "
                "at 4:00 PM with the address. No details line and no link, since the case "
                "gives none."
            ),
            booking=Booking(),
            details_line=False,
            good_reply=(
                "Hello,\n\nYou're confirmed for Wednesday, April 14 at 4:00 PM at "
                "320 Example St, Unit 3." + SIGNATURE
            ),
            good_unit="320-03",
            good_tour={"status": "confirmed", "start": _at(14, 16).isoformat()},
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
                item.scenario.scenario_id: {
                    "expected": item.expected,
                    "why": item.why,
                    **(
                        {"booking": item.booking.model_dump(mode="json")}
                        if item.booking is not None
                        else {}
                    ),
                    **(
                        {"details_line": item.details_line} if item.details_line is not None else {}
                    ),
                }
                for item in items
            },
            indent=2,
        ),
    }
    for name, text in files.items():
        (out / name).write_text(text)
    return [out / name for name in files]
