"""Scenarios cut from past threads, and the train / dev / test split."""

import random
from pathlib import Path

from pydantic import BaseModel

from api.src.sernia_ai.zillow_leads.case import LeadCase, OperatingData, build_case, decision_points
from api.src.sernia_ai.zillow_leads.thread import LeadThread


class Scenario(BaseModel):
    """A moment in a real thread where a decision was owed, and what happened next.

    It holds only the conversation. Listings, schedules and booking links
    come from operating data when the scenario is run.

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


def case_for(scenario: Scenario, operating: OperatingData) -> LeadCase:
    """The scenario as a case: the conversation plus what was true at that moment."""
    return build_case(scenario.thread, operating, upto=len(scenario.thread.messages) - 1)


def read_scenarios(path: Path) -> list[Scenario]:
    return [Scenario.model_validate_json(line) for line in path.read_text().splitlines() if line]
