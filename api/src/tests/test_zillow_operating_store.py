"""The lead agent's operating data file: what is accepted, and what a bad file does.

No network and no git: the workspace pull is replaced in every test that loads.
All unit data here is invented.
"""

import asyncio
import json
from datetime import date, time
from pathlib import Path

import pytest

from api.src.sernia_ai.config import ZILLOW_OPERATING_FILE
from api.src.sernia_ai.zillow_leads import operating_store
from api.src.sernia_ai.zillow_leads.case import (
    AgentAvailability,
    Listing,
    OperatingData,
    PreferredWindow,
    ShowingWindow,
)
from api.src.sernia_ai.zillow_leads.operating_store import OperatingDataInvalid

GOOD = OperatingData(
    listings=[
        Listing(
            unit="12-A",
            address="12 Example St #A",
            rent=1500,
            lease_start_earliest=date(2027, 7, 1),
            lease_start_latest=date(2027, 8, 1),
            booking_url="https://book.example.com/12-a",
            facts="1 bedroom, second floor\nDishwasher\nBuilt 1925, renovated 2019",
            preferred_times=[
                PreferredWindow(days=["mon", "tue"], start=time(15, 0), end=time(18, 0))
            ],
        ),
        Listing(unit="12-B", rented_on=date(2027, 6, 1)),
    ],
    showing_window=ShowingWindow(earliest_start=time(10, 0), latest_start=time(17, 0)),
    blackout_dates=[date(2027, 7, 4)],
)


def with_listing(**fields) -> OperatingData:
    return OperatingData(listings=[Listing(**{"unit": "12-A", **fields})])


def put(workspace: Path, text: str) -> None:
    path = workspace / ZILLOW_OPERATING_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


class TestChecks:
    def test_ordinary_listing_data_passes(self):
        assert operating_store.problems_in(GOOD) == []
        assert operating_store.problems_in(OperatingData()) == []

    @pytest.mark.parametrize(
        ("fact", "reason"),
        [
            ("Call Sam at 412-555-0134 to see it", "phone number"),
            ("Call (412) 555 0134", "phone number"),
            ("Questions: sam@example.com", "email address"),
            ("Floor plan at https://example.com/plan.pdf", "link"),
            ("More at www.example.com", "link"),
            ("Lockbox on the back door", "access code"),
            ("Door code is 4821", "access code"),
            ("Wifi password on the fridge", "access code"),
        ],
    )
    def test_a_fact_that_is_not_public_is_refused(self, fact, reason):
        (problem,) = operating_store.problems_in(with_listing(facts=f"Dishwasher\n{fact}"))
        assert reason in problem and "12-A" in problem

    def test_private_text_is_refused_in_names_and_addresses_too(self):
        problems = operating_store.problems_in(
            OperatingData(
                listings=[Listing(unit="12-A", address="12 Example St, call 412-555-0134")],
                rented_units=["12-C (sam@example.com)"],
            )
        )
        assert any("address" in problem and "phone number" in problem for problem in problems)
        assert any("rented_units" in problem and "email" in problem for problem in problems)

    @pytest.mark.parametrize(
        "url",
        [
            "http://book.example.com/12-a",  # not https
            "book.example.com/12-a",
            "https://user:secret@book.example.com/",
            "https://book.example.com/a b",
            "javascript:alert(1)",
        ],
    )
    def test_a_booking_link_must_be_a_plain_https_address(self, url):
        assert operating_store.problems_in(with_listing(booking_url=url))
        assert operating_store.problems_in(OperatingData(booking_url=url))

    @pytest.mark.parametrize(
        "operating",
        [
            with_listing(rent=0),
            with_listing(
                lease_start_earliest=date(2027, 8, 1), lease_start_latest=date(2027, 7, 1)
            ),
            with_listing(unit=" "),
            with_listing(facts="x" * (operating_store.MAX_FACT_LENGTH + 1)),
            with_listing(facts="\n".join(["A fact"] * (operating_store.MAX_FACTS_PER_LISTING + 1))),
            with_listing(preferred_times=[PreferredWindow(days=[], start=time(9), end=time(10))]),
            with_listing(
                preferred_times=[PreferredWindow(days=["mon"], start=time(12), end=time(10))]
            ),
            OperatingData(listings=[Listing(unit="12-A"), Listing(unit="12-a")]),
            OperatingData(timezone="Mars/Olympus"),
            OperatingData(
                showing_window=ShowingWindow(earliest_start=time(18), latest_start=time(9))
            ),
        ],
    )
    def test_values_that_make_no_sense_are_refused(self, operating):
        assert operating_store.problems_in(operating)


class TestParse:
    def test_a_file_reads_back_as_what_was_written(self):
        assert operating_store.parse(operating_store.to_file_text(GOOD)) == GOOD

    def test_fact_sheets_are_written_as_lists_of_lines(self):
        document = json.loads(operating_store.to_file_text(GOOD))
        assert document["listings"][0]["facts"] == [
            "1 bedroom, second floor",
            "Dishwasher",
            "Built 1925, renovated 2019",
        ]
        assert "availability" not in document  # hand-kept tour times are not part of the file

    def test_a_fact_sheet_typed_as_one_string_is_read_too(self):
        text = json.dumps({"listings": [{"unit": "12-A", "facts": "Dishwasher\nCats allowed"}]})
        assert operating_store.parse(text).listings[0].facts == "Dishwasher\nCats allowed"

    def test_a_misspelt_key_is_an_error_not_a_dropped_value(self):
        """Dropped silently, ``rentd_on`` would leave a rented unit on offer."""
        text = json.dumps({"listings": [{"unit": "12-A", "rentd_on": "2027-06-01"}]})
        with pytest.raises(OperatingDataInvalid, match="listings.0.rentd_on"):
            operating_store.parse(text)

    @pytest.mark.parametrize(
        "text",
        [
            "",
            "{not json",
            "[]",
            json.dumps({"listings": [{"rent": 1500}]}),  # no unit
            json.dumps({"listings": [{"unit": "12-A", "rent": "a lot"}]}),
            json.dumps({"blackout_dates": ["July 4th"]}),
            "<<<<<<< HEAD\n{}\n=======\n{}\n>>>>>>> main\n",  # a merge conflict left in the file
        ],
    )
    def test_a_file_of_the_wrong_shape_is_refused(self, text):
        with pytest.raises(OperatingDataInvalid):
            operating_store.parse(text)

    def test_a_file_with_a_private_fact_is_refused(self):
        text = json.dumps({"listings": [{"unit": "12-A", "facts": ["Door code is 4821"]}]})
        with pytest.raises(OperatingDataInvalid, match="access code"):
            operating_store.parse(text)


class TestReadAndWrite:
    def test_a_missing_file_is_empty_data_and_not_a_problem(self, tmp_path):
        loaded = operating_store.read(tmp_path)
        assert loaded == operating_store.Loaded()
        assert loaded.data == OperatingData() and not loaded.found and loaded.problem is None

    def test_what_is_written_is_read_back(self, tmp_path):
        operating_store.write(tmp_path, GOOD)

        loaded = operating_store.read(tmp_path)

        assert loaded.data == GOOD and loaded.found and loaded.problem is None
        assert [path.name for path in (tmp_path / ZILLOW_OPERATING_FILE).parent.iterdir()] == [
            Path(ZILLOW_OPERATING_FILE).name
        ]

    def test_a_bad_file_gives_no_data_and_says_why(self, tmp_path):
        put(tmp_path, '{"listings": [{"unit": "12-A", "rent": 1500,}]}')  # trailing comma

        loaded = operating_store.read(tmp_path)

        assert loaded.data == OperatingData() and loaded.found
        assert loaded.problem

    def test_a_file_that_is_not_text_gives_no_data(self, tmp_path):
        path = tmp_path / ZILLOW_OPERATING_FILE
        path.parent.mkdir(parents=True)
        path.write_bytes(b"\xff\xfe\x00")

        loaded = operating_store.read(tmp_path)

        assert loaded.data == OperatingData() and loaded.problem

    def test_a_write_that_fails_its_checks_changes_nothing(self, tmp_path):
        operating_store.write(tmp_path, GOOD)
        before = (tmp_path / ZILLOW_OPERATING_FILE).read_text()

        with pytest.raises(OperatingDataInvalid, match="phone number"):
            operating_store.write(tmp_path, with_listing(facts="Call 412-555-0134"))

        assert (tmp_path / ZILLOW_OPERATING_FILE).read_text() == before

    def test_hand_kept_tour_times_survive_a_write(self, tmp_path):
        """Production does not read them, but a tool's write must not delete a person's text."""
        operating = GOOD.model_copy(
            update={"availability": [AgentAvailability(agent="Alex", weekday_times=[time(10)])]}
        )
        operating_store.write(tmp_path, operating)
        assert operating_store.read(tmp_path).data.availability == operating.availability


class TestLoad:
    @pytest.mark.asyncio
    async def test_the_workspace_is_pulled_before_the_file_is_read(self, tmp_path, monkeypatch):
        async def pull(workspace: Path) -> None:
            operating_store.write(workspace, GOOD)  # an edit that arrives with the pull

        monkeypatch.setattr(operating_store, "pull_workspace", pull)

        loaded = await operating_store.load(tmp_path)

        assert loaded.data == GOOD

    @pytest.mark.asyncio
    async def test_a_failed_pull_falls_back_to_the_copy_on_disk(self, tmp_path, monkeypatch):
        async def pull(_workspace: Path) -> None:
            raise RuntimeError("no network")

        monkeypatch.setattr(operating_store, "pull_workspace", pull)
        operating_store.write(tmp_path, GOOD)

        assert (await operating_store.load(tmp_path)).data == GOOD

    @pytest.mark.asyncio
    async def test_a_slow_pull_is_not_waited_for_and_not_cut_short(self, tmp_path, monkeypatch):
        finished = asyncio.Event()
        release = asyncio.Event()

        async def pull(_workspace: Path) -> None:
            await release.wait()
            finished.set()

        monkeypatch.setattr(operating_store, "pull_workspace", pull)
        monkeypatch.setattr(operating_store, "PULL_TIMEOUT_SECONDS", 0.01)
        operating_store.write(tmp_path, GOOD)

        loaded = await operating_store.load(tmp_path)

        assert loaded.data == GOOD
        assert not finished.is_set()
        release.set()  # the pull was left running, so it can still finish
        await asyncio.wait_for(finished.wait(), 1)

    @pytest.mark.asyncio
    async def test_a_bad_file_is_reported_and_never_raises(self, tmp_path, monkeypatch):
        async def pull(_workspace: Path) -> None:
            return None

        monkeypatch.setattr(operating_store, "pull_workspace", pull)
        put(tmp_path, "{not json")

        loaded = await operating_store.load(tmp_path)

        assert loaded.data == OperatingData() and loaded.problem
