"""Rental listing tools: what is saved at once, what waits for a card, what is refused.

The API is replaced by an in-memory fake with the same paths and answers as
``api/src/rental_listings/routes.py``; nothing leaves the process. All unit
data is invented.
"""

from __future__ import annotations

import copy
import time
from datetime import date
from typing import Any
from urllib.parse import unquote

import httpx
import pytest
import pytest_asyncio
from fastmcp import Client, FastMCP
from fastmcp.exceptions import ToolError

from sernia_mcp.core import rental_listings as core
from sernia_mcp.core.errors import ExternalServiceError, ValidationError
from sernia_mcp.tools import approvals
from sernia_mcp.tools import rental_listings as tools

LISTING = {
    "unit": "12-A",
    "address": "12 Example St #A",
    "asking_rent": 1500,
    "next_lease_start_earliest": None,
    "next_lease_start_latest": None,
    "virtual_tours_allowed": False,
    "off_market_on": None,
    "showing_booking_url": "",
    "preferred_showing_times": [],
    "public_facts": ["1 bedroom, second floor", "Dishwasher"],
    "internal_notes": ["Resident wants 48 hours' notice where possible"],
}
RULES = {
    "timezone": "America/New_York",
    "earliest_start": "09:00:00",
    "latest_start": "18:00:00",
    "blackout_dates": ["2027-07-04"],
    "showing_booking_url": "",
}


class FakeApi:
    """The API's rental listing endpoints, in memory."""

    def __init__(self) -> None:
        self.listings: dict[str, dict] = {"12-a": copy.deepcopy(LISTING)}
        self.rules = copy.deepcopy(RULES)
        self.saves: list[tuple[str, str]] = []  # (method, path) of real saves
        self.dry_runs = 0
        self.tokens: set[str] = set()

    def _refuse(self, listing: dict) -> None:
        for line in listing.get("public_facts", []) + listing.get("internal_notes", []):
            if "door code" in line.lower():
                raise core.RefusedError("Not saved.", [f"{line!r} mentions an access code"])

    async def __call__(self, method, path, token, *, json=None, params=None):
        self.tokens.add(token)
        dry = bool(params and params.get("dry_run"))
        if method == "GET" and path == "":
            return {
                "listings": [copy.deepcopy(item) for _, item in sorted(self.listings.items())],
                "showing_rules": copy.deepcopy(self.rules),
            }
        if method == "GET" and path == "/history":
            return [
                {
                    "subject": "12-A",
                    "changed_at": "2027-05-01T12:00:00Z",
                    "changed_by": "Pat <pat@serniacapital.com>",
                    "source": "Sernia MCP",
                    "snapshot": LISTING,
                }
            ]
        if method == "POST" and path == "/listings":
            listing = json["listing"]
            if listing["unit"].lower() in self.listings:
                raise core.RefusedError("Not saved.", ["already has a listing"])
            self._refuse(listing)
        elif method == "PUT" and path.startswith("/listings/"):
            key = unquote(path.removeprefix("/listings/")).lower()
            if self.listings.get(key) != json["based_on"]:
                raise core.RefusedError("Not saved.", ["Someone changed this after you loaded it."])
            listing = json["listing"]
            self._refuse(listing)
        elif method == "PUT" and path == "/showing-rules":
            if self.rules != json["based_on"]:
                raise core.RefusedError("Not saved.", ["Someone changed this after you loaded it."])
            listing = None
        else:
            raise AssertionError(f"unexpected {method} {path}")
        if dry:
            self.dry_runs += 1
            return json.get("listing") or json.get("showing_rules")
        self.saves.append((method, path))
        if listing is None:
            self.rules = json["showing_rules"]
            return self.rules
        self.listings[listing["unit"].lower()] = listing
        return listing


@pytest.fixture
def api(monkeypatch) -> FakeApi:
    fake = FakeApi()
    monkeypatch.setattr(core, "_request", fake)
    monkeypatch.setattr(tools, "_caller", lambda: ("oat_test", "pat@serniacapital.com"))
    return fake


@pytest.fixture(autouse=True)
def _clear_pending():
    approvals._PENDING.clear()
    yield
    approvals._PENDING.clear()


@pytest_asyncio.fixture
async def client():
    server = FastMCP("test_rental_listings")
    server.add_tool(tools.rental_listings_read)
    server.add_tool(tools.rental_listing_history)
    server.add_provider(approvals.approvals_app)
    async with Client(server) as connected:
        yield connected


def _actions(value):
    if isinstance(value, dict):
        if value.get("action") == "toolCall":
            yield value
        for child in value.values():
            yield from _actions(child)
    elif isinstance(value, list):
        for child in value:
            yield from _actions(child)


def _texts(value) -> str:
    """Every piece of text on a card, joined."""
    found: list[str] = []

    def walk(node):
        if isinstance(node, dict):
            for key, child in node.items():
                if key in ("content", "text") and isinstance(child, str):
                    found.append(child)
                walk(child)
        elif isinstance(node, list):
            for child in node:
                walk(child)

    walk(value)
    return "\n".join(found)


class TestPlans:
    def data(self) -> dict[str, Any]:
        return {"listings": [copy.deepcopy(LISTING)], "showing_rules": copy.deepcopy(RULES)}

    def test_a_typed_change_does_not_need_a_person(self):
        plan = core.plan_listing_change(self.data(), unit="12-a", asking_rent=1550)

        assert not plan.needs_person
        assert plan.listing["asking_rent"] == 1550 and plan.based_on == LISTING
        assert plan.changes == ["asking_rent: 1500 -> 1550"]

    @pytest.mark.parametrize(
        "change",
        [
            {"unit": "12-C", "create": True},
            {"unit": "12-A", "address": "12 Example Street"},
            {"unit": "12-A", "showing_booking_url": "https://book.example.com"},
            {"unit": "12-A", "add_public_facts": ["Cats allowed"]},
            {"unit": "12-A", "remove_public_facts": ["Dishwasher"]},
            {"unit": "12-A", "add_internal_notes": ["Lease start can move"]},
            {"unit": "12-A", "asking_rent": 1600, "add_public_facts": ["Cats allowed"]},
        ],
    )
    def test_free_text_a_link_or_a_new_listing_needs_a_person(self, change):
        assert core.plan_listing_change(self.data(), **change).needs_person

    def test_lines_are_added_once_and_removed_by_their_text(self):
        plan = core.plan_listing_change(
            self.data(),
            unit="12-A",
            add_public_facts=["Cats allowed", " Tenant pays\ngas ", "dishwasher"],
            remove_public_facts=["1 bedroom, second floor"],
        )

        assert plan.listing["public_facts"] == ["Dishwasher", "Cats allowed", "Tenant pays gas"]

    @pytest.mark.parametrize(
        ("change", "reason"),
        [
            ({"unit": "12A", "asking_rent": 1}, "No listing for a unit named '12A'"),
            ({"unit": "12-A", "create": True}, "already on file"),
            ({"unit": "12-A", "remove_public_facts": ["Dish washer"]}, "has no public fact"),
            ({"unit": "12-A", "asking_rent": 1500}, "already reads that way"),
            (
                {"unit": "12-A", "off_market_on": date(2027, 6, 1), "clear": ["off_market_on"]},
                "both set and cleared",
            ),
        ],
    )
    def test_a_change_that_cannot_be_made_as_asked_says_why(self, change, reason):
        with pytest.raises(core.RefusedError, match=reason):
            core.plan_listing_change(self.data(), **change)

    def test_clearing_a_field(self):
        data = self.data()
        data["listings"][0]["off_market_on"] = "2027-06-01"

        plan = core.plan_listing_change(data, unit="12-A", clear=["off_market_on"])

        assert plan.listing["off_market_on"] is None and not plan.needs_person

    def test_rules_changes(self):
        from datetime import time

        plan = core.plan_rules_change(
            self.data(),
            earliest_start=time(10),
            add_blackout_dates=[date(2027, 5, 31)],
            remove_blackout_dates=[date(2027, 7, 4)],
        )

        assert plan.rules["earliest_start"] == "10:00:00"
        assert plan.rules["blackout_dates"] == ["2027-05-31"]
        assert not plan.needs_person
        assert core.plan_rules_change(
            self.data(), showing_booking_url="https://book.example.com"
        ).needs_person
        with pytest.raises(core.RefusedError, match="already read that way"):
            core.plan_rules_change(self.data(), add_blackout_dates=[date(2027, 7, 4)])


class TestTools:
    @pytest.mark.asyncio
    async def test_read_returns_listings_and_rules_with_the_user_s_token(self, api, client):
        result = await client.call_tool("rental_listings_read", {})

        assert '"unit": "12-A"' in result.data and '"blackout_dates"' in result.data
        assert api.tokens == {"oat_test"}

    @pytest.mark.asyncio
    async def test_history_lists_who_and_from_where(self, api, client):
        result = await client.call_tool("rental_listing_history", {"unit": "12-A"})

        assert "by Pat <pat@serniacapital.com> via Sernia MCP" in result.data

    @pytest.mark.asyncio
    async def test_a_typed_change_is_checked_then_saved_at_once(self, api, client):
        result = await client.call_tool(
            "rental_listing_update", {"unit": "12-A", "off_market_on": "2027-06-01"}
        )

        assert api.dry_runs == 1 and api.saves == [("PUT", "/listings/12-A")]
        assert api.listings["12-a"]["off_market_on"] == "2027-06-01"
        assert "Saved: 12-A" in _texts(result.structured_content)
        assert list(_actions(result.structured_content)) == []
        assert approvals._PENDING == {}

    @pytest.mark.asyncio
    async def test_a_fact_waits_for_approve_and_then_is_saved(self, api, client):
        result = await client.call_tool(
            "rental_listing_update", {"unit": "12-A", "add_public_facts": ["Cats allowed"]}
        )

        assert api.dry_runs == 1 and api.saves == []
        assert "Add public fact: Cats allowed" in _texts(result.structured_content)
        approve = next(
            a
            for a in _actions(result.structured_content)
            if a["arguments"]["decision"] == "approve"
        )
        with pytest.raises(ToolError, match="Unknown tool"):
            await client.call_tool("_confirm_rental_change", approve["arguments"])

        said = await client.call_tool(approve["tool"], approve["arguments"])

        assert "Saved the listing for 12-A" in said.data
        assert api.listings["12-a"]["public_facts"][-1] == "Cats allowed"
        assert api.saves == [("PUT", "/listings/12-A")]

    @pytest.mark.asyncio
    async def test_a_rejected_change_is_not_saved(self, api, client):
        result = await client.call_tool(
            "rental_listing_update", {"unit": "12-C", "create": True, "asking_rent": 1300}
        )
        reject = next(
            a for a in _actions(result.structured_content) if a["arguments"]["decision"] == "reject"
        )

        said = await client.call_tool(reject["tool"], reject["arguments"])

        assert said.data.startswith("Cancelled") and api.saves == []
        assert "12-c" not in api.listings

    @pytest.mark.asyncio
    async def test_a_change_the_api_would_refuse_never_reaches_a_card(self, api, client):
        with pytest.raises(ToolError, match="access code"):
            await client.call_tool(
                "rental_listing_update", {"unit": "12-A", "add_public_facts": ["Door code 4821"]}
            )

        assert approvals._PENDING == {} and api.saves == []

    @pytest.mark.asyncio
    async def test_a_change_made_while_the_card_waited_is_not_overwritten(self, api, client):
        result = await client.call_tool(
            "rental_listing_update", {"unit": "12-A", "add_public_facts": ["Cats allowed"]}
        )
        api.listings["12-a"]["asking_rent"] = 1700  # someone else's edit, meanwhile
        approve = next(
            a
            for a in _actions(result.structured_content)
            if a["arguments"]["decision"] == "approve"
        )

        said = await client.call_tool(approve["tool"], approve["arguments"])

        assert said.data.startswith("Not saved.") and "changed this" in said.data
        assert api.listings["12-a"]["asking_rent"] == 1700
        assert "Cats allowed" not in api.listings["12-a"]["public_facts"]

    @pytest.mark.asyncio
    async def test_a_card_is_only_approved_in_the_session_that_asked(
        self, api, client, monkeypatch
    ):
        result = await client.call_tool(
            "rental_listing_update",
            {"unit": "12-A", "add_internal_notes": ["Lease start can move"]},
        )
        approve = next(
            a
            for a in _actions(result.structured_content)
            if a["arguments"]["decision"] == "approve"
        )
        monkeypatch.setattr(tools, "_caller", lambda: ("oat_other", "sam@serniacapital.com"))

        said = await client.call_tool(approve["tool"], approve["arguments"])

        assert "another user" in said.data and api.saves == []

    @pytest.mark.asyncio
    async def test_an_expired_card_saves_nothing(self, api, client):
        result = await client.call_tool(
            "rental_listing_update", {"unit": "12-A", "add_public_facts": ["Cats allowed"]}
        )
        approve = next(
            a
            for a in _actions(result.structured_content)
            if a["arguments"]["decision"] == "approve"
        )
        approvals._PENDING[approve["arguments"]["pending_id"]]["created_at"] = time.time() - 3600

        said = await client.call_tool(approve["tool"], approve["arguments"])

        assert "expired" in said.data and api.saves == []

    @pytest.mark.asyncio
    async def test_showing_rules_window_saves_at_once_and_a_link_waits(self, api, client):
        saved = await client.call_tool("showing_rules_update", {"earliest_start": "10:00"})
        waiting = await client.call_tool(
            "showing_rules_update", {"showing_booking_url": "https://book.example.com"}
        )

        assert api.rules["earliest_start"] == "10:00:00"
        assert "Saved: showing rules" in _texts(saved.structured_content)
        assert api.rules["showing_booking_url"] == ""
        assert len(list(_actions(waiting.structured_content))) == 2

    @pytest.mark.asyncio
    async def test_the_confirm_tool_is_not_listed(self, client):
        names = {tool.name for tool in await client.list_tools()}

        assert {"rental_listing_update", "showing_rules_update"} <= names
        assert "_confirm_rental_change" not in names


class TestRequest:
    """How the API's answers reach the model."""

    @pytest.fixture
    def answer(self, monkeypatch):
        monkeypatch.setattr(core, "SERNIA_MCP_API_BASE_URL", "https://api.example.test")
        replies: list[httpx.Response] = []

        def handler(request: httpx.Request) -> httpx.Response:
            assert request.headers["Authorization"] == "Bearer oat_test"
            return replies.pop(0)

        transport = httpx.MockTransport(handler)
        real = httpx.AsyncClient
        monkeypatch.setattr(core.httpx, "AsyncClient", lambda: real(transport=transport))
        return replies

    @pytest.mark.asyncio
    async def test_a_refusal_carries_the_api_s_reasons(self, answer):
        answer.append(
            httpx.Response(422, json={"detail": {"message": "Not saved.", "problems": ["rent"]}})
        )

        with pytest.raises(core.RefusedError) as caught:
            await core.read_rental_data("oat_test")

        assert caught.value.problems == ["rent"]

    @pytest.mark.asyncio
    async def test_an_unaccepted_user_is_said_plainly(self, answer):
        answer.append(httpx.Response(401, json={"detail": "nope"}))

        with pytest.raises(ExternalServiceError, match="did not accept this user"):
            await core.read_rental_data("oat_test")

    @pytest.mark.asyncio
    async def test_a_unit_name_with_a_slash_is_one_path_segment(self, answer):
        answer.append(httpx.Response(200, json=LISTING))
        plan = core.ListingPlan(
            listing=LISTING, based_on={**LISTING, "unit": "12/14"}, needs_person=False, changes=[]
        )

        await core.save_listing("oat_test", plan)

    @pytest.mark.asyncio
    async def test_no_base_url_turns_the_tools_off(self, monkeypatch):
        monkeypatch.setattr(core, "SERNIA_MCP_API_BASE_URL", "")

        with pytest.raises(ExternalServiceError, match="SERNIA_MCP_API_BASE_URL"):
            await core.read_rental_data("oat_test")

    @pytest.mark.asyncio
    async def test_no_signed_in_user_is_refused(self, monkeypatch):
        monkeypatch.setattr(core, "SERNIA_MCP_API_BASE_URL", "https://api.example.test")

        with pytest.raises(ValidationError, match="signed-in user"):
            await core.read_rental_data("")
