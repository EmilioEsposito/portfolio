"""DuckDB toolset for per-conversation SQL analytics.

Provides 4 tools: list_datasets, load_dataset, describe_table, run_sql.
Each conversation gets a file-backed DuckDB at /tmp/sernia_duckdb/<conversation_id>.duckdb.
"""

import csv
import json
import subprocess
import sys
import threading
import time
from pathlib import Path

import duckdb
from pydantic_ai import FunctionToolset, RunContext

from api.src.sernia_ai.deps import SerniaDeps
from api.src.sernia_ai.tools.data_export import DATA_BASE, _validate_conversation_id
from api.src.sernia_ai.tools.duckdb_worker import MAX_CHARS, MAX_CSV_BYTES, open_connection

duckdb_toolset = FunctionToolset()

DUCKDB_BASE = Path("/tmp/sernia_duckdb")


QUERY_TIMEOUT_SECONDS = 30
MAX_QUERY_CHARS = 64_000
_WORKER_SLOTS = threading.BoundedSemaphore(2)


def _database_path(conversation_id: str) -> Path:
    _validate_conversation_id(conversation_id)
    DUCKDB_BASE.mkdir(parents=True, exist_ok=True)
    return DUCKDB_BASE / f"{conversation_id}.duckdb"


def _get_connection(conversation_id: str) -> duckdb.DuckDBPyConnection:
    """Restricted connection for internal callers; model tools use bounded workers."""
    return open_connection(_database_path(conversation_id))


def _run_worker(conversation_id: str, operation: str, **arguments: str | None) -> str:
    if not _WORKER_SLOTS.acquire(blocking=False):
        return "SQL error: Analytics is busy. Try again shortly."
    try:
        request = {
            "db_path": str(_database_path(conversation_id)),
            "operation": operation,
            **arguments,
        }
        try:
            completed = subprocess.run(
                [sys.executable, "-I", str(Path(__file__).with_name("duckdb_worker.py"))],
                input=json.dumps(request),
                text=True,
                capture_output=True,
                timeout=QUERY_TIMEOUT_SECONDS,
                # No API/provider credentials, dotenv, Python path overrides or
                # inherited filesystem handles reach the analytics interpreter.
                env={"LANG": "C.UTF-8", "OPENBLAS_NUM_THREADS": "1"},
                cwd=DUCKDB_BASE,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return "SQL error: Analytics exceeded its time limit. Simplify the query or use a smaller dataset."
        if completed.returncode:
            return "SQL error: Analytics worker exceeded a resource limit or failed. Simplify the query or use a smaller dataset."
        return json.loads(completed.stdout)
    finally:
        _WORKER_SLOTS.release()


@duckdb_toolset.tool
def list_datasets(ctx: RunContext[SerniaDeps]) -> str:
    """List CSV datasets available for this conversation. Shows filename, row count, and columns.

    Use load_dataset to import a dataset into DuckDB for SQL querying.
    """
    _validate_conversation_id(ctx.deps.conversation_id)
    data_dir = DATA_BASE / ctx.deps.conversation_id
    if not data_dir.exists():
        return "No datasets available. Data-fetching tools (like read_google_sheet) will create datasets automatically."

    csv_files = sorted(data_dir.glob("*.csv"))
    if not csv_files:
        return "No datasets available."

    lines: list[str] = []
    for f in csv_files[:100]:
        try:
            if not f.resolve().is_relative_to(data_dir.resolve()):
                continue
            if f.stat().st_size > MAX_CSV_BYTES:
                lines.append(f"- {f.stem} (exceeds 10 MiB dataset limit)")
                continue
            with open(f, encoding="utf-8") as fh:
                reader = csv.reader(fh)
                headers = next(reader, [])
                row_count = sum(1 for _ in reader)
            lines.append(
                f"- {f.stem} ({row_count} rows, {len(headers)} cols)\n"
                f"  Columns: {', '.join(headers[:20])}" + (" ..." if len(headers) > 20 else "")
            )
        except Exception as e:
            lines.append(f"- {f.stem} (error reading: {e})")

    return "\n".join(lines)[:MAX_CHARS]


@duckdb_toolset.tool
def load_dataset(
    ctx: RunContext[SerniaDeps],
    dataset_name: str,
    table_name: str | None = None,
) -> str:
    """Load a CSV dataset into a DuckDB table for SQL querying.

    Args:
        dataset_name: Name of the dataset (from list_datasets, without .csv extension).
        table_name: Optional table name (defaults to dataset_name).
    """
    cid = ctx.deps.conversation_id
    _validate_conversation_id(cid)

    csv_path = (DATA_BASE / cid / f"{dataset_name}.csv").resolve()
    # Validate resolved path is within expected directory
    expected_prefix = (DATA_BASE / cid).resolve()
    if not csv_path.is_relative_to(expected_prefix):
        return "Error: Invalid dataset path."
    if not csv_path.exists():
        return f"Dataset '{dataset_name}' not found. Use list_datasets to see available datasets."

    if not csv_path.is_file() or any(char in str(csv_path) for char in "*?[]"):
        return "Error: Invalid dataset path."
    if csv_path.stat().st_size > MAX_CSV_BYTES:
        return "Error: Dataset exceeds the 10 MiB import limit."
    return _run_worker(cid, "load", csv_path=str(csv_path), table_name=table_name or dataset_name)


@duckdb_toolset.tool
def describe_table(
    ctx: RunContext[SerniaDeps],
    table_name: str | None = None,
) -> str:
    """Describe a DuckDB table's schema, or list all tables if no name given.

    Args:
        table_name: Table to describe. Omit to list all tables.
    """
    return _run_worker(ctx.deps.conversation_id, "describe", table_name=table_name)


@duckdb_toolset.tool
def run_sql(ctx: RunContext[SerniaDeps], query: str) -> str:
    """Execute one analytics SQL statement against the conversation's DuckDB.

    Supports SELECT, JOIN, GROUP BY, window functions and local table creation/
    updates. File/network access, extensions and configuration changes are disabled.
    Use load_dataset to import a conversation CSV. Queries have a 30-second deadline
    and resource limits; results show at most 100 rows / 8K characters.

    Args:
        query: One SQL statement to execute.
    """
    if len(query) > MAX_QUERY_CHARS:
        return "SQL error: Query exceeds the 64,000-character limit."
    return _run_worker(ctx.deps.conversation_id, "sql", query=query)


def cleanup_stale_data(max_age_hours: int = 24) -> None:
    """Delete stale DuckDB and CSV files older than max_age_hours.

    Best-effort — never raises. Called during app startup.
    """
    cutoff = time.time() - (max_age_hours * 3600)

    for base_dir in (DUCKDB_BASE, DATA_BASE):
        if not base_dir.exists():
            continue
        try:
            for entry in base_dir.iterdir():
                try:
                    if entry.stat().st_mtime < cutoff:
                        if entry.is_file():
                            entry.unlink()
                        elif entry.is_dir():
                            # Remove CSV directories
                            for child in entry.iterdir():
                                child.unlink(missing_ok=True)
                            entry.rmdir()
                except OSError:
                    pass
        except OSError:
            pass
