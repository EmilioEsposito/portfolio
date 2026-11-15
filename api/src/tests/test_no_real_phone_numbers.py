"""Tracked files must not contain real phone numbers.

The repository is public. Runtime code reads Sernia's numbers from settings
(see ``api/src/utils/phone_config.py``) and tests use fictional numbers, so a
number in a tracked file that is not plainly fictional is a mistake. These are
allowed:

- any number on the 555 exchange (``412-555-0123``); use 555-0100 to 555-0199,
  the range set aside for fiction, in new tests;
- the 555 area code, which is not assigned;
- one digit repeated (``+19999999999``).

The scan looks for the usual spellings: E.164 (``+14125550123``), formatted
numbers (``(412) 555-0123``, ``412-555-0123``, ``412.555.0123``), URL-encoded
``%2B1...``, ``tel:`` links, and Quo contact external IDs (``e`` and the
digits). A failure names the file and line and shows only the area code,
because CI logs for a public repository are public too.
"""

import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
THIS_FILE = Path(__file__).resolve()
SKIPPED_FILES = {"uv.lock", "pnpm-lock.yaml", "package-lock.json"}

_NUMBER = r"\(?[2-9]\d{2}\)?[\s.-]?[2-9]\d{2}[\s.-]?\d{4}"
PHONE = re.compile(
    "|".join(
        [
            rf"\+1[\s.-]?{_NUMBER}",  # E.164, or +1 with separators
            r"(?<![\d+])\(?[2-9]\d{2}\)?[\s.-][2-9]\d{2}[\s.-]\d{4}",  # formatted
            r"%2B1[2-9]\d{2}[2-9]\d{6}",  # URL-encoded E.164
            r"tel:1?[2-9]\d{2}[2-9]\d{6}",  # tel: link without the +
            r"(?<![\w])e1?[2-9]\d{2}[2-9]\d{6}",  # Quo external ID
        ]
    )
    + r"(?!\d)"
)


def is_fictional(digits: str) -> bool:
    """True for a ten-digit number that cannot reach a real line."""
    area, exchange = digits[:3], digits[3:6]
    return exchange == "555" or area == "555" or len(set(digits)) == 1


def real_numbers(text: str) -> list[tuple[int, str]]:
    """``(line number, masked number)`` for every number that is not fictional."""
    found = []
    for line_number, line in enumerate(text.splitlines(), 1):
        for match in PHONE.finditer(line):
            digits = re.sub(r"\D", "", match.group().removeprefix("%2B"))[-10:]
            if not is_fictional(digits):
                found.append((line_number, f"{digits[:3]}-xxx-xxxx"))
    return found


def tracked_files() -> list[Path]:
    try:
        listing = subprocess.run(
            ["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("Not a git checkout")
    names = [name for name in listing.decode().split("\0") if name]
    return [ROOT / name for name in names if Path(name).name not in SKIPPED_FILES]


def test_tracked_files_have_no_real_phone_numbers():
    problems = []
    for path in tracked_files():
        if path.resolve() == THIS_FILE:
            continue  # the examples below are unassignable, but they look real
        try:
            data = path.read_bytes()
        except (FileNotFoundError, IsADirectoryError):
            continue  # deleted in the working tree, or a submodule
        if b"\0" in data[:8000]:
            continue  # binary
        for line_number, masked in real_numbers(data.decode("utf-8", errors="replace")):
            problems.append(f"{path.relative_to(ROOT)}:{line_number}: {masked}")
    assert not problems, (
        "Real-looking phone numbers in tracked files. Read real numbers from settings "
        "(api/src/utils/phone_config.py) and use 555-01xx numbers in tests:\n" + "\n".join(problems)
    )


# 211 is a service code and is never assigned as an area code, so these
# examples look real to the scan but cannot reach anyone.
@pytest.mark.parametrize(
    "text",
    [
        "+12119876543",
        "+1 (211) 987-6543",
        "(211) 987-6543",
        "211-987-6543",
        "211.987.6543",
        "participants[]=%2B12119876543",
        '<a href="tel:2119876543">',
        "externalIds: ['e2119876543']",
    ],
)
def test_scan_finds_each_spelling(text):
    assert real_numbers(text) == [(1, "211-xxx-xxxx")]


@pytest.mark.parametrize(
    "text",
    [
        "+14125550123",
        "(412) 555-0123",
        "+15555550101",
        "+19999999999",
        "2026-11-14 10:30",  # a timestamp
        "+14120000001",  # no real exchange starts with 0
        "sha256:2119876543210",  # a longer run of digits
    ],
)
def test_scan_ignores_fictional_numbers_and_other_digits(text):
    assert real_numbers(text) == []
