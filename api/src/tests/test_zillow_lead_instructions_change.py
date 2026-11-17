"""The lead agent's instructions differ from the last reviewed version only by a known diff.

Everything in ``zillow_leads/prompts/`` is read by a production model on every
lead, so a change to it is a change in behaviour. ``zillow_instructions_change.diff``
is the latest change to the joined instructions (``role.md``, ``policy.md``,
``record.md``, as ``agent.load_instructions`` joins them, with the team phone
left as its placeholder), and ``BEFORE_SHA256`` identifies the text it was
made against. The test undoes the diff on the current files and checks that
what is left is exactly that earlier text: nothing the model reads changed
except the lines in the diff, which is what a reviewer reads and what the
pull request lists.

When the instructions change again on purpose, write the new diff against the
commit being changed (``main`` before the change), and review it:

    uv run python -m api.src.tests.test_zillow_lead_instructions_change --write origin/main
"""

import hashlib
import re
import subprocess
import sys
from difflib import unified_diff
from pathlib import Path

from api.src.zillow_leads import agent

DIFF = Path(__file__).with_name("zillow_instructions_change.diff")
# The joined instructions on main at a15bbca ("Lead agent: showings agreed by
# email are booked (#409)"), before the booking policy.
BEFORE_SHA256 = "3776c88e1cc65e17e369cae2294868346fc508696d70b9385ed7eb3781e6f56f"
PATHS = [agent.ROLE_PATH, agent.POLICY_PATH, agent.RECORD_PATH]
HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


def joined(texts: list[str]) -> str:
    """The instructions as ``load_instructions`` joins them, phone placeholder kept."""
    return "\n".join(texts)


def current() -> str:
    return joined([path.read_text() for path in PATHS])


def undo(diff: str, after: list[str]) -> list[str]:
    """``after`` with a unified diff taken back out. Fails if the diff does not fit."""
    before: list[str] = []
    position = 0
    lines = diff.splitlines()
    index = 0
    while index < len(lines):
        match = HUNK.match(lines[index])
        index += 1
        if not match:
            continue
        new_start, new_count = int(match[3]), int(match[4] or 1)
        start = new_start - 1 if new_count else new_start
        assert start >= position, "hunks out of order"
        before += after[position:start]
        position = start
        while index < len(lines) and not lines[index].startswith("@@"):
            tag, text = lines[index][:1], lines[index][1:]
            index += 1
            if tag in (" ", "+"):
                assert after[position] == text, (position + 1, text)
                position += 1
            if tag in (" ", "-"):
                before.append(text)
    return before + after[position:]


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def test_the_instructions_differ_from_the_reviewed_version_only_by_the_diff():
    after = current().splitlines()
    before = undo(DIFF.read_text(), after)

    assert _sha("\n".join(before)) == BEFORE_SHA256


def test_the_diff_is_what_turns_the_reviewed_version_into_this_one():
    after = current().splitlines()
    before = undo(DIFF.read_text(), after)
    redone = list(unified_diff(before, after, "before", "after", lineterm=""))

    assert "\n".join(redone) + "\n" == DIFF.read_text()


def _write(ref: str) -> None:
    root = Path(
        subprocess.run(
            ["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True, check=True
        ).stdout.strip()
    )
    texts = [
        subprocess.run(
            ["git", "show", f"{ref}:{path.relative_to(root)}"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        for path in PATHS
    ]
    before = joined(texts).splitlines()
    diff = unified_diff(before, current().splitlines(), "before", "after", lineterm="")
    DIFF.write_text("\n".join(diff) + "\n")
    print(f"wrote {DIFF}")
    print(f"BEFORE_SHA256 = {_sha(chr(10).join(before))!r}")


if __name__ == "__main__" and "--write" in sys.argv:
    _write(sys.argv[sys.argv.index("--write") + 1])
