"""Rental listing tools: every change is saved at once, or refused with the reason.

The API is replaced by an in-memory fake with the same paths and answers as
``api/src/rental_listings/routes.py``; nothing leaves the process. All unit
data is invented.
"""

from __future__ import annotations

import copy
import json
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
    "zillow_listing_url_public_view": "https://www.zillow.com/homedetails/x/123_zpid/",
    "zillow_listing_url_internal_editor_view": "",
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


AD = {"title": "12 Example St | Zillow", "page_text": "$1,650/mo\n# 12 Example St"}


class FakeApi:
    """The API's rental listing endpoints, in memory."""

    def __init__(self) -> None:
        self.listings: dict[str, dict] = {"12-a": copy.deepcopy(LISTING)}
        self.rules = copy.deepcopy(RULES)
        self.saves: list[tuple[str, str]] = []  # (method, path) of saves
        self.tokens: set[str] = set()
        self.ad_requests: list[str] = []
        # Called once after the next read, to change what is on file in between.
        self.after_read = None

    def _refuse(self, listing: dict) -> None:
        for line in listing.get("public_facts", []) + listing.get("internal_notes", []):
            if "door code" in line.lower():
                raise core.RefusedError("Not saved.", [f"{line!r} mentions an access code"])

    async def __call__(self, method, path, token, *, json=None, params=None, timeout=None):
        self.tokens.add(token)
        assert not (params and "dry_run" in params)  # a save is a save: no dry run first
        if method == "GET" and path == "":
            read = {
                "listings": [copy.deepcopy(item) for _, item in sorted(self.listings.items())],
                "showing_rules": copy.deepcopy(self.rules),
            }
            if self.after_read:
                self.after_read()
                self.after_read = None
            return read
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
        if method == "GET" and path.startswith("/zillow-ad/"):
            self.ad_requests.append(unquote(path.removeprefix("/zillow-ad/")))
            return copy.deepcopy(AD)
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
    monkeypatch.setattr(tools, "_caller", lambda: "oat_test")
    return fake


READS = ["rental_listings_read", "rental_listing_history", "rental_listing_zillow_ad_read"]
SAVES = ["rental_listing_update", "showing_rules_update"]


@pytest_asyncio.fixture
async def client():
    """A plain client: these tools need no card, so they work in any client."""
    server = FastMCP("test_rental_listings")
    for name in READS + SAVES:
        server.add_tool(getattr(tools, name))
    async with Client(server) as connected:
        yield connected


class TestPlans:
    def data(self) -> dict[str, Any]:
        return {"listings": [copy.deepcopy(LISTING)], "showing_rules": copy.deepcopy(RULES)}

    def test_a_change_is_planned_from_what_is_on_file(self):
        plan = core.plan_listing_change(self.data(), unit="12-a", asking_rent=1550)

        assert plan.listing["asking_rent"] == 1550 and plan.based_on == LISTING
        assert plan.changes == ["asking_rent: 1500 -> 1550"]

    def test_a_new_listing_is_based_on_nothing(self):
        plan = core.plan_listing_change(self.data(), unit="12-C", create=True, asking_rent=1300)

        assert plan.based_on is None and plan.listing["unit"] == "12-C"
        assert plan.changes == ["New listing for unit 12-C", "asking_rent: None -> 1300"]

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

        assert plan.listing["off_market_on"] is None

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
    async def test_the_zillow_ad_comes_back_beside_what_is_on_file(self, api, client):
        result = await client.call_tool("rental_listing_zillow_ad_read", {"unit": "12-a"})

        said = json.loads(result.data)
        assert "$1,650/mo" in said["ad"]["page_text"]
        assert said["on_file"]["asking_rent"] == 1500
        assert api.ad_requests == ["12-A"]  # the name on file
        assert api.saves == []

    @pytest.mark.asyncio
    async def test_the_zillow_ad_of_an_unknown_unit_names_those_on_file(self, api, client):
        with pytest.raises(ToolError, match="on file: 12-A"):
            await client.call_tool("rental_listing_zillow_ad_read", {"unit": "99"})
        assert api.ad_requests == []

    @pytest.mark.asyncio
    async def test_a_change_is_saved_at_once_and_the_answer_says_what_was_saved(self, api, client):
        result = await client.call_tool(
            "rental_listing_update", {"unit": "12-A", "off_market_on": "2027-06-01"}
        )

        assert api.saves == [("PUT", "/listings/12-A")]
        assert api.listings["12-a"]["off_market_on"] == "2027-06-01"
        said = result.data
        assert said.startswith("Saved the listing for 12-A.")
        assert "- off_market_on: None -> '2027-06-01'" in said
        on_file = json.loads(said.split("Now on file:\n", 1)[1])
        assert on_file == api.listings["12-a"]

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("change", "line"),
        [
            ({"add_public_facts": ["Cats allowed"]}, "- Add public fact: Cats allowed"),
            ({"remove_public_facts": ["Dishwasher"]}, "- Remove public fact: Dishwasher"),
            (
                {"add_internal_notes": ["Lease start can move"]},
                "- Add internal note: Lease start can move",
            ),
            ({"address": "12 Example Street"}, "- address: '12 Example St #A' -> "),
            ({"showing_booking_url": "https://book.example.com"}, "- showing_booking_url: "),
            ({"zillow_listing_url_public_view": ""}, "- zillow_listing_url_public_view: "),
        ],
    )
    async def test_free_text_and_links_are_saved_at_once_too(self, api, client, change, line):
        result = await client.call_tool("rental_listing_update", {"unit": "12-A", **change})

        assert api.saves == [("PUT", "/listings/12-A")]
        assert line in result.data

    @pytest.mark.asyncio
    async def test_a_new_listing_is_saved_at_once(self, api, client):
        result = await client.call_tool(
            "rental_listing_update", {"unit": "12-C", "create": True, "asking_rent": 1300}
        )

        assert api.saves == [("POST", "/listings")]
        assert api.listings["12-c"]["asking_rent"] == 1300
        assert "- New listing for unit 12-C" in result.data

    @pytest.mark.asyncio
    async def test_a_change_the_api_refuses_says_why_and_saves_nothing(self, api, client):
        with pytest.raises(ToolError, match="access code"):
            await client.call_tool(
                "rental_listing_update", {"unit": "12-A", "add_public_facts": ["Door code 4821"]}
            )

        assert api.listings["12-a"] == LISTING

    @pytest.mark.asyncio
    async def test_a_change_made_since_the_read_is_not_overwritten(self, api, client):
        def someone_else_edits():
            api.listings["12-a"]["asking_rent"] = 1700

        api.after_read = someone_else_edits

        with pytest.raises(ToolError, match=r"Not saved.*changed this"):
            await client.call_tool(
                "rental_listing_update", {"unit": "12-A", "add_public_facts": ["Cats allowed"]}
            )

        assert api.listings["12-a"]["asking_rent"] == 1700
        assert "Cats allowed" not in api.listings["12-a"]["public_facts"]

    @pytest.mark.asyncio
    async def test_showing_rules_are_saved_at_once_a_link_included(self, api, client):
        result = await client.call_tool(
            "showing_rules_update",
            {"earliest_start": "10:00", "showing_booking_url": "https://book.example.com"},
        )

        assert api.saves == [("PUT", "/showing-rules")]
        assert api.rules["earliest_start"] == "10:00:00"
        assert api.rules["showing_booking_url"] == "https://book.example.com"
        assert result.data.startswith("Saved the showing rules.")
        assert "- earliest_start: '09:00:00' -> '10:00:00'" in result.data

    @pytest.mark.asyncio
    async def test_a_client_is_told_which_tools_read_and_which_save(self, client):
        """Asking first is the client's setting, so it must be able to tell them apart."""
        listed = {tool.name: tool for tool in await client.list_tools()}

        assert set(listed) == set(READS + SAVES)
        for name in READS:
            assert listed[name].annotations.read_only_hint is True
        for name in SAVES:
            assert listed[name].annotations.read_only_hint is False
            assert listed[name].annotations.destructive_hint is False
            # No card, so nothing for a card to call.
            assert "ui" not in (listed[name].meta or {})


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
    async def test_a_zillow_ad_the_api_could_not_read_says_why(self, answer):
        answer.append(
            httpx.Response(
                424,
                json={
                    "detail": {
                        "message": "Could not read the Zillow ad.",
                        "problems": ["Zillow refused the request with its bot check."],
                    }
                },
            )
        )

        with pytest.raises(
            ExternalServiceError, match=r"Could not read the Zillow ad\. Zillow refused"
        ):
            await core.read_zillow_ad("oat_test", "12/14")

    @pytest.mark.asyncio
    async def test_a_zillow_ad_read_waits_longer_than_the_api_s_page_reader(self, monkeypatch):
        seen: dict[str, Any] = {}

        async def request(method, path, token, *, json=None, params=None, timeout=core._TIMEOUT):
            seen.update(path=path, timeout=timeout)
            return {}

        monkeypatch.setattr(core, "_request", request)
        await core.read_zillow_ad("oat_test", "12/14")

        assert seen == {"path": "/zillow-ad/12%2F14", "timeout": 75}
        assert core._ZILLOW_AD_TIMEOUT > 60  # zillow_ad.py's reader timeout

    @pytest.mark.asyncio
    async def test_an_unaccepted_user_is_said_plainly(self, answer):
        answer.append(httpx.Response(401, json={"detail": "nope"}))

        with pytest.raises(ExternalServiceError, match="did not accept this user"):
            await core.read_rental_data("oat_test")

    @pytest.mark.asyncio
    async def test_a_unit_name_with_a_slash_is_one_path_segment(self, answer):
        answer.append(httpx.Response(200, json=LISTING))
        plan = core.ListingPlan(listing=LISTING, based_on={**LISTING, "unit": "12/14"}, changes=[])

        await core.save_listing("oat_test", plan)

    @pytest.mark.asyncio
    async def test_an_off_market_date_that_cancels_showings_is_sent_to_the_screen(self, answer):
        """Saving it texts the people booked, so only a person on the screen confirms it."""
        answer.append(
            httpx.Response(
                409,
                json={
                    "detail": {
                        "code": "cancels_showings",
                        "message": "Not saved. This cancels 1 showing and texts 1 person. "
                        "Confirm to save.",
                        "problems": [],
                        "showings": 1,
                        "people": 1,
                    }
                },
            )
        )
        plan = core.ListingPlan(
            listing={**LISTING, "off_market_on": "2027-06-01"}, based_on=LISTING, changes=[]
        )

        with pytest.raises(core.RefusedError) as caught:
            await core.save_listing("oat_test", plan)

        assert "cancels 1 showing and texts 1 person" in str(caught.value)
        assert "Rental Listings screen" in str(caught.value)

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
