"""Scenarios cut from past threads, and the train / dev / test split."""

import hashlib
import json
import random
from datetime import UTC, datetime
from pathlib import Path

from pydantic import AwareDatetime, BaseModel, Field

from api.src.zillow_leads.case import (
    BookedTour,
    LeadCase,
    ListingData,
    TourSlot,
    build_case,
    decision_points,
)
from api.src.zillow_leads.thread import LeadThread


class Scenario(BaseModel):
    """A moment in a real thread where a decision was owed, and what happened next.

    It holds only the conversation. Listings, schedules and booking links
    come from listing data when the scenario is run.

    ``actual_reply`` is context for the person reviewing. It is never shown to
    the model and is not treated as the right answer.
    """

    thread: LeadThread  # cut at the decision point: the last message is the lead's
    actual_reply: str | None = None
    actual_reply_delay_minutes: int | None = None

    @property
    def scenario_id(self) -> str:
        return f"{self.thread.thread_id}-{len(self.thread.messages) - 1}"


# Each thread belongs to one split, for good. Whole threads, because a later
# scenario in a thread contains the earlier ones.
#   train: worked examples that may be pasted into a prompt as few-shot
#          examples: a case paired with the reply a person would send (for
#          the policy), or a reply paired with its verdict (for an LLM judge).
#          Nothing is trained; the name is inherited from machine learning
#   dev:   run, read, label and change things against these, as often as needed
#   test:  not looked at until a version is thought finished, then run once
SPLIT_SHARES = {"train": 0.15, "test": 0.40}  # dev is the remainder
SPLITS = ("train", "dev", "test")


def assign_splits(thread_ids: list[str], *, seen: set[str], seed: int = 0) -> dict[str, str]:
    """Assign every thread to train, dev or test.

    ``seen`` are threads whose scenarios have already been run and looked at.
    They can only be dev: a test set is worth something only if nothing about
    it has influenced a change.
    """
    threads = sorted(set(thread_ids))
    unseen = [thread for thread in threads if thread not in seen]
    random.Random(seed).shuffle(unseen)
    train = round(len(threads) * SPLIT_SHARES["train"])
    test = round(len(threads) * SPLIT_SHARES["test"])
    splits = {thread: "dev" for thread in threads if thread in seen}
    for position, thread in enumerate(unseen):
        splits[thread] = (
            "train" if position < train else "test" if position < train + test else "dev"
        )
    return splits


def in_split(scenarios: list[Scenario], splits: dict[str, str], split: str) -> list[Scenario]:
    return [item for item in scenarios if splits.get(item.thread.thread_id) == split]


def scenarios_from_thread(thread: LeadThread) -> list[Scenario]:
    """One scenario per decision point in a past thread."""
    scenarios: list[Scenario] = []
    for index in decision_points(thread):
        lead_message = thread.messages[index]
        reply = thread.messages[index + 1] if index + 1 < len(thread.messages) else None
        scenarios.append(
            Scenario(
                thread=thread.model_copy(update={"messages": thread.messages[: index + 1]}),
                actual_reply=reply.body if reply else None,
                actual_reply_delay_minutes=(
                    int((reply.sent_at - lead_message.sent_at).total_seconds() // 60)
                    if reply
                    else None
                ),
            )
        )
    return scenarios


class CaseEdit(BaseModel):
    """Tour facts a person added to one scenario's case, from reading its thread.

    A replay takes tour times from one weekly pattern, so it cannot know a time
    the team offered that week or a tour that was booked. Where the thread shows
    one plainly, an edit puts it in the case, as live listing data would have
    held it. Edits are made by hand, never by the model being evaluated.
    """

    offered: list[AwareDatetime] = Field(default_factory=list)
    booked: list[BookedTour] = Field(default_factory=list)
    note: str = ""  # what in the thread shows it


def read_case_edits(path: Path) -> dict[str, CaseEdit]:
    """Edits by scenario id, from a JSON file."""
    return {
        scenario_id: CaseEdit.model_validate(edit)
        for scenario_id, edit in json.loads(path.read_text()).items()
    }


def edit_fingerprint(edit: CaseEdit | None) -> str:
    """Identifies the tour facts an edit adds to a case; empty when there is no edit.

    A run stores it with each row, so a later change to the edits file can be
    told apart from the edit the row was run with. The note is left out: it
    explains the edit and does not reach the model.
    """
    if edit is None:
        return ""
    facts = {
        "offered": sorted(start.astimezone(UTC).isoformat() for start in edit.offered),
        "booked": sorted(
            [tour.unit, tour.start.astimezone(UTC).isoformat()] for tour in edit.booked
        ),
    }
    return hashlib.sha256(json.dumps(facts).encode()).hexdigest()[:12]


def edit_run_with(row: dict, edits: dict[str, CaseEdit]) -> CaseEdit | None:
    """The hand edit a stored row's case was built with, for rebuilding that case.

    Raises when the edits given are not the ones the row was run with: the
    rebuilt case would then show tour facts the model never received. A row
    from before fingerprints were stored is trusted if its edit still exists.
    """
    if not row.get("case_edit"):
        return None
    edit = edits.get(row["case_id"])
    recorded = row.get("case_edit_hash")
    if edit is None or (recorded is not None and recorded != edit_fingerprint(edit)):
        raise ValueError(
            f"{row['case_id']} was run with a hand edit that is "
            f"{'missing from' if edit is None else 'different in'} the edits given, "
            "so its case cannot be rebuilt. Pass the edits it was run with, or run it again."
        )
    return edit


def case_for(
    scenario: Scenario, listing_data: ListingData, edit: CaseEdit | None = None
) -> LeadCase:
    """The scenario as a case: the conversation plus what was true at that moment."""
    upto = len(scenario.thread.messages) - 1
    if edit is None:
        return build_case(scenario.thread, listing_data, upto=upto)
    case = build_case(scenario.thread, listing_data, upto=upto, booked_tours=edit.booked)
    known = {slot.start for slot in case.offerable_slots}
    added = [TourSlot(start=start, agent="") for start in edit.offered if start not in known]
    slots = sorted(case.offerable_slots + added, key=_slot_order)
    return case.model_copy(update={"offerable_slots": slots})


def _slot_order(slot: TourSlot) -> tuple[datetime, str]:
    return (slot.start, slot.agent)


def read_scenarios(path: Path) -> list[Scenario]:
    return [Scenario.model_validate_json(line) for line in path.read_text().splitlines() if line]
