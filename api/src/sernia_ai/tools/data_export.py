"""Shared CSV writer for data-fetching tools.

Tools write structured data to disk so the DuckDB toolset can load and query it.
"""

import csv
import re
from pathlib import Path

DATA_BASE = Path("/tmp/sernia_data")


def _sanitize_name(name: str) -> str:
    """Enforce [a-z0-9_], truncate to 64 chars."""
    name = name.lower().strip()
    name = re.sub(r"[^a-z0-9_]", "_", name)
    name = re.sub(r"_+", "_", name).strip("_")
    return name[:64] or "dataset"


def _validate_conversation_id(cid: str) -> None:
    """Reject path traversal attempts in conversation IDs."""
    if not cid:
        raise ValueError("conversation_id is empty")
    for bad in ("..", "/", "\\", "\x00"):
        if bad in cid:
            raise ValueError(f"Invalid conversation_id: contains {bad!r}")


def write_dataset(
    conversation_id: str,
    name: str,
    headers: list[str],
    rows: list[list[str]],
) -> tuple[Path, int]:
    """Write a CSV to /tmp/sernia_data/<conversation_id>/<name>.csv.

    Pads short rows to match header length and skips empty rows so DuckDB
    can parse the file without column-count mismatches.

    Returns (written_path, rows_written) — rows_written excludes blanks.
    """
    _validate_conversation_id(conversation_id)
    safe_name = _sanitize_name(name)
    directory = DATA_BASE / conversation_id
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{safe_name}.csv"

    ncols = len(headers)
    rows_written = 0
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(headers)
        for row in rows:
            # Skip empty / whitespace-only rows
            if not row or all(not cell or cell.isspace() for cell in row):
                continue
            # Pad short rows to match header count
            padded = row + [""] * (ncols - len(row)) if len(row) < ncols else row
            writer.writerow(padded)
            rows_written += 1

    return path, rows_written
