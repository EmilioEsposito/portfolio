"""The general agent's tools for the lead agent's operating data (no network).

The tools are called directly for the rules, and through the real agent with a
scripted model for the wiring: the deferred capability, the approval pause and
the refusal of the ordinary file tools. All unit data here is invented.
"""

from datetime import date, time
from pathlib import Path
from types import SimpleNamespace

import pytest
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

from api.src.sernia_ai.agent import sernia_agent
from api.src.sernia_ai.config import ZILLOW_OPERATING_FILE
from api.src.sernia_ai.deps import SerniaDeps
from api.src.sernia_ai.zillow_leads import data_tools, operating_store
from api.src.sernia_ai.zillow_leads.case import (
    AgentAvailability,
    Listing,
    OperatingData,
    PreferredWindow,
    ShowingWindow,
)

ON_FILE = OperatingData(
    listings=[
        Listing(
            unit="12-A",
            address="12 Example St #A",
            rent=1500,
            facts="1 bedroom, second floor\nDishwasher",
        ),
        Listing(
            unit="12-B",
            rent=1400,
            rented_on=date(2026, 6, 1),
            booking_url="https://book.example.com/12-b",
        ),
    ],
    blackout_dates=[date(2027, 7, 4)],
)


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    operating_store.write(tmp_path, ON_FILE)
    return tmp_path


def ctx(workspace: Path, approved: bool = False):
    return SimpleNamespace(
        deps=SimpleNamespace(workspace_path=workspace), tool_call_approved=approved
    )


def on_file(workspace: Path) -> OperatingData:
    loaded = operating_store.read(workspace)
    assert loaded.problem is None
    return loaded.data


def unit(workspace: Path, name: str) -> Listing:
    (listing,) = [item for item in on_file(workspace).listings if item.unit == name]
    return listing


class TestTypedFieldsAreSavedAtOnce:
    @pytest.mark.asyncio
    async def test_marking_a_unit_rented(self, workspace):
        said = await data_tools.lead_data_update_listing(
            ctx(workspace), unit="12-a ", rented_on=date(2027, 6, 1)
        )

        assert said.startswith("Saved.") and "rented_on: 2027-06-01" in said
        assert unit(workspace, "12-A").rented_on == date(2027, 6, 1)
        assert unit(workspace, "12-A").facts == "1 bedroom, second floor\nDishwasher"
        assert unit(workspace, "12-B") == ON_FILE.listings[1]

    @pytest.mark.asyncio
    async def test_rent_dates_virtual_tours_and_preferred_times(self, workspace):
        afternoons = PreferredWindow(days=["mon", "tue"], start=time(15), end=time(18))

        await data_tools.lead_data_update_listing(
            ctx(workspace),
            unit="12-A",
            rent=1550,
            lease_start_earliest=date(2027, 7, 1),
            lease_start_latest=date(2027, 8, 1),
            virtual_tours=True,
            preferred_times=[afternoons],
        )

        listing = unit(workspace, "12-A")
        assert (listing.rent, listing.virtual_tours) == (1550, True)
        assert listing.lease_start_latest == date(2027, 8, 1)
        assert listing.preferred_times == [afternoons]

    @pytest.mark.asyncio
    async def test_a_rented_unit_is_listed_again_by_clearing_the_date(self, workspace):
        await data_tools.lead_data_update_listing(
            ctx(workspace), unit="12-B", rent=1450, clear=["rented_on"]
        )

        assert unit(workspace, "12-B").rented_on is None
        assert unit(workspace, "12-B").rent == 1450

    @pytest.mark.asyncio
    async def test_the_showing_window_and_blackout_dates(self, workspace):
        said = await data_tools.lead_data_update_showing_rules(
            ctx(workspace),
            earliest_start=time(10),
            add_blackout_dates=[date(2027, 5, 31), date(2027, 7, 4)],
            remove_blackout_dates=[date(2027, 1, 1)],
        )

        data = on_file(workspace)
        assert said.startswith("Saved.") and "10:00 to 18:00" in said
        assert data.showing_window == ShowingWindow(earliest_start=time(10), latest_start=time(18))
        assert data.blackout_dates == [date(2027, 5, 31), date(2027, 7, 4)]

    @pytest.mark.asyncio
    async def test_a_blackout_date_can_be_taken_off(self, workspace):
        await data_tools.lead_data_update_showing_rules(
            ctx(workspace), remove_blackout_dates=[date(2027, 7, 4)]
        )
        assert on_file(workspace).blackout_dates == []


class TestFreeTextWaitsForAPerson:
    @pytest.mark.parametrize(
        "change",
        [
            {"unit": "12-C", "create": True, "rent": 1300},
            {"unit": "12-A", "address": "12 Example Street, Unit A"},
            {"unit": "12-A", "add_facts": ["Cats allowed"]},
            {"unit": "12-A", "remove_facts": ["Dishwasher"]},
            {"unit": "12-A", "booking_url": "https://book.example.com/12-a"},
            {"unit": "12-B", "booking_url": ""},  # taking a link off
            # One text change makes the whole call wait, typed fields included.
            {"unit": "12-A", "rent": 1600, "add_facts": ["Cats allowed"]},
        ],
    )
    @pytest.mark.asyncio
    async def test_nothing_is_written_until_it_is_approved(self, workspace, change):
        before = (workspace / ZILLOW_OPERATING_FILE).read_text()

        with pytest.raises(ApprovalRequired):
            await data_tools.lead_data_update_listing(ctx(workspace), **change)

        assert (workspace / ZILLOW_OPERATING_FILE).read_text() == before

    @pytest.mark.asyncio
    async def test_an_approved_change_is_written(self, workspace):
        said = await data_tools.lead_data_update_listing(
            ctx(workspace, approved=True),
            unit="12-A",
            add_facts=["Cats allowed", "  Tenant pays\ngas and electric ", "dishwasher"],
            remove_facts=["1 bedroom, second floor"],
        )

        assert said.startswith("Saved.")
        # A fact is one line, and one already on file is not added twice.
        assert unit(workspace, "12-A").facts == (
            "Dishwasher\nCats allowed\nTenant pays gas and electric"
        )

    @pytest.mark.asyncio
    async def test_an_approved_new_unit_is_added(self, workspace):
        await data_tools.lead_data_update_listing(
            ctx(workspace, approved=True),
            unit="12-C",
            create=True,
            address="12 Example St #C",
            rent=1300,
            add_facts=["Studio"],
        )

        assert [item.unit for item in on_file(workspace).listings] == ["12-A", "12-B", "12-C"]
        assert unit(workspace, "12-C").facts == "Studio"

    @pytest.mark.asyncio
    async def test_the_shared_booking_link_waits_too(self, workspace):
        with pytest.raises(ApprovalRequired):
            await data_tools.lead_data_update_showing_rules(
                ctx(workspace), booking_url="https://book.example.com"
            )

        await data_tools.lead_data_update_showing_rules(
            ctx(workspace, approved=True), booking_url="https://book.example.com"
        )
        assert on_file(workspace).booking_url == "https://book.example.com"


class TestRefusals:
    @pytest.mark.parametrize(
        ("change", "reason"),
        [
            ({"add_facts": ["Call Sam at 412-555-0134"]}, "phone number"),
            ({"add_facts": ["Door code is 4821"]}, "access code"),
            ({"add_facts": ["Ask sam@example.com"]}, "email address"),
            ({"booking_url": "http://book.example.com"}, "https"),
            ({"rent": -5}, "rent"),
        ],
    )
    @pytest.mark.asyncio
    async def test_a_change_that_fails_the_checks_is_refused_before_anyone_is_asked(
        self, workspace, change, reason
    ):
        before = (workspace / ZILLOW_OPERATING_FILE).read_text()

        # Approved or not: the checks come first, so approval cannot let it through.
        for approved in (False, True):
            said = await data_tools.lead_data_update_listing(
                ctx(workspace, approved), unit="12-A", **change
            )
            assert said.startswith("Not saved.") and reason in said

        assert (workspace / ZILLOW_OPERATING_FILE).read_text() == before

    @pytest.mark.asyncio
    async def test_an_unknown_unit_is_not_created_by_accident(self, workspace):
        said = await data_tools.lead_data_update_listing(
            ctx(workspace), unit="12A", rented_on=date(2027, 6, 1)
        )

        assert said.startswith("Not saved.") and "12-A, 12-B" in said
        assert on_file(workspace) == ON_FILE

    @pytest.mark.asyncio
    async def test_a_unit_on_file_is_not_created_twice(self, workspace):
        said = await data_tools.lead_data_update_listing(ctx(workspace), unit="12-A", create=True)
        assert said.startswith("Not saved.")

    @pytest.mark.asyncio
    async def test_removing_a_fact_that_is_not_on_file_says_what_is(self, workspace):
        said = await data_tools.lead_data_update_listing(
            ctx(workspace, approved=True), unit="12-A", remove_facts=["Dish washer"]
        )

        assert said.startswith("Not saved.") and "Dishwasher" in said
        assert on_file(workspace) == ON_FILE

    @pytest.mark.asyncio
    async def test_setting_and_clearing_one_field_is_refused(self, workspace):
        said = await data_tools.lead_data_update_listing(
            ctx(workspace), unit="12-B", rented_on=date(2027, 6, 1), clear=["rented_on"]
        )
        assert said.startswith("Not saved.")

    @pytest.mark.asyncio
    async def test_a_window_that_ends_before_it_starts_is_refused(self, workspace):
        said = await data_tools.lead_data_update_showing_rules(
            ctx(workspace), earliest_start=time(19)
        )

        assert said.startswith("Not saved.")
        assert on_file(workspace) == ON_FILE

    @pytest.mark.asyncio
    async def test_a_change_that_changes_nothing_says_so(self, workspace):
        said = await data_tools.lead_data_update_listing(ctx(workspace), unit="12-A", rent=1500)
        rules = await data_tools.lead_data_update_showing_rules(
            ctx(workspace), add_blackout_dates=[date(2027, 7, 4)]
        )

        assert said.startswith("Nothing to change") and rules.startswith("Nothing to change")

    @pytest.mark.asyncio
    async def test_a_file_a_person_broke_is_left_for_a_person(self, workspace):
        (workspace / ZILLOW_OPERATING_FILE).write_text("{not json")

        for said in (
            await data_tools.lead_data_update_listing(ctx(workspace, True), unit="12-A", rent=1),
            await data_tools.lead_data_update_showing_rules(ctx(workspace), earliest_start=time(8)),
            await data_tools.lead_data_read(ctx(workspace)),
        ):
            assert "fails its checks" in said and "person" in said
        assert (workspace / ZILLOW_OPERATING_FILE).read_text() == "{not json"


class TestRead:
    @pytest.mark.asyncio
    async def test_it_returns_the_file(self, workspace):
        said = await data_tools.lead_data_read(ctx(workspace))

        assert operating_store.parse(said) == ON_FILE

    @pytest.mark.asyncio
    async def test_with_no_file_it_says_how_to_start(self, tmp_path):
        said = await data_tools.lead_data_read(ctx(tmp_path))
        assert "Nothing is on file" in said and "create=True" in said

    @pytest.mark.asyncio
    async def test_the_first_unit_creates_the_file(self, tmp_path):
        await data_tools.lead_data_update_listing(
            ctx(tmp_path, approved=True), unit="12-A", create=True, rent=1500
        )
        assert [item.unit for item in on_file(tmp_path).listings] == ["12-A"]

    @pytest.mark.asyncio
    async def test_hand_kept_tour_times_are_pointed_out_as_unused(self, workspace):
        pattern = [AgentAvailability(agent="Alex", weekday_times=[time(10)])]
        operating_store.write(workspace, ON_FILE.model_copy(update={"availability": pattern}))

        said = await data_tools.lead_data_read(ctx(workspace))

        assert "does not use them" in said


def _deps(root: Path) -> SerniaDeps:
    return SerniaDeps(
        db_session=None,  # type: ignore[arg-type] — no tool here touches the DB
        conversation_id="test-lead-data-tools",
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


LOAD = ("load_capability", {"id": data_tools.CAPABILITY_ID})


class TestOnTheGeneralAgent:
    def test_the_tools_are_a_deferred_capability(self):
        capabilities = sernia_agent.root_capability.capabilities
        (capability,) = [
            c for c in capabilities if getattr(c, "id", None) == data_tools.CAPABILITY_ID
        ]

        assert capability.defer_loading is True
        assert capability.description == data_tools.DESCRIPTION

    @pytest.mark.asyncio
    async def test_the_tools_appear_only_once_loaded_and_a_typed_change_is_saved(self, workspace):
        script = _Script(
            LOAD, ("lead_data_update_listing", {"unit": "12-A", "rented_on": "2027-06-01"})
        )

        with sernia_agent.override(native_tools=[]):
            result = await sernia_agent.run(
                "12-A is rented from June 1.", deps=_deps(workspace), model=FunctionModel(script)
            )

        assert result.output == "done"
        assert f"- {data_tools.CAPABILITY_ID}: " in script.instructions[0]
        assert not any(name.startswith("lead_data_") for name in script.tools[0])
        assert {
            "lead_data_read",
            "lead_data_update_listing",
            "lead_data_update_showing_rules",
        } <= set(script.tools[1])
        assert _returns(result.all_messages())["lead_data_update_listing"].startswith("Saved.")
        assert unit(workspace, "12-A").rented_on == date(2027, 6, 1)

    @pytest.mark.asyncio
    async def test_a_fact_pauses_the_run_for_approval_and_is_written_once_approved(self, workspace):
        script = _Script(
            LOAD, ("lead_data_update_listing", {"unit": "12-A", "add_facts": ["Cats allowed"]})
        )

        with sernia_agent.override(native_tools=[]):
            paused = await sernia_agent.run(
                "12-A allows cats.", deps=_deps(workspace), model=FunctionModel(script)
            )
            assert isinstance(paused.output, DeferredToolRequests)
            (call,) = paused.output.approvals
            # The card a person sees is the change itself.
            assert call.tool_name == "lead_data_update_listing"
            assert call.args_as_dict() == {"unit": "12-A", "add_facts": ["Cats allowed"]}
            assert on_file(workspace) == ON_FILE

            resumed = await sernia_agent.run(
                message_history=paused.all_messages(),
                deferred_tool_results=DeferredToolResults(approvals={call.tool_call_id: True}),
                deps=_deps(workspace),
                model=FunctionModel(script),
            )

        assert resumed.output == "done"
        assert unit(workspace, "12-A").facts.endswith("\nCats allowed")

    @pytest.mark.asyncio
    async def test_a_rejected_fact_is_not_written(self, workspace):
        script = _Script(
            LOAD, ("lead_data_update_listing", {"unit": "12-A", "add_facts": ["Cats allowed"]})
        )

        with sernia_agent.override(native_tools=[]):
            paused = await sernia_agent.run(
                "12-A allows cats.", deps=_deps(workspace), model=FunctionModel(script)
            )
            (call,) = paused.output.approvals
            await sernia_agent.run(
                message_history=paused.all_messages(),
                deferred_tool_results=DeferredToolResults(approvals={call.tool_call_id: False}),
                deps=_deps(workspace),
                model=FunctionModel(script),
            )

        assert on_file(workspace) == ON_FILE

    @pytest.mark.parametrize(
        "call",
        [
            ("workspace_write_file", {"path": ZILLOW_OPERATING_FILE, "content": "{}"}),
            (
                "workspace_write_file",
                {"path": f"/workspace/areas/../{ZILLOW_OPERATING_FILE}", "content": "{}"},
            ),
            (
                "workspace_edit_file",
                {"path": ZILLOW_OPERATING_FILE, "old_text": "1500", "new_text": "500"},
            ),
            ("workspace_delete_file", {"path": f"/workspace/{ZILLOW_OPERATING_FILE}"}),
        ],
    )
    @pytest.mark.asyncio
    async def test_the_ordinary_file_tools_cannot_change_the_file(self, workspace, call):
        (workspace / "areas").mkdir()
        script = _Script(call)

        with sernia_agent.override(native_tools=[]):
            result = await sernia_agent.run(
                "Edit it.", deps=_deps(workspace), model=FunctionModel(script), output_type=str
            )

        (said,) = _returns(result.all_messages()).values()
        assert "cannot be changed with this tool" in said and "zillow-lead-data" in said
        assert on_file(workspace) == ON_FILE

    @pytest.mark.asyncio
    async def test_the_ordinary_file_tools_can_still_read_it(self, workspace):
        script = _Script(("workspace_read_file", {"path": ZILLOW_OPERATING_FILE}))

        with sernia_agent.override(native_tools=[]):
            result = await sernia_agent.run(
                "Read it.", deps=_deps(workspace), model=FunctionModel(script), output_type=str
            )

        assert "12 Example St #A" in _returns(result.all_messages())["workspace_read_file"]
