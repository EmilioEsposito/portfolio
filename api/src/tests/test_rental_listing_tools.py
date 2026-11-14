"""The general agent's tools for rental listing data (test database, no network).

The tools are called directly for the rules, and through the real agent with a
scripted model for the wiring: the deferred capability and the approval pause.
All unit data here is invented.
"""

import asyncio
import json
from datetime import UTC, date, time
from pathlib import Path
from types import SimpleNamespace

import pytest
import pytest_asyncio
from pydantic_ai import ApprovalRequired, DeferredToolRequests, DeferredToolResults
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel
from sqlalchemy import update

from api.src.database.database import AsyncSessionFactory
from api.src.rental_listings import service, zillow_ad
from api.src.rental_listings.models import RentalListingRecord
from api.src.rental_listings.schema import PreferredWindow, RentalListing, ShowingRules
from api.src.sernia_ai.agent import sernia_agent
from api.src.sernia_ai.deps import SerniaDeps
from api.src.sernia_ai.tools import rental_listing_tools as tools
from api.src.sernia_ai.tools.workspace_tools import workspace_tools_disabled
from api.src.tests.test_rental_listings import clear_rental_data
from api.src.tests.test_zillow_ad import FETCHED_AT, LINK, ad_page

ON_FILE = [
    RentalListing(
        unit="12-A",
        address="12 Example St #A",
        asking_rent=1500,
        public_facts=["1 bedroom, second floor", "Dishwasher"],
        internal_notes=["Resident wants 48 hours' notice where possible"],
    ),
    RentalListing(
        unit="12-B",
        asking_rent=1400,
        off_market_on=date(2026, 6, 1),
        showing_booking_url="https://book.example.com/12-b",
    ),
]
RULES = ShowingRules(blackout_dates=[date(2027, 7, 4)])


@pytest_asyncio.fixture
async def on_file():
    """Two units and one blackout date saved, and removed afterwards."""
    await clear_rental_data()
    async with AsyncSessionFactory() as session:
        for unit in ON_FILE:
            await service.save_listing(session, unit, changed_by="setup")
        await service.save_showing_rules(session, RULES, changed_by="setup")
    yield
    await clear_rental_data()


@pytest_asyncio.fixture
async def nothing_on_file():
    await clear_rental_data()
    yield
    await clear_rental_data()


def ctx(approved: bool = False):
    deps = SimpleNamespace(user_name="Pat", conversation_id="conversation-1")
    return SimpleNamespace(deps=deps, tool_call_approved=approved)


async def units() -> list[RentalListing]:
    async with AsyncSessionFactory() as session:
        return await service.read_listings(session)


async def unit(name: str) -> RentalListing:
    async with AsyncSessionFactory() as session:
        found = await service.read_listing(session, name)
    assert found is not None
    return found


async def rules() -> ShowingRules:
    async with AsyncSessionFactory() as session:
        return await service.read_showing_rules(session)


async def history(subject: str | None = None):
    async with AsyncSessionFactory() as session:
        return await service.read_history(session, subject)


@pytest.mark.usefixtures("on_file")
class TestTypedFieldsAreSavedAtOnce:
    @pytest.mark.asyncio
    async def test_marking_a_unit_rented(self):
        said = await tools.rental_listing_update(
            ctx(), unit="12-a ", off_market_on=date(2027, 6, 1)
        )

        assert said.startswith("Saved.") and "off_market_on: 2027-06-01" in said
        assert await unit("12-A") == ON_FILE[0].model_copy(
            update={"off_market_on": date(2027, 6, 1)}
        )
        assert await unit("12-B") == ON_FILE[1]

    @pytest.mark.asyncio
    async def test_the_change_is_recorded_with_who_and_which_conversation(self):
        await tools.rental_listing_update(ctx(), unit="12-A", asking_rent=1550)

        newest = (await history("12-A"))[0]

        assert newest.changed_by == "Pat (via Sernia AI)" and newest.source == "conversation-1"
        assert newest.snapshot["asking_rent"] == 1550

    @pytest.mark.asyncio
    async def test_rent_dates_virtual_tours_and_preferred_times(self):
        afternoons = PreferredWindow(days=["mon", "tue"], start=time(15), end=time(18))

        await tools.rental_listing_update(
            ctx(),
            unit="12-A",
            asking_rent=1550,
            next_lease_start_earliest=date(2027, 7, 1),
            next_lease_start_latest=date(2027, 8, 1),
            virtual_tours_allowed=True,
            preferred_showing_times=[afternoons],
        )

        saved = await unit("12-A")
        assert (saved.asking_rent, saved.virtual_tours_allowed) == (1550, True)
        assert saved.next_lease_start_latest == date(2027, 8, 1)
        assert saved.preferred_showing_times == [afternoons]

    @pytest.mark.asyncio
    async def test_a_rented_unit_is_listed_again_by_clearing_the_date(self):
        await tools.rental_listing_update(
            ctx(), unit="12-B", asking_rent=1450, clear=["off_market_on"]
        )

        saved = await unit("12-B")
        assert saved.off_market_on is None and saved.asking_rent == 1450

    @pytest.mark.asyncio
    async def test_the_showing_window_and_blackout_dates(self):
        said = await tools.showing_rules_update(
            ctx(),
            earliest_start=time(10),
            add_blackout_dates=[date(2027, 5, 31), date(2027, 7, 4)],
            remove_blackout_dates=[date(2027, 1, 1)],
        )

        saved = await rules()
        assert said.startswith("Saved.") and "10:00 to 18:00" in said
        assert (saved.earliest_start, saved.latest_start) == (time(10), time(18))
        assert saved.blackout_dates == [date(2027, 5, 31), date(2027, 7, 4)]
        assert (await history("showing_rules"))[0].changed_by == "Pat (via Sernia AI)"

    @pytest.mark.asyncio
    async def test_a_blackout_date_can_be_taken_off(self):
        await tools.showing_rules_update(ctx(), remove_blackout_dates=[date(2027, 7, 4)])
        assert (await rules()).blackout_dates == []


@pytest.mark.usefixtures("on_file")
class TestFreeTextWaitsForAPerson:
    @pytest.mark.parametrize(
        "change",
        [
            {"unit": "12-C", "create": True, "asking_rent": 1300},
            {"unit": "12-A", "address": "12 Example Street, Unit A"},
            {"unit": "12-A", "add_public_facts": ["Cats allowed"]},
            {"unit": "12-A", "remove_public_facts": ["Dishwasher"]},
            {"unit": "12-A", "add_internal_notes": ["Lease start can move to June 15"]},
            {
                "unit": "12-A",
                "remove_internal_notes": ["Resident wants 48 hours' notice where possible"],
            },
            {"unit": "12-A", "showing_booking_url": "https://book.example.com/12-a"},
            {"unit": "12-B", "showing_booking_url": ""},  # taking a link off
            {
                "unit": "12-A",
                "zillow_listing_url_public_view": "https://www.zillow.com/homedetails/x/1_zpid/",
            },
            {
                "unit": "12-A",
                "zillow_listing_url_internal_editor_view": "https://www.zillow.com/rental-manager/x",
            },
            # One text change makes the whole call wait, typed fields included.
            {"unit": "12-A", "asking_rent": 1600, "add_public_facts": ["Cats allowed"]},
        ],
    )
    @pytest.mark.asyncio
    async def test_nothing_is_saved_until_it_is_approved(self, change):
        with pytest.raises(ApprovalRequired):
            await tools.rental_listing_update(ctx(), **change)

        assert await units() == ON_FILE
        assert len(await history()) == 3  # the fixture's saves only

    @pytest.mark.asyncio
    async def test_an_approved_change_is_saved(self):
        said = await tools.rental_listing_update(
            ctx(approved=True),
            unit="12-A",
            add_public_facts=["Cats allowed", "  Tenant pays\ngas and electric ", "dishwasher"],
            remove_public_facts=["1 bedroom, second floor"],
            add_internal_notes=["Lease start can move to June 15; check with us first"],
        )

        saved = await unit("12-A")
        assert said.startswith("Saved.")
        # A fact is one line, and one already on file is not added twice.
        assert saved.public_facts == [
            "Dishwasher",
            "Cats allowed",
            "Tenant pays gas and electric",
        ]
        assert saved.internal_notes == [
            "Resident wants 48 hours' notice where possible",
            "Lease start can move to June 15; check with us first",
        ]

    @pytest.mark.asyncio
    async def test_an_approved_new_unit_is_added(self):
        await tools.rental_listing_update(
            ctx(approved=True),
            unit="12-C",
            create=True,
            address="12 Example St #C",
            asking_rent=1300,
            add_public_facts=["Studio"],
        )

        assert [item.unit for item in await units()] == ["12-A", "12-B", "12-C"]
        assert (await unit("12-C")).public_facts == ["Studio"]

    @pytest.mark.asyncio
    async def test_two_approved_creations_of_one_unit_in_different_case_make_one_listing(self):
        said = await asyncio.gather(
            tools.rental_listing_update(ctx(approved=True), unit="12-C", create=True),
            tools.rental_listing_update(ctx(approved=True), unit="12-c", create=True),
        )

        assert sorted(text.split(".")[0] for text in said) == ["Not saved", "Saved"]
        names = [item.unit.lower() for item in await units()]
        assert names == ["12-a", "12-b", "12-c"]

    @pytest.mark.asyncio
    async def test_the_shared_booking_link_waits_too(self):
        with pytest.raises(ApprovalRequired):
            await tools.showing_rules_update(ctx(), showing_booking_url="https://book.example.com")
        assert await rules() == RULES

        await tools.showing_rules_update(
            ctx(approved=True), showing_booking_url="https://book.example.com"
        )
        assert (await rules()).showing_booking_url == "https://book.example.com"


@pytest.mark.usefixtures("on_file")
class TestRefusals:
    @pytest.mark.parametrize(
        ("change", "reason"),
        [
            ({"add_public_facts": ["Call Sam at 412-555-0134"]}, "phone number"),
            ({"add_public_facts": ["Door code is 4821"]}, "access code"),
            ({"add_internal_notes": ["Lockbox code on the rail"]}, "access code"),
            ({"add_internal_notes": ["Resident: sam@example.com"]}, "email address"),
            ({"showing_booking_url": "http://book.example.com"}, "https"),
            ({"zillow_listing_url_public_view": "https://example.com/ad"}, "zillow.com"),
            ({"asking_rent": -5}, "asking_rent"),
        ],
    )
    @pytest.mark.asyncio
    async def test_a_change_that_fails_the_checks_is_refused_before_anyone_is_asked(
        self, change, reason
    ):
        # Approved or not: the checks come first, so approval cannot let it through.
        for approved in (False, True):
            said = await tools.rental_listing_update(ctx(approved), unit="12-A", **change)
            assert said.startswith("Not saved.") and reason in said

        assert await units() == ON_FILE

    @pytest.mark.asyncio
    async def test_an_unknown_unit_is_not_created_by_accident(self):
        said = await tools.rental_listing_update(ctx(), unit="12A", off_market_on=date(2027, 6, 1))

        assert said.startswith("Not saved.") and "12-A, 12-B" in said
        assert await units() == ON_FILE

    @pytest.mark.asyncio
    async def test_a_unit_on_file_is_not_created_twice(self):
        said = await tools.rental_listing_update(ctx(), unit="12-A", create=True)
        assert said.startswith("Not saved.")

    @pytest.mark.asyncio
    async def test_removing_a_line_that_is_not_on_file_says_what_is(self):
        fact = await tools.rental_listing_update(
            ctx(approved=True), unit="12-A", remove_public_facts=["Dish washer"]
        )
        note = await tools.rental_listing_update(
            ctx(approved=True), unit="12-A", remove_internal_notes=["Dishwasher"]
        )

        assert fact.startswith("Not saved.") and "Dishwasher" in fact
        assert note.startswith("Not saved.") and "48 hours" in note
        assert await units() == ON_FILE

    @pytest.mark.asyncio
    async def test_setting_and_clearing_one_field_is_refused(self):
        said = await tools.rental_listing_update(
            ctx(), unit="12-B", off_market_on=date(2027, 6, 1), clear=["off_market_on"]
        )
        assert said.startswith("Not saved.")

    @pytest.mark.asyncio
    async def test_a_time_with_a_utc_offset_is_refused(self):
        aware = time(9, 0, tzinfo=UTC)

        rules_said = await tools.showing_rules_update(ctx(), earliest_start=aware)
        listing_said = await tools.rental_listing_update(
            ctx(),
            unit="12-A",
            preferred_showing_times=[PreferredWindow(days=["mon"], start=aware, end=time(18))],
        )

        assert rules_said.startswith("Not saved.") and "no UTC offset" in rules_said
        assert listing_said.startswith("Not saved.") and "no UTC offset" in listing_said
        assert await rules() == RULES and await units() == ON_FILE

    @pytest.mark.asyncio
    async def test_two_rule_changes_at_once_both_land(self):
        await asyncio.gather(
            tools.showing_rules_update(ctx(), add_blackout_dates=[date(2027, 5, 31)]),
            tools.showing_rules_update(ctx(), earliest_start=time(10)),
        )

        saved = await rules()
        assert saved.blackout_dates == [date(2027, 5, 31), date(2027, 7, 4)]
        assert saved.earliest_start == time(10)

    @pytest.mark.asyncio
    async def test_a_window_that_ends_before_it_starts_is_refused(self):
        said = await tools.showing_rules_update(ctx(), earliest_start=time(19))

        assert said.startswith("Not saved.")
        assert await rules() == RULES

    @pytest.mark.asyncio
    async def test_a_change_that_changes_nothing_says_so_and_is_not_recorded(self):
        said = await tools.rental_listing_update(ctx(), unit="12-A", asking_rent=1500)
        same_rules = await tools.showing_rules_update(ctx(), add_blackout_dates=[date(2027, 7, 4)])

        assert said.startswith("Nothing to change")
        assert same_rules.startswith("Nothing to change")
        assert len(await history()) == 3

    @pytest.mark.asyncio
    async def test_a_row_someone_broke_by_hand_is_reported_not_built_on(self):
        async with AsyncSessionFactory() as session:
            await session.execute(
                update(RentalListingRecord)
                .where(RentalListingRecord.unit == "12-A")
                .values(public_facts=["Door code is 4821"])
            )
            await session.commit()

        changed = await tools.rental_listing_update(ctx(approved=True), unit="12-A", asking_rent=1)
        read = await tools.rental_listings_read(ctx())

        assert changed.startswith("Not saved.") and "fails its checks" in changed
        assert "fails its checks" in read and "access code" in read


class TestRead:
    @pytest.mark.usefixtures("on_file")
    @pytest.mark.asyncio
    async def test_it_returns_every_unit_and_the_rules(self):
        said = await tools.rental_listings_read(ctx())

        assert '"unit": "12-A"' in said and '"unit": "12-B"' in said
        assert "Resident wants 48 hours' notice where possible" in said
        assert '"blackout_dates": [\n      "2027-07-04"' in said

    @pytest.mark.usefixtures("nothing_on_file")
    @pytest.mark.asyncio
    async def test_with_nothing_on_file_it_says_how_to_start(self):
        said = await tools.rental_listings_read(ctx())
        assert "No rental listings are on file" in said and "create=True" in said

    @pytest.mark.usefixtures("nothing_on_file")
    @pytest.mark.asyncio
    async def test_the_first_unit_can_be_added(self):
        await tools.rental_listing_update(
            ctx(approved=True), unit="12-A", create=True, asking_rent=1500
        )
        assert [item.unit for item in await units()] == ["12-A"]


@pytest.mark.usefixtures("on_file")
class TestZillowAdRead:
    @pytest.mark.usefixtures("on_file")
    @pytest.mark.asyncio
    async def test_it_returns_the_ad_beside_what_is_on_file(self, monkeypatch):
        link = LINK
        async with AsyncSessionFactory() as session:
            await service.save_listing(
                session,
                ON_FILE[0].model_copy(update={"zillow_listing_url_public_view": link}),
                changed_by="setup",
            )
        asked: list[str] = []

        async def read_ad(listing):
            asked.append(listing.zillow_listing_url_public_view)
            return zillow_ad.parse_ad(ad_page(), url=link, fetched_at=FETCHED_AT)

        monkeypatch.setattr(tools.zillow_ad, "read_ad", read_ad)
        said = json.loads(await tools.rental_listing_zillow_ad_read(ctx(), unit="12-a"))

        assert asked == [link]
        assert said["ad"]["rent"] == 1650 and said["ad"]["for_rent"] is True
        assert said["on_file"]["asking_rent"] == 1500
        assert len(await history("12-A")) == 2  # it saved nothing

    @pytest.mark.usefixtures("on_file")
    @pytest.mark.asyncio
    async def test_with_no_link_on_file_it_says_to_ask_for_one(self):
        said = await tools.rental_listing_zillow_ad_read(ctx(), unit="12-A")

        assert said.startswith("Could not read the Zillow ad for 12-A")
        assert "ad text" in said

    @pytest.mark.usefixtures("on_file")
    @pytest.mark.asyncio
    async def test_a_refusal_from_zillow_is_reported(self, monkeypatch):
        async def refused(listing):
            raise zillow_ad.ZillowAdUnavailable("Zillow refused the request with its bot check.")

        monkeypatch.setattr(tools.zillow_ad, "read_ad", refused)
        said = await tools.rental_listing_zillow_ad_read(ctx(), unit="12-A")

        assert "bot check" in said

    @pytest.mark.usefixtures("on_file")
    @pytest.mark.asyncio
    async def test_an_unknown_unit_is_named(self):
        said = await tools.rental_listing_zillow_ad_read(ctx(), unit="99")

        assert said.startswith("No listing for a unit named '99'")


@pytest.mark.usefixtures("on_file")
class TestHistory:
    @pytest.mark.asyncio
    async def test_it_lists_changes_newest_first_with_who_made_them(self):
        await tools.rental_listing_update(ctx(), unit="12-A", asking_rent=1550)

        said = await tools.rental_listing_history(ctx(), unit="12-A")
        newest, oldest = said.split("\n\n")

        assert "12-A, by Pat (via Sernia AI)" in newest and '"asking_rent": 1550' in newest
        assert "by setup" in oldest and '"asking_rent": 1500' in oldest

    @pytest.mark.asyncio
    async def test_it_can_show_the_showing_rules_or_everything(self):
        assert "2027-07-04" in await tools.rental_listing_history(ctx(), unit="showing_rules")
        assert len((await tools.rental_listing_history(ctx())).split("\n\n")) == 3
        assert len((await tools.rental_listing_history(ctx(), limit=1)).split("\n\n")) == 1
        assert (await tools.rental_listing_history(ctx(), unit="12-Z")).startswith(
            "No saved changes"
        )


def _deps(root: Path) -> SerniaDeps:
    return SerniaDeps(
        db_session=None,  # type: ignore[arg-type] — the tools open their own sessions
        conversation_id="test-rental-listing-tools",
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
        self.instructions: list[str] = []

    def __call__(self, _messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        self.tools.append([tool.name for tool in info.function_tools])
        self.instructions.append(info.instructions or "")
        step = next(self._steps, None)
        if step is None:
            return ModelResponse(parts=[TextPart("done")])
        return ModelResponse(parts=[ToolCallPart(*step)])


def _returns(messages: list[ModelMessage]) -> dict[str, str]:
    return {
        part.tool_name: str(part.content)
        for message in messages
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, ToolReturnPart)
    }


LOAD = ("load_capability", {"id": tools.CAPABILITY_ID})


@pytest.mark.usefixtures("on_file")
class TestOnTheGeneralAgent:
    def test_the_tools_are_a_deferred_capability(self):
        capabilities = sernia_agent.root_capability.capabilities
        (capability,) = [c for c in capabilities if getattr(c, "id", None) == tools.CAPABILITY_ID]

        assert capability.defer_loading is True
        assert capability.description == tools.DESCRIPTION

    @pytest.mark.asyncio
    async def test_the_tools_appear_only_once_loaded_and_a_typed_change_is_saved(self, tmp_path):
        script = _Script(
            LOAD, ("rental_listing_update", {"unit": "12-A", "off_market_on": "2027-06-01"})
        )

        with sernia_agent.override(native_tools=[]):
            result = await sernia_agent.run(
                "12-A is rented from June 1.", deps=_deps(tmp_path), model=FunctionModel(script)
            )

        assert result.output == "done"
        assert f"- {tools.CAPABILITY_ID}: " in script.instructions[0]
        assert not any(name.startswith("rental_listing") for name in script.tools[0])
        assert {
            "rental_listings_read",
            "rental_listing_update",
            "showing_rules_update",
            "rental_listing_history",
            "rental_listing_zillow_ad_read",
        } <= set(script.tools[1])
        assert _returns(result.all_messages())["rental_listing_update"].startswith("Saved.")
        assert (await unit("12-A")).off_market_on == date(2027, 6, 1)
        assert (await history("12-A"))[0].source == "test-rental-listing-tools"

    @pytest.mark.asyncio
    async def test_a_note_pauses_the_run_for_approval_and_is_saved_once_approved(self, tmp_path):
        change = {"unit": "12-A", "add_internal_notes": ["Lease start can move to June 15"]}
        script = _Script(LOAD, ("rental_listing_update", change))

        with sernia_agent.override(native_tools=[]):
            paused = await sernia_agent.run(
                "12-A's lease start can move.", deps=_deps(tmp_path), model=FunctionModel(script)
            )
            assert isinstance(paused.output, DeferredToolRequests)
            (call,) = paused.output.approvals
            # The card a person sees is the change itself.
            assert call.tool_name == "rental_listing_update"
            assert call.args_as_dict() == change
            assert await units() == ON_FILE

            resumed = await sernia_agent.run(
                message_history=paused.all_messages(),
                deferred_tool_results=DeferredToolResults(approvals={call.tool_call_id: True}),
                deps=_deps(tmp_path),
                model=FunctionModel(script),
            )

        assert resumed.output == "done"
        assert (await unit("12-A")).internal_notes[-1] == "Lease start can move to June 15"

    @pytest.mark.asyncio
    async def test_a_run_that_must_change_nothing_gets_no_tools_even_after_loading(self, tmp_path):
        """The A/B harness clears toolsets, which does not reach a capability's tools."""
        script = _Script(LOAD)

        with sernia_agent.override(native_tools=[], toolsets=[]), workspace_tools_disabled():
            await sernia_agent.run("Hello.", deps=_deps(tmp_path), model=FunctionModel(script))

        assert len(script.tools) == 2  # before and after the load
        assert all(set(names) <= {"load_capability"} for names in script.tools)

    @pytest.mark.asyncio
    async def test_a_rejected_fact_is_not_saved(self, tmp_path):
        script = _Script(
            LOAD, ("rental_listing_update", {"unit": "12-A", "add_public_facts": ["Cats allowed"]})
        )

        with sernia_agent.override(native_tools=[]):
            paused = await sernia_agent.run(
                "12-A allows cats.", deps=_deps(tmp_path), model=FunctionModel(script)
            )
            (call,) = paused.output.approvals
            await sernia_agent.run(
                message_history=paused.all_messages(),
                deferred_tool_results=DeferredToolResults(approvals={call.tool_call_id: False}),
                deps=_deps(tmp_path),
                model=FunctionModel(script),
            )

        assert await units() == ON_FILE
