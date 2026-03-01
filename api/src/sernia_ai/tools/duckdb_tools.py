"""DuckDB toolset for per-conversation SQL analytics.

Provides 4 tools: list_datasets, load_dataset, describe_table, run_sql.
Each conversation gets a file-backed DuckDB at /tmp/sernia_duckdb/<conversation_id>.duckdb.
"""

import csv
import os
import time
from pathlib import Path

import duckdb
from pydantic_ai import FunctionToolset, RunContext

from api.src.sernia_ai.deps import SerniaDeps
from api.src.sernia_ai.tools.data_export import DATA_BASE, _validate_conversation_id

duckdb_toolset = FunctionToolset()

DUCKDB_BASE = Path("/tmp/sernia_duckdb")


def _get_connection(conversation_id: str) -> duckdb.DuckDBPyConnection:
    """Open a file-backed DuckDB for this conversation."""
    _validate_conversation_id(conversation_id)
    DUCKDB_BASE.mkdir(parents=True, exist_ok=True)
    db_path = DUCKDB_BASE / f"{conversation_id}.duckdb"
    return duckdb.connect(str(db_path))


def _format_result(
    columns: list[str],
    rows: list[tuple],
    max_rows: int = 100,
    max_chars: int = 8000,
) -> str:
    """Format query results as a pipe-separated table."""
    lines: list[str] = []
    lines.append(" | ".join(columns))
    lines.append("-" * len(lines[0]))

    for row in rows[:max_rows]:
        lines.append(" | ".join(str(v) for v in row))

    text = "\n".join(lines)
    if len(text) > max_chars:
        text = text[:max_chars] + "\n...(truncated)"
    return text


@duckdb_toolset.tool
def list_datasets(ctx: RunContext[SerniaDeps]) -> str:
    """List CSV datasets available for this conversation. Shows filename, row count, and columns.

    Use load_dataset to import a dataset into DuckDB for SQL querying.
    """
    data_dir = DATA_BASE / ctx.deps.conversation_id
    if not data_dir.exists():
        return "No datasets available. Data-fetching tools (like read_google_sheet) will create datasets automatically."

    csv_files = sorted(data_dir.glob("*.csv"))
    if not csv_files:
        return "No datasets available."

    lines: list[str] = []
    for f in csv_files:
        try:
            with open(f, encoding="utf-8") as fh:
                reader = csv.reader(fh)
                headers = next(reader, [])
                row_count = sum(1 for _ in reader)
            lines.append(
                f"- {f.stem} ({row_count} rows, {len(headers)} cols)\n"
                f"  Columns: {', '.join(headers[:20])}"
                + (" ..." if len(headers) > 20 else "")
            )
        except Exception as e:
            lines.append(f"- {f.stem} (error reading: {e})")

    return "\n".join(lines)


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
    if not str(csv_path).startswith(str(expected_prefix)):
        return "Error: Invalid dataset path."
    if not csv_path.exists():
        return f"Dataset '{dataset_name}' not found. Use list_datasets to see available datasets."

    tbl = table_name or dataset_name
    conn = _get_connection(cid)
    try:
        conn.execute(f"DROP TABLE IF EXISTS \"{tbl}\"")
        conn.execute(
            f"CREATE TABLE \"{tbl}\" AS SELECT * FROM read_csv('{csv_path}', "
            f"header=true, auto_detect=true, null_padding=true, "
            f"ignore_errors=true, parallel=false)"
        )
        # Get schema info
        schema = conn.execute(f"DESCRIBE \"{tbl}\"").fetchall()
        row_count = conn.execute(f"SELECT count(*) FROM \"{tbl}\"").fetchone()[0]
        col_info = ", ".join(f"{col[0]} ({col[1]})" for col in schema)
        return f"Table '{tbl}' created ({row_count} rows). Columns: {col_info}"
    finally:
        conn.close()


@duckdb_toolset.tool
def describe_table(
    ctx: RunContext[SerniaDeps],
    table_name: str | None = None,
) -> str:
    """Describe a DuckDB table's schema, or list all tables if no name given.

    Args:
        table_name: Table to describe. Omit to list all tables.
    """
    conn = _get_connection(ctx.deps.conversation_id)
    try:
        if table_name is None:
            tables = conn.execute("SHOW TABLES").fetchall()
            if not tables:
                return "No tables loaded. Use load_dataset to import a CSV first."
            lines: list[str] = []
            for (tbl,) in tables:
                count = conn.execute(f"SELECT count(*) FROM \"{tbl}\"").fetchone()[0]
                lines.append(f"- {tbl} ({count} rows)")
            return "Tables:\n" + "\n".join(lines)
        else:
            schema = conn.execute(f"DESCRIBE \"{table_name}\"").fetchall()
            row_count = conn.execute(f"SELECT count(*) FROM \"{table_name}\"").fetchone()[0]
            lines = [f"Table '{table_name}' ({row_count} rows):", ""]
            lines.append("Column | Type | Nullable")
            lines.append("--- | --- | ---")
            for col in schema:
                lines.append(f"{col[0]} | {col[1]} | {col[2]}")
            return "\n".join(lines)
    except duckdb.CatalogException:
        return f"Table '{table_name}' not found. Use describe_table() with no args to list tables."
    finally:
        conn.close()


@duckdb_toolset.tool
def run_sql(ctx: RunContext[SerniaDeps], query: str) -> str:
    """Execute any SQL query against the conversation's DuckDB database.

    Supports full DuckDB SQL: SELECT, JOIN, GROUP BY, window functions, CREATE TABLE, etc.
    Results are capped at 100 rows / 8K characters.

    Args:
        query: SQL query to execute.
    """
    conn = _get_connection(ctx.deps.conversation_id)
    try:
        result = conn.execute(query)
        if result.description is None:
            return "Query executed successfully (no results returned)."

        columns = [desc[0] for desc in result.description]
        rows = result.fetchall()
        total_rows = len(rows)
        total_cols = len(columns)

        formatted = _format_result(columns, rows)
        return f"{formatted}\n\nQuery returned {total_rows} rows, {total_cols} columns."
    except duckdb.Error as e:
        return f"SQL error: {e}"
    finally:
        conn.close()


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
