"""Which of the team's two Quo lines sends each text (``lines.py``).

Texts to a showing agent go from the AI line, so a reply reaches Sernia AI's SMS
conversation; texts to prospects, leads and residents go from the shared team line.
"""

import importlib
import pkgutil
from datetime import timedelta

import pytest

import api.src.showing_bookings as package
from api.src.showing_bookings import delivery_checks, host_event, lines
from api.src.tests.showing_public import ALEX_MOBILE, ROBIN_MOBILE, at
from api.src.tests.test_showing_reminders import (
    JANE_MOBILE,
    book,
    live,
    robin,
    run,
    short_notice,
)
from api.src.tests.test_showing_reminders import world as reminder_world

world = reminder_world

# The host's calendar events: kinds starting host_ that are not texts.
HOST_CALENDAR_KINDS = host_event.SHOWING_LOCKED_KINDS


def every_kind() -> set[str]:
    """Every notice kind named by a constant anywhere in ``showing_bookings``."""
    kinds: set[str] = set()
    for info in pkgutil.iter_modules(package.__path__):
        module = importlib.import_module(f"{package.__name__}.{info.name}")
        for name, value in vars(module).items():
            if name.endswith("_KIND") and isinstance(value, str):
                kinds.add(value)
            elif name.endswith("_KINDS") and isinstance(value, frozenset | set):
                kinds |= {kind for kind in value if isinstance(kind, str)}
    return kinds


def test_every_text_to_a_showing_agent_goes_from_the_ai_line():
    """A new host_ text kind fails here until ``lines.AI_LINE_KINDS`` lists it."""
    host_texts = {kind for kind in every_kind() if kind.startswith("host_")} - HOST_CALENDAR_KINDS
    assert {delivery_checks.HOST_MORNING_KIND, delivery_checks.HOST_JOIN_KIND} <= host_texts
    assert host_texts == lines.AI_LINE_KINDS
    assert all(lines.line_for(kind) == "ai" for kind in host_texts)


def test_everyone_else_is_texted_from_the_team_line():
    others = {kind for kind in every_kind() if not kind.startswith("host_")}
    assert {"prospect_confirmation", "prospect_reminder", "resident_new_showing"} <= others
    assert {lines.line_for(kind) for kind in others} == {"team"}


@pytest.mark.asyncio
async def test_the_host_hears_from_the_ai_line_and_prospects_from_the_team_line(world):
    await live()
    short_notice(world)
    world.now = at(11)
    await book(world)  # 5 hours ahead: the host is not texted
    world.now = at(14, 30)
    await book(world, robin())

    assert world.texts.lines(ALEX_MOBILE) == ["team"]
    assert world.texts.lines(ROBIN_MOBILE) == ["team"]
    # Robin booked inside 2 hours: the host's text.
    assert world.texts.lines(JANE_MOBILE) == ["ai"]


@pytest.mark.asyncio
async def test_the_messages_list_says_which_line_each_text_went_from(world):
    await live()
    await book(world)
    await run(world, at(8))
    await run(world, at(8) + timedelta(minutes=1))
    assert world.texts.lines(JANE_MOBILE) == ["ai"]
    from api.src.tests.test_showing_team_actions import team_app

    async with world.client(team_app(world)) as client:
        answer = await client.get("/api/showing-bookings/notices")
    by_kind = {item["kind"]: item for item in answer.json()}
    assert by_kind[delivery_checks.HOST_MORNING_KIND]["from_line"] == "ai"
    assert by_kind[host_event.CONFIRMATION_KIND]["from_line"] == "team"
    assert by_kind[host_event.CREATE_KIND]["from_line"] is None
