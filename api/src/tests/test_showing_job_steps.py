"""One step of the every-minute job never stops the others.

A step made to raise is logged at error level with its name, and the rest of
``holds.send_pending`` still runs: held notices are released and due notices
delivered. Bookings through the public routes, capture mode, the stand-ins;
nothing is sent anywhere.
"""

import pytest

from api.src.showing_bookings import alerts, daily_texts, holds, outbox, reminders
from api.src.showing_bookings.schema import DrainReport
from api.src.tests.showing_public import at, rows
from api.src.tests.test_showing_reminders import book, run
from api.src.tests.test_showing_reminders import world as reminder_world

world = reminder_world


class Broken(RuntimeError):
    pass


def breaks(*_args, **_kwargs):
    raise Broken("made to fail")


@pytest.fixture
def logged(monkeypatch) -> list[tuple[str, dict]]:
    found: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        holds.logfire, "exception", lambda message, **fields: found.append((message, fields))
    )
    return found


async def held_count() -> int:
    _, _, notices = await rows()
    return sum(notice.status == "held" for notice in notices)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("module", "name", "step"),
    [
        (reminders, "queue_due", "reminders"),
        (daily_texts, "queue_due", "daily_texts"),
        (alerts, "check", "alerts"),
    ],
)
async def test_a_failing_step_leaves_the_release_and_the_drain_working(
    world, monkeypatch, logged, module, name, step
):
    await book(world)  # captured confirmation; the host's event and invite held behind it
    assert await held_count() > 0
    monkeypatch.setattr(module, name, breaks)

    report = await run(world, at(14, 5, day=world.now.date()))

    assert report.released > 0
    assert report.captured > 0
    assert await held_count() == 0
    _, _, notices = await rows()
    assert {notice.status for notice in notices} <= {"captured", "sent"}
    assert logged == [("showing notices job step failed: {step}", {"step": step})]


@pytest.mark.asyncio
async def test_a_failing_release_still_lets_the_drain_deliver(world, monkeypatch, logged):
    await book(world)
    # Release by hand, then break the release step: the drain still delivers them.
    await holds.release_held(now=world.now)
    monkeypatch.setattr(holds, "release_held", breaks)

    report = await run(world, at(14, 5, day=world.now.date()))

    assert report.captured > 0
    _, _, notices = await rows()
    assert not any(notice.status == "pending" for notice in notices)
    assert logged == [("showing notices job step failed: {step}", {"step": "release_held"})]


@pytest.mark.asyncio
async def test_a_failing_drain_is_logged_and_the_alerts_still_run(world, monkeypatch, logged):
    await book(world)
    checked = []

    async def check(*, now, alerter=None):
        checked.append(now)
        return 0

    monkeypatch.setattr(outbox, "drain", breaks)
    monkeypatch.setattr(alerts, "check", check)
    report = await run(world, at(14, 5, day=world.now.date()))

    assert report.model_dump(include=set(DrainReport.model_fields)) == DrainReport().model_dump()
    assert checked == [at(14, 5, day=world.now.date())]
    assert logged == [("showing notices job step failed: {step}", {"step": "drain"})]


@pytest.mark.asyncio
async def test_with_every_step_working_nothing_is_logged(world, logged):
    await book(world)
    await run(world, at(14, 5, day=world.now.date()))
    assert logged == []
