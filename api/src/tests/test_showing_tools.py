"""The general agent's showing tools (test database, no network).

The tools are called directly for the rules, and through the real agent with a
scripted model for the wiring: the deferred capability and the approval pause.
Bookings go through the same world as the team's actions
(``test_showing_team_actions.py``): Quo is a ``RecordingTextSender``, the
calendars an ``InMemoryCalendar``. Nothing is sent anywhere. Names, numbers
and units are made up.
"""

import ast
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import pytest_asyncio
from pydantic_ai import ApprovalRequired, DeferredToolRequests, DeferredToolResults
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from sqlalchemy import delete, select

from api.src.database.database import AsyncSessionFactory
from api.src.sernia_ai.deps import SerniaDeps
from api.src.sernia_ai.general_agent import sernia_agent
from api.src.sernia_ai.tools import showing_tools as tools
from api.src.sernia_ai.tools.workspace_tools import workspace_tools_disabled
from api.src.showing_bookings import (
    bookings,
    cancelling,
    delivery_checks,
    effects,
    lead_bookings,
    lead_tours,
    open_times,
    team_actions,
)
from api.src.showing_bookings.models import ShowingAgentRecord
from api.src.showing_bookings.team_actions import TeamBookingRequest
from api.src.tests.showing_helpers import clear_showing_data, store_settings
from api.src.tests.showing_public import (
    ALEX_EMAIL,
    ALEX_MOBILE,
    CASEY_EMAIL,
    CASEY_MOBILE,
    JANE,
    NOW,
    ROBIN_EMAIL,
    ROBIN_MOBILE,
    UNIT,
    PublicWorld,
    add_world_rows,
    at,
    booking,
    install,
    rows,
    statuses,
)
from api.src.tests.test_rental_listings import clear_rental_data
from api.src.zillow_leads.listing_data import as_listing_data
from api.src.zillow_leads.models import ZillowEmailJob, ZillowLeadLink

PUBLIC = "/api/showing-bookings/public"
THREAD = "thread-tools-casey"
UNKNOWN_THREAD = "thread-tools-nobody"
WHEN = "Wed, Apr 14 at 4:00 PM"
PLACE = "320 Example St (unit 320-03)"
TEST_MODE = (
    "This is not production and bookings are in test mode: only people on the test list "
    "are sent anything; the rest is captured until a person sends it from Captured "
    "messages on the Booking settings tab."
)


async def _clear_threads() -> None:
    async with AsyncSessionFactory() as session:
        await session.execute(
            delete(ZillowLeadLink).where(ZillowLeadLink.thread_id.like("thread-tools-%"))
        )
        await session.execute(
            delete(ZillowEmailJob).where(ZillowEmailJob.message_id.like("msg-tools-%"))
        )
        await session.commit()


@pytest_asyncio.fixture
async def world(monkeypatch):
    for name in ("RAILWAY_ENVIRONMENT_NAME", "TURNSTILE_SECRET_KEY", "SHOWING_PUBLIC_BASE_URL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("SHOWING_TEST_PHONES", f"{ALEX_MOBILE},{ROBIN_MOBILE},{CASEY_MOBILE}")
    monkeypatch.setenv("SHOWING_TEST_EMAILS", f"{ALEX_EMAIL},{ROBIN_EMAIL},{CASEY_EMAIL},{JANE}")
    open_times.forget_free_busy()
    await clear_showing_data()
    await clear_rental_data()
    await _clear_threads()
    state = PublicWorld()
    install(state, monkeypatch)
    monkeypatch.setattr(cancelling, "clock", lambda: state.now)
    monkeypatch.setattr(tools, "clock", lambda: state.now)
    monkeypatch.setattr(tools, "text_sender", lambda: state.texts)
    await add_world_rows(state)
    await store_settings(send_mode="test")
    yield state
    open_times.forget_free_busy()
    await clear_showing_data()
    await clear_rental_data()
    await _clear_threads()


def ctx(approved: bool = False):
    deps = SimpleNamespace(
        user_name="Pat", user_email="pat@serniacapital.com", conversation_id="conversation-1"
    )
    return SimpleNamespace(deps=deps, tool_call_approved=approved)


def effect_in(answer: str) -> str:
    """The effect a first call states, as the model would copy it."""
    return ast.literal_eval(answer.split("effect=", 1)[1].removesuffix("."))


async def page_book(world: PublicWorld, **changes) -> None:
    async with world.client() as client:
        answer = await client.post(f"{PUBLIC}/units/{UNIT}/bookings", json=booking(**changes))
    assert answer.status_code == 201, answer.text


async def read(**args) -> dict:
    open_times.forget_free_busy()
    return json.loads(await tools.showings_read(ctx(), **args))


async def add_lead_thread(thread_id: str) -> None:
    """A Zillow lead's thread the lead agent has handled: one email job."""
    async with AsyncSessionFactory() as session:
        session.add(
            ZillowEmailJob(
                message_id=f"msg-tools-{thread_id}",
                thread_id=thread_id,
                state="done",
                created_at=NOW,
                next_attempt_at=NOW,
                expires_at=NOW,
            )
        )
        await session.commit()


async def thread_token(thread_id: str) -> str | None:
    async with AsyncSessionFactory() as session:
        return await session.scalar(
            select(ZillowLeadLink.token).where(ZillowLeadLink.thread_id == thread_id)
        )


CASEY = {
    "unit": UNIT,
    "start": at(16).isoformat(),
    "name": "Casey Morgan",
    "mobile": "412-555-0119",
    "email": CASEY_EMAIL,
}


async def book_approved(**args) -> str:
    """The three calls of an approved booking: the effect, the approval, the booking."""
    effect = effect_in(await tools.showing_book(ctx(), **args))
    with pytest.raises(ApprovalRequired):
        await tools.showing_book(ctx(), **args, effect=effect)
    return await tools.showing_book(ctx(approved=True), **args, effect=effect)


async def cancel_approved(**args) -> str:
    effect = effect_in(await tools.showing_cancel(ctx(), **args))
    with pytest.raises(ApprovalRequired):
        await tools.showing_cancel(ctx(), **args, effect=effect)
    return await tools.showing_cancel(ctx(approved=True), **args, effect=effect)


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_read_lists_each_units_link_open_times_and_who_is_booked(world):
    await page_book(world)

    document = await read()

    assert document["sending"] == TEST_MODE
    assert document["public_booking"] is True
    (unit,) = document["units"]
    assert unit["unit"] == UNIT
    assert unit["booking_link"] == bookings.unit_url(UNIT)
    assert "no_link_because" not in unit
    # Joins come first, then the rest; a few per unit when every unit is read.
    assert {key: unit["open_times"][0][key] for key in ("when", "kind", "places_left", "host")} == {
        "when": WHEN,
        "kind": "joins a showing already booked",
        "places_left": 3,
        "host": "Jane Doe",
    }
    assert len(unit["open_times"]) == tools.TIMES_FOR_EACH_UNIT
    assert unit["more_open_times"] > 0
    (showing,) = document["booked"]
    assert (showing["unit"], showing["when"], showing["host"]) == (UNIT, WHEN, "Jane Doe")
    (alex,) = showing["people"]
    assert alex == {
        "attendee_id": alex["attendee_id"],
        "name": "Alex Parker",
        "mobile": "412-555-0117",
        "email": ALEX_EMAIL,
        "takes_texts": True,
        "booked_by": "the booking page",
    }
    assert document["agent_calendars"] == [{"name": "Jane Doe", "read": "read"}]


@pytest.mark.asyncio
async def test_naming_one_unit_lists_more_of_its_times(world):
    document = await read(unit="320-03")

    (unit,) = document["units"]
    assert len(unit["open_times"]) > tools.TIMES_FOR_EACH_UNIT


@pytest.mark.asyncio
async def test_with_no_link_the_read_says_why(world):
    await store_settings(send_mode="test", public_booking_enabled=False)
    (unit,) = (await read())["units"]
    assert unit["booking_link"] is None
    assert unit["no_link_because"].startswith("Public booking is switched off")

    await store_settings(send_mode="live")
    (unit,) = (await read())["units"]
    assert unit["booking_link"] is None
    assert unit["no_link_because"].startswith(
        "Bookings are in live mode and 320-03 is not ticked live"
    )


@pytest.mark.asyncio
async def test_with_a_thread_the_read_gives_the_threads_own_link(world):
    await add_lead_thread(THREAD)
    assert await thread_token(THREAD) is None

    thread = (await read(zillow_thread_id=THREAD))["thread"]

    token = await thread_token(THREAD)
    assert token is not None  # made on the first read
    assert thread["booking_links"] == {UNIT: lead_tours.lead_link(UNIT, token)}
    assert thread["own_showings"] == []
    assert "switch to use the booking service is off" in thread["note"]
    # The same link on a second read.
    again = (await read(zillow_thread_id=THREAD))["thread"]
    assert again["booking_links"] == thread["booking_links"]


@pytest.mark.asyncio
async def test_a_threads_link_says_why_there_is_none(world):
    await add_lead_thread(THREAD)
    await store_settings(send_mode="test", public_booking_enabled=False)

    thread = (await read(zillow_thread_id=THREAD))["thread"]

    assert thread["booking_links"][UNIT]["no_link_because"].startswith(
        "Public booking is switched off"
    )


@pytest.mark.asyncio
async def test_an_unknown_thread_gets_no_token(world):
    thread = (await read(zillow_thread_id=UNKNOWN_THREAD))["thread"]

    assert thread["problem"].startswith("No Zillow lead thread")
    assert await thread_token(UNKNOWN_THREAD) is None


# ---------------------------------------------------------------------------
# Booking
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_first_call_books_nothing_and_states_the_effect(world):
    answer = await tools.showing_book(ctx(), **CASEY, texts_accepted=True)

    assert answer.startswith("Not booked yet. Books Casey Morgan")
    assert effect_in(answer) == (
        f"Books Casey Morgan into a new showing of {PLACE} on {WHEN}, hosted by Jane Doe. "
        "Jane Doe gets a calendar event; the unit's residents get a text about the showing, "
        "if anyone lives there; Casey Morgan gets a confirmation text at 412-555-0119 and a "
        f"calendar invite at {CASEY_EMAIL}. {TEST_MODE}"
    )
    assert await rows() == ([], [], [])


@pytest.mark.asyncio
async def test_a_booking_waits_for_approval_then_books_as_the_tab_does(world):
    effect = effect_in(await tools.showing_book(ctx(), **CASEY, texts_accepted=True))

    with pytest.raises(ApprovalRequired) as raised:
        await tools.showing_book(ctx(), **CASEY, texts_accepted=True, effect=effect)
    assert raised.value.metadata == {"effect": effect}
    assert await rows() == ([], [], [])

    said = await tools.showing_book(ctx(approved=True), **CASEY, texts_accepted=True, effect=effect)

    assert said.startswith(f"Booked Casey Morgan at {UNIT} on {WHEN} (a new showing).")
    _, (casey,), notices = await rows()
    assert (casey.source, casey.mobile, casey.email, casey.texts_accepted) == (
        "team",
        CASEY_MOBILE,
        CASEY_EMAIL,
        True,
    )
    assert casey.texts_consent_page == "team:pat@serniacapital.com (via Sernia AI)"
    # The notices of a booking on the tab with texts (test_showing_team_actions.py).
    assert statuses(notices) == [
        ("prospect_confirmation", "sent"),
        ("host_event", "held"),
        ("prospect_invite", "held"),
    ]
    assert len(world.texts.to(CASEY_MOBILE)) == 1


@pytest.mark.asyncio
async def test_texts_are_off_unless_set_and_then_nothing_is_texted(world):
    effect = effect_in(await tools.showing_book(ctx(), **CASEY))
    assert "Casey Morgan gets a calendar invite at" in effect
    assert "Casey Morgan takes no texts" in effect

    await book_approved(**CASEY)

    _, (casey,), notices = await rows()
    assert (casey.texts_accepted, casey.texts_consent_at) == (False, None)
    assert statuses(notices) == [("host_event", "pending"), ("prospect_invite", "pending")]
    assert world.texts.sent == []


@pytest.mark.asyncio
async def test_an_effect_that_changed_books_nothing(world):
    effect = effect_in(await tools.showing_book(ctx(), **CASEY))
    await page_book(world)  # the time is now a join, not a new showing

    said = await tools.showing_book(ctx(approved=True), **CASEY, effect=effect)

    assert said.startswith("Not booked yet. What it would do changed since it was asked.")
    assert "already booked on" in effect_in(said)
    _, attendees, _ = await rows()
    assert [person.name for person in attendees] == ["Alex Parker"]


@pytest.mark.asyncio
async def test_a_time_that_cannot_be_booked_is_answered_not_raised(world):
    said = await tools.showing_book(ctx(), **{**CASEY, "start": at(3).isoformat()})

    assert said == (
        "Not booked. Wed, Apr 14 at 3:00 AM cannot be booked for 320-03: The time is outside "
        "the showing window. Call showings_read for its open times."
    )
    assert await rows() == ([], [], [])


@pytest.mark.asyncio
async def test_a_refusal_from_the_booking_code_is_answered_as_its_message(world):
    await page_book(world)

    said = await book_approved(**{**CASEY, "name": "Alex Again", "mobile": "412-555-0117"})

    assert said == (
        "Not booked. This number is already booked for that showing. "
        "(mobile: Already booked for this showing.)"
    )


@pytest.mark.asyncio
async def test_bad_fields_are_refused_before_anyone_is_asked(world):
    said = await tools.showing_book(ctx(), **{**CASEY, "mobile": "12"})

    assert said.startswith("Not booked. Check the marked fields. (mobile:")


# ---------------------------------------------------------------------------
# Cancelling
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cancelling_a_showing_states_the_effect_then_cancels_it(world):
    await page_book(world)
    (showing,) = (await read())["booked"]

    answer = await tools.showing_cancel(ctx(), showing_id=showing["showing_id"])

    assert effect_in(answer) == (
        f"Cancels the showing of {PLACE} on {WHEN}, hosted by Jane Doe, and everyone booked in "
        "it: Alex Parker. Texted that the team cancelled: Alex Parker. Any calendar invites "
        "they were sent are withdrawn; Jane Doe's calendar event is cancelled; the unit's "
        f"residents are texted that it is off, if they were told of it. {TEST_MODE}"
    )
    said = await cancel_approved(showing_id=showing["showing_id"])

    assert said == (
        f"Cancelled. Showing {showing['showing_id']} is now cancelled; 1 person cancelled, "
        "1 cancellation text queued."
    )
    (row,), (alex,), notices = await rows()
    assert (row.status, row.cancel_reason, alex.status) == ("cancelled", "team", "cancelled")
    assert "prospect_cancelled_by_team" in [notice.kind for notice in notices]
    assert len(world.texts.to(ALEX_MOBILE)) == 2  # the confirmation, then the cancellation


@pytest.mark.asyncio
async def test_removing_one_person_leaves_the_others(world):
    await page_book(world)
    await page_book(world, name="Robin Lee", mobile="412-555-0118", email=ROBIN_EMAIL)
    (showing,) = (await read())["booked"]
    robin = next(p for p in showing["people"] if p["name"] == "Robin Lee")
    args = {"showing_id": showing["showing_id"], "attendee_id": robin["attendee_id"]}

    effect = effect_in(await tools.showing_cancel(ctx(), **args))
    assert effect.startswith(
        f"Removes Robin Lee from the showing of {PLACE} on {WHEN}, hosted by Jane Doe. "
        "Robin Lee is texted that the team cancelled their place."
    )
    assert "Jane Doe's calendar event is updated; Alex Parker stays booked." in effect

    said = await cancel_approved(**args)

    assert said.startswith(f"Cancelled. Showing {showing['showing_id']} is now scheduled;")
    (row,), attendees, _ = await rows()
    assert row.status == "scheduled"
    assert sorted((p.name, p.status, p.cancel_reason) for p in attendees) == [
        ("Alex Parker", "booked", None),
        ("Robin Lee", "cancelled", "team"),
    ]


@pytest.mark.asyncio
async def test_cancel_refusals_are_answered(world):
    await page_book(world)
    (showing,) = (await read())["booked"]

    assert (await tools.showing_cancel(ctx(), showing_id=999999)).startswith("No showing")
    assert (
        await tools.showing_cancel(ctx(), showing_id=showing["showing_id"], attendee_id=999999)
    ).startswith("No person with attendee_id 999999")
    world.now = at(16, 5)
    assert (await tools.showing_cancel(ctx(), showing_id=showing["showing_id"])).endswith(
        "has started; it can no longer be cancelled."
    )


# ---------------------------------------------------------------------------
# A booking tied to a Zillow lead's thread
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_booking_for_a_lead_is_the_threads_own_showing(world):
    await add_lead_thread(THREAD)

    effect = effect_in(
        await tools.showing_book(ctx(), **CASEY, texts_accepted=True, zillow_thread_id=THREAD)
    )
    assert "It is tied to the lead's Zillow thread" in effect
    await book_approved(**CASEY, texts_accepted=True, zillow_thread_id=THREAD)

    _, (casey,), _ = await rows()
    assert (casey.zillow_thread_id, casey.source, casey.lead_conversation_id) == (
        THREAD,
        "team",
        None,
    )
    # Never mistaken for a place an unsent lead-agent reply booked.
    assert not delivery_checks.reply_unsettled(casey)
    # The lead agent's next case lists it as the thread's own showing ...
    found = await lead_tours.lead_booking_for(
        lead_tours.BOOKING_TOUR_SOURCE,
        THREAD,
        as_listing_data([world.listing], world.rules),
        world.now,
    )
    assert [(own.unit, own.start, own.status) for own in found.own_showings] == [
        (UNIT, at(16), "booked")
    ]
    # ... and planning, moving or cancelling from the thread finds it.
    assert await lead_bookings._thread_places(THREAD, UNIT, world.now) == [(casey.id, at(16))]
    async with AsyncSessionFactory() as session:
        moving = await lead_bookings._moving(session, [casey.id])
    assert moving.kept is not None and moving.kept.mobile == CASEY_MOBILE
    # The read marks the person and lists the thread's showing.
    document = await read(zillow_thread_id=THREAD)
    (person,) = document["booked"][0]["people"]
    assert person["this_thread"] is True
    assert document["thread"]["own_showings"] == [{"unit": UNIT, "when": WHEN, "status": "booked"}]


@pytest.mark.asyncio
async def test_an_unknown_thread_books_nothing(world):
    said = await tools.showing_book(ctx(), **CASEY, zillow_thread_id=UNKNOWN_THREAD)

    assert said.startswith("Not booked. No Zillow lead thread")
    assert await rows() == ([], [], [])


@pytest.mark.asyncio
async def test_the_previews_other_callers_use_answer_codes_and_change_nothing(world):
    """``effects`` is shared with other approval cards (the Sernia MCP service)."""
    body = {"unit": UNIT, "start": at(16).isoformat(), "name": "Casey Morgan"}

    preview = await effects.preview_booking(TeamBookingRequest(**body), now=world.now)
    assert (preview.unit, preview.start, preview.name) == (UNIT, at(16), "Casey Morgan")
    assert preview.sentence == effect_in(await tools.showing_book(ctx(), **body))

    async def code(call) -> str:
        with pytest.raises(bookings.Refused) as raised:
            await call
        return raised.value.code

    late = TeamBookingRequest(**{**body, "start": at(3).isoformat()})
    assert await code(effects.preview_booking(late, now=world.now)) == effects.NOT_OPEN
    assert (
        await code(
            effects.preview_booking(
                TeamBookingRequest(**body), now=world.now, zillow_thread_id=UNKNOWN_THREAD
            )
        )
        == "unknown_thread"
    )
    assert await code(effects.preview_cancellation(999999, now=world.now)) == "unknown_showing"
    assert await rows() == ([], [], [])


SAM = "sam.roe@example.com"


async def add_sam() -> None:
    """A second agent, after Jane by priority, free when Jane is not."""
    async with AsyncSessionFactory() as session:
        session.add(
            ShowingAgentRecord(
                name="Sam Roe",
                email=SAM,
                mobile="+14125550102",
                busy_calendar_ids=[SAM],
                priority=2,
            )
        )
        await session.commit()


@pytest.mark.asyncio
async def test_the_preview_reads_calendars_afresh_so_the_host_is_the_one_booked(world):
    await add_sam()
    effect = effect_in(await tools.showing_book(ctx(), **CASEY))
    assert "hosted by Jane Doe" in effect
    # Jane's calendar fills while the 90-second free/busy cache still says she is free.
    world.calendar.add_busy(JANE, at(16), at(16, 30))

    said = await tools.showing_book(ctx(approved=True), **CASEY, effect=effect)

    assert said.startswith("Not booked yet. What it would do changed since it was asked.")
    assert "hosted by Sam Roe" in effect_in(said)
    assert await rows() == ([], [], [])


@pytest.mark.asyncio
async def test_a_host_change_after_approval_is_refused_under_the_booking_locks(world, monkeypatch):
    await add_sam()
    effect = effect_in(await tools.showing_book(ctx(), **CASEY))
    real = team_actions.book_for_caller

    async def jane_fills_up_first(*args, **kwargs):
        world.calendar.add_busy(JANE, at(16), at(16, 30))
        return await real(*args, **kwargs)

    monkeypatch.setattr(team_actions, "book_for_caller", jane_fills_up_first)

    said = await tools.showing_book(ctx(approved=True), **CASEY, effect=effect)

    assert said.startswith(
        "Not booked. Nothing was done: what it would do changed since it was approved. "
        "It would now read: Books Casey Morgan into a new showing"
    )
    assert "hosted by Sam Roe" in said
    assert said.endswith("Call showing_book again without effect to get the text to approve.")
    assert await rows() == ([], [], [])


@pytest.mark.asyncio
async def test_someone_who_joins_after_approval_stops_the_cancellation(world, monkeypatch):
    await page_book(world)
    (showing,) = (await read())["booked"]
    effect = effect_in(await tools.showing_cancel(ctx(), showing_id=showing["showing_id"]))
    real = cancelling.cancel_showing

    async def robin_joins_first(*args, **kwargs):
        await page_book(world, name="Robin Lee", mobile="412-555-0118", email=ROBIN_EMAIL)
        return await real(*args, **kwargs)

    monkeypatch.setattr(cancelling, "cancel_showing", robin_joins_first)

    said = await tools.showing_cancel(
        ctx(approved=True), showing_id=showing["showing_id"], effect=effect
    )

    assert said.startswith(
        "Not cancelled. Nothing was done: what it would do changed since it was approved."
    )
    assert "everyone booked in it: Alex Parker and Robin Lee." in said
    (row,), attendees, notices = await rows()
    assert row.status == "scheduled"
    assert [person.status for person in attendees] == ["booked", "booked"]
    assert not [n for n in notices if n.kind == "prospect_cancelled_by_team"]


@pytest.mark.asyncio
async def test_the_shared_actions_refuse_an_effect_that_no_longer_reads_as_approved(world):
    """``expected_effect`` on the actions, for every caller that shows a person a preview."""
    body = TeamBookingRequest(unit=UNIT, start=at(16).isoformat(), name="Casey Morgan")
    current = (await effects.preview_booking(body, now=world.now)).sentence

    with pytest.raises(effects.EffectChanged) as raised:
        await team_actions.book_for_caller(
            body, taken_by="pat@serniacapital.com", now=world.now, expected_effect="Old text."
        )
    assert (raised.value.status, raised.value.code) == (409, "effect_changed")
    assert raised.value.effect == current
    assert await rows() == ([], [], [])

    await page_book(world)
    (showing,) = (await read())["booked"]
    with pytest.raises(effects.EffectChanged) as raised:
        await cancelling.cancel_showing(
            showing["showing_id"], now=world.now, texts=world.texts, expected_effect="Old text."
        )
    assert raised.value.effect == await effects.preview_cancellation(
        showing["showing_id"], now=world.now
    )
    (row,), (alex,), _ = await rows()
    assert (row.status, alex.status) == ("scheduled", "booked")


# ---------------------------------------------------------------------------
# On the general agent
# ---------------------------------------------------------------------------


def _deps(root: Path) -> SerniaDeps:
    return SerniaDeps(
        db_session=None,  # type: ignore[arg-type] — the tools open their own sessions
        conversation_id="test-showing-tools",
        user_identifier="user_test",
        user_name="Test User",
        user_email="test@serniacapital.com",
        modality="web_chat",
        workspace_path=root,
    )


class _Script:
    """A model that makes the given tool calls in order, then says "done"."""

    def __init__(self, *calls: tuple[str, dict]) -> None:
        self._steps = iter(calls)
        self.tools: list[list[str]] = []

    def __call__(self, _messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        self.tools.append([tool.name for tool in info.function_tools])
        step = next(self._steps, None)
        if step is None:
            return ModelResponse(parts=[TextPart("done")])
        return ModelResponse(parts=[ToolCallPart(*step)])


LOAD = ("load_capability", {"id": tools.CAPABILITY_ID})
TOOLS = {"showings_read", "showing_book", "showing_cancel"}


@pytest.mark.asyncio
async def test_on_the_agent_a_booking_pauses_with_its_effect_on_the_card(world, tmp_path):
    effect = effect_in(await tools.showing_book(ctx(), **CASEY))
    call = {**CASEY, "effect": effect}
    script = _Script(LOAD, ("showing_book", call))

    with sernia_agent.override(native_tools=[]):
        paused = await sernia_agent.run(
            "Book Casey.", deps=_deps(tmp_path), model=FunctionModel(script)
        )
        assert not TOOLS & set(script.tools[0])
        assert TOOLS <= set(script.tools[1])
        assert isinstance(paused.output, DeferredToolRequests)
        (pending,) = paused.output.approvals
        # The card a person sees carries the effect.
        assert pending.tool_name == "showing_book"
        assert pending.args_as_dict()["effect"] == effect
        assert await rows() == ([], [], [])

        resumed = await sernia_agent.run(
            message_history=paused.all_messages(),
            deferred_tool_results=DeferredToolResults(approvals={pending.tool_call_id: True}),
            deps=_deps(tmp_path),
            model=FunctionModel(script),
        )

    assert resumed.output == "done"
    _, (casey,), _ = await rows()
    assert casey.texts_consent_page is None
    assert casey.name == "Casey Morgan"


@pytest.mark.asyncio
async def test_a_run_that_must_change_nothing_gets_no_showing_tools(world, tmp_path):
    """The switch alone withholds them, even once the capability is loaded.

    Clearing the agent's toolsets (``override(toolsets=[])``) also drops tools a
    capability contributes, so it is left out here: this tests the switch.
    """
    script = _Script(LOAD)

    with sernia_agent.override(native_tools=[]), workspace_tools_disabled():
        await sernia_agent.run("Hello.", deps=_deps(tmp_path), model=FunctionModel(script))

    assert len(script.tools) == 2  # before and after the load
    assert all(not TOOLS & set(names) for names in script.tools)
    assert "load_capability" in script.tools[1]
