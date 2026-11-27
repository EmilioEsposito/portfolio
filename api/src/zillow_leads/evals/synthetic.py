"""Made-up scenarios: the booking service's part of a case, and a unit's Zillow ad description.

Real scenarios come from past threads and stay in the ignored ``data/``
folder. These are invented, with made-up names, units, times and tokens, so
they live in the repository: each is a ``Scenario`` and a ``CaseEdit`` in the
evals' usual form, run against ``LISTINGS``, and they cover what the case
holds only while the lead agent uses the booking service: per-lead booking
links, the lead's own showings (booked or cancelled), a showing others booked
that the lead can join, and times open at one unit only. They also cover
the policy for a lead's own showing: confirming a time by email, moving and
cancelling it, moving and reconfirming it with and without the lead's details
in, a lead who would rather not give a mobile number, and one case built as
production builds it with the switch off, which must be answered as before the
booking service existed.

``expected`` is the action a person would want, and ``why`` what a person or
the judge should look for. ``booking``, when set, is what sending the decision
should do to the lead's showings, and ``details_line`` whether the reply
should carry the line offering a text reminder. ``run --expected`` checks those
three by code (``checks.ExpectedOutcome``). ``good_reply`` (with ``good_tour``)
is a decision that keeps every exact rule, so the deterministic checks can be
run on each case without a model.

A second set, ``description_scenarios`` with ``DESCRIBED``, covers the rules
for a unit's Zillow ad description: a question answered from it, a rent, a
lease start or a tour type in the ad that differs from the unit's terms, a
public fact that corrects the ad, and an ad that contradicts itself. Its cases
are built as production builds them with the booking service off.

Write either set as files the ``run`` command reads:

    uv run python -m api.src.zillow_leads.evals synthetic --out api/src/zillow_leads/evals/data/synthetic
    uv run python -m api.src.zillow_leads.evals synthetic --set description \
        --out api/src/zillow_leads/evals/data/synthetic-description
"""

import json
from collections.abc import Callable
from datetime import date, datetime, time, timedelta
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
            virtual_tours=True,
            facts="One bedroom\nOff-street parking for one car",
        ),
    ],
)


def _at(day: int, hour: int, minute: int = 0) -> datetime:
    """A local time on April ``day``, 2027."""
    return datetime.combine(ASKED.date().replace(day=day), time(hour, minute), ZONE)


def links(token: str) -> dict[str, str]:
    return {listing.unit: f"{BASE}/{listing.unit}?t={token}" for listing in LISTINGS.listings}


def _thread(
    thread_id: str,
    *messages: tuple[int, bool, str],
    unit: str = "320-03",
    listing_data: ListingData = LISTINGS,
) -> LeadThread:
    """Messages as (minutes before ``ASKED``, from Sernia, body).

    The subject names the unit the scenario is about, as a Zillow inquiry's does.
    """
    address = next(item.address for item in listing_data.listings if item.unit == unit)
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
                subject=f"{address} inquiry",
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
# The same for a lead booked at 320-03 on Wednesday at 4:00: production's case
# leaves the lead's own showing out of the open times and the booked tours
# (``case.build_case``), so it is in neither here.
OPEN_WHILE_BOOKED = [slot for slot in OPEN if (slot.unit, slot.start) != ("320-03", _at(14, 16))]


def scenarios() -> list[Synthetic]:
    alex, sam, jo, lee, kim = (
        "Zq3xV9mK2pL7sT4wY8bN1c",
        "Hd8rW2nQ5vJ1kF6tG9mX3a",
        "Pe4bL7cR1yN8uT2wM5zK9d",
        "Ua6gS3hD8jF1kL4qW7eR2t",
        "Tb2nM8vC5xZ1aQ7wE4rY6u",
    )
    max_, ash, pat, rio, dee, eli, vic, uma = (
        "Kc5vB9nM2xL8qW4eR7tY1u",
        "Wf3gH6jK9lZ2xC5vB8nM1q",
        "Rj7kL1zX4cV8bN2mQ5wE9t",
        "Ym4nB7vC1xZ5aS8dF2gH6j",
        "Qa9sD2fG5hJ8kL1zX4cV7b",
        "Nm6bV3cX9zL2kJ5hG8fD1s",
        "Jp8rT4yU1iO6pA3sD9fG2h",
        "Lw5eR9tY3uI7oP1aS4dF8g",
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
                unit_offered=OPEN_WHILE_BOOKED,
                booking_links=links(dee),
                own_showings=[booked_alex.model_copy()],
                note="Made up: the lead booked through their link, with their details.",
            ),
            expected="reply",
            why=(
                "Confirm the existing time, 'Wednesday, April 14 at 4:00 PM', and nothing else. "
                "No details line: the lead's details are in, and their link would only say "
                "they are booked."
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
                unit_offered=OPEN_WHILE_BOOKED,
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
                unit_offered=OPEN_WHILE_BOOKED,
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
                unit_offered=OPEN_WHILE_BOOKED,
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
                    unit="412-01",
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
                    "synthetic-virtual-confirmed",
                    (
                        5,
                        False,
                        "I'm out of town. Could I do a virtual tour of 412 Example Ave on "
                        "Wednesday at 5?",
                    ),
                    unit="412-01",
                )
            ),
            edit=CaseEdit(
                unit_offered=OPEN,
                booking_links=links(vic),
                own_showings=[],
                note="Made up: 412-01 allows virtual tours; Wednesday 5:00 is open there.",
            ),
            expected="reply",
            why=(
                "Confirm the virtual tour, saying plainly that it is virtual, with the time "
                "once as 'Wednesday, April 14 at 5:00 PM'. No details line: a virtual tour "
                "books nothing, so the link would only open the in-person booking form."
            ),
            booking=Booking(),
            details_line=False,
            good_reply=(
                "Hello,\n\nYou're confirmed for a virtual tour of 412 Example Ave, Unit 1 on "
                "Wednesday, April 14 at 5:00 PM." + SIGNATURE
            ),
            good_unit="412-01",
            good_tour={"status": "confirmed", "start": _at(14, 17).isoformat(), "virtual": True},
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
                unit_offered=OPEN_WHILE_BOOKED,
                booking_links=links(ash),
                own_showings=[booked_alex.model_copy()],
                note="Made up: the lead is booked for Wednesday at 4:00; Thursday 11:00 is open.",
            ),
            expected="reply",
            why=(
                "Thursday at 11:00 is open at 320-03: confirm the move, stating 'Thursday, April "
                "15 at 11:00 AM' once. No details line: the showing it moves has the lead's "
                "details, and the moved one keeps them. Record the tour as rescheduled (or "
                "confirmed) at that time for 320-03, and a team note with the old and new times."
            ),
            booking=Booking(action="move", unit="320-03", start=_at(15, 11)),
            details_line=False,
            good_reply=(
                "Hello,\n\nNo problem. Your showing is now Thursday, April 15 at 11:00 AM at "
                "320 Example St, Unit 3." + SIGNATURE
            ),
            good_unit="320-03",
            good_tour={"status": "rescheduled", "start": _at(15, 11).isoformat()},
        ),
        Synthetic(
            scenario=Scenario(
                thread=_thread(
                    "synthetic-move-provisional",
                    (300, False, "Could I see 320 Example St tomorrow at 4 PM?"),
                    (
                        270,
                        True,
                        "Hello,\n\nYou're confirmed for Wednesday, April 14 at 4:00 PM at "
                        "320 Example St, Unit 3.\n\nTo get a text reminder and a calendar "
                        f"invite, add your details here: {links(uma)['320-03']}",
                    ),
                    (
                        5,
                        False,
                        "Sorry, I have to work tomorrow. Can we do Thursday at 11 instead?",
                    ),
                )
            ),
            edit=CaseEdit(
                unit_offered=OPEN_WHILE_BOOKED,
                booking_links=links(uma),
                own_showings=[provisional.model_copy()],
                note=(
                    "Made up: an email reply booked Wednesday at 4:00 and the lead has not "
                    "added details; Thursday 11:00 is open."
                ),
            ),
            expected="reply",
            why=(
                "Thursday at 11:00 is open at 320-03: confirm the move, stating 'Thursday, April "
                "15 at 11:00 AM' once, with the details line and the lead's own 320-03 link: the "
                "showing it moves has no details to keep. Record the tour as rescheduled (or "
                "confirmed) at that time for 320-03, and a team note with the old and new times."
            ),
            booking=Booking(action="move", unit="320-03", start=_at(15, 11)),
            details_line=True,
            good_reply=(
                "Hello,\n\nNo problem. Your showing is now Thursday, April 15 at 11:00 AM at "
                "320 Example St, Unit 3.\n\n"
                "To get a text reminder and a calendar invite, add your details here: "
                f"{links(uma)['320-03']}" + SIGNATURE
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
                unit_offered=OPEN_WHILE_BOOKED,
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


# Two units whose ads were copied into their listings. 530-02's ad is out of
# date: its rent, availability and tour type differ from the unit's terms, and
# a public fact corrects its laundry. 614-01's ad says two things about parking.
DESCRIBED = ListingData(
    timezone="America/New_York",
    listings=[
        Listing(
            unit="530-02",
            address="530 Example Rd, Unit 2",
            rent=1500,
            lease_start_earliest=date(2027, 6, 1),
            lease_start_latest=date(2027, 6, 1),
            booking_url=f"{BASE}/530-02",
            facts=(
                "Two bedrooms\nLaundry in the basement, shared with one other unit\n"
                "Tenant pays gas and electric"
            ),
            description=(
                "Bright two-bedroom on the second floor of a two-family house, a short "
                "walk to the park.\n\n"
                "Updated kitchen with a dishwasher and a gas range. Hardwood floors "
                "throughout. In-unit laundry.\n\n"
                "Available May 15 for $1,450/month. Virtual tours available."
            ),
        ),
        Listing(
            unit="614-01",
            address="614 Example Ln, Unit 1",
            rent=1350,
            lease_start_earliest=date(2027, 7, 1),
            lease_start_latest=date(2027, 7, 1),
            booking_url=f"{BASE}/614-01",
            facts="One bedroom",
            description=(
                "Cozy one-bedroom on the first floor with an updated bathroom.\n"
                "Off-street parking for one car in the driveway.\n"
                "Street parking only, no driveway access."
            ),
        ),
    ],
)
DESCRIBED_NOTE = "Made up: the case as production builds it, with the unit's ad copied in."
# The policy's exact answer to a virtual tour request for a unit that does not allow them.
IN_PERSON_ONLY = (
    "We are currently prioritizing in-person tours right now, since we prefer all tenants to "
    "see the apartment in person to prevent expectation mismatch. We will let you know if we "
    "start doing virtual tours (perhaps next week). In the event we do virtual tours, we "
    "prefer facetime if possible, and during business hours for the local time zone. We hope "
    "you understand, thanks!"
)


def description_scenarios() -> list[Synthetic]:
    def ask(thread_id: str, body: str, unit: str = "530-02") -> Scenario:
        return Scenario(
            thread=_thread(thread_id, (5, False, body), unit=unit, listing_data=DESCRIBED)
        )

    book = f"Click here to book an in-person showing: {BASE}/530-02"
    return [
        Synthetic(
            scenario=ask(
                "synthetic-description-answer",
                "Hi, does the apartment at 530 Example Rd have a dishwasher?",
            ),
            edit=CaseEdit(note=DESCRIBED_NOTE),
            expected="reply",
            why=(
                "The dishwasher is only in the ad description, which is part of the unit's "
                "entry: answer yes from it and give the booking link. Not an escalation."
            ),
            good_reply=(
                "Hello,\n\nYes, the apartment has a dishwasher in its updated kitchen.\n\n"
                + book
                + SIGNATURE
            ),
            good_unit="530-02",
        ),
        Synthetic(
            scenario=ask(
                "synthetic-description-terms",
                "Hi, what is the rent at 530 Example Rd, and when could I move in?",
            ),
            edit=CaseEdit(note=DESCRIBED_NOTE),
            expected="reply",
            why=(
                "Rent and lease start come only from the unit's first line: $1,500 a month, "
                "lease starts June 1, 2027. The ad's $1,450 and May 15 must not appear, the "
                "reply must not mention that the ad differs, and it is not an escalation."
            ),
            good_reply=(
                "Hello,\n\nThe rent is $1,500 per month, and the lease starts June 1, 2027.\n\n"
                + book
                + SIGNATURE
            ),
            good_unit="530-02",
        ),
        Synthetic(
            scenario=ask(
                "synthetic-description-virtual",
                "Could I do a virtual tour of 530 Example Rd? The ad says virtual tours are "
                "available.",
            ),
            edit=CaseEdit(note=DESCRIBED_NOTE),
            expected="reply",
            why=(
                "The tour type comes only from the unit's first line, which says in-person "
                "tours only: reply with the policy's exact in-person text. Do not say the ad "
                "is wrong, and do not escalate over it."
            ),
            good_reply="Hello,\n\n" + IN_PERSON_ONLY + "\n\n" + book + SIGNATURE,
            good_unit="530-02",
        ),
        Synthetic(
            scenario=ask(
                "synthetic-description-correction",
                "Hi, is the laundry at 530 Example Rd in the unit?",
            ),
            edit=CaseEdit(note=DESCRIBED_NOTE),
            expected="reply",
            why=(
                "The ad says in-unit laundry; a public fact says the laundry is in the "
                "basement, shared with one other unit. The fact corrects the ad: answer from "
                "the fact. Not a conflict to escalate."
            ),
            good_reply=(
                "Hello,\n\nThe laundry is in the basement, shared with one other unit.\n\n"
                + book
                + SIGNATURE
            ),
            good_unit="530-02",
        ),
        Synthetic(
            scenario=ask(
                "synthetic-description-conflict",
                "Hi, is there parking at 614 Example Ln?",
                unit="614-01",
            ),
            edit=CaseEdit(note=DESCRIBED_NOTE),
            expected="escalate",
            why=(
                "The ad says both off-street parking in the driveway and street parking "
                "only. Two statements in the description contradict each other, so the "
                "question goes to a person, with parking as the question."
            ),
        ),
    ]


# Each set of made-up scenarios, with the listing data its cases are built from.
SETS: dict[str, tuple[Callable[[], list[Synthetic]], ListingData]] = {
    "booking": (scenarios, LISTINGS),
    "description": (description_scenarios, DESCRIBED),
}


def write(out: Path, which: str = "booking") -> list[Path]:
    """One set's scenarios, their edits and its listing data, as the ``run`` command reads them."""
    make, listing_data = SETS[which]
    out.mkdir(parents=True, exist_ok=True)
    items = make()
    files = {
        "scenarios.jsonl": "".join(item.scenario.model_dump_json() + "\n" for item in items),
        "case-edits.json": json.dumps(
            {
                item.scenario.scenario_id: item.edit.model_dump(mode="json", exclude_none=True)
                for item in items
            },
            indent=2,
        ),
        "listings.json": listing_data.model_dump_json(indent=2),
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
