"""Credential-free DuckDB subprocess. Never import the API or load its environment.

Engine permissions restrict SQL I/O; OS limits and the parent's deadline bound
resource consumption. Run this file with the venv Python's isolated (-I) mode.
"""

import json
import resource
import sys
from pathlib import Path

import duckdb

MAX_ROWS = 100
MAX_CHARS = 8000
MAX_CSV_BYTES = 10 * 1024 * 1024
MAX_DATABASE_BYTES = 128 * 1024 * 1024


def quote_identifier(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def open_connection(db_path: Path, csv_path: Path | None = None) -> duckdb.DuckDBPyConnection:
    """Lock permissions before any supplied SQL or persisted object is evaluated.

    Only the import operation gets a single validated CSV path. Never reuse its
    connection for model SQL. DuckDB also permits its own DB/WAL files internally.
    """
    conn = duckdb.connect(
        str(db_path),
        config={
            "allow_unsigned_extensions": False,
            "allow_community_extensions": False,
            "autoload_known_extensions": False,
            "autoinstall_known_extensions": False,
            "allow_persistent_secrets": False,
            "allow_unredacted_secrets": False,
            "memory_limit": "128MB",
            "threads": 1,
            "temp_directory": "",
            "max_temp_directory_size": "0B",
        },
    )
    try:
        # These options must be set after opening the database, before locking it.
        conn.execute("SET enable_logging = false")
        conn.execute("SET allowed_paths = ?", [[str(csv_path)] if csv_path else []])
        conn.execute("SET allowed_directories = []")
        conn.execute("SET enable_external_access = false")
        conn.execute("SET lock_configuration = true")
        return conn
    except Exception:
        conn.close()
        raise


def format_result(
    columns: list[str], rows: list[tuple], max_rows: int = MAX_ROWS, max_chars: int = MAX_CHARS
) -> str:
    """Format only the bounded result page, limiting output as it is assembled."""
    header = " | ".join(columns)[:max_chars]
    text = header + "\n" + "-" * len(header)
    for row in rows[:max_rows]:
        if len(text) >= max_chars:
            break
        text += "\n" + " | ".join(str(value)[:max_chars] for value in row)
    return text[:max_chars] + ("\n...(truncated)" if len(text) > max_chars else "")


def execute_sql(conn: duckdb.DuckDBPyConnection, query: str) -> str:
    statements = conn.extract_statements(query)
    # Parse with DuckDB itself, not a keyword/regex filter. Permissions below the
    # parser also cover nested functions, views and macros. Exclude COPY because
    # DuckDB implicitly allowlists its own DB/WAL, which COPY could corrupt.
    allowed = {"SELECT", "CREATE", "INSERT", "UPDATE", "DELETE", "ALTER", "DROP"}
    if len(statements) != 1 or statements[0].type.name not in allowed:
        return "SQL error: Use one analytics statement (SELECT, CREATE, INSERT, UPDATE, DELETE, ALTER or DROP)."
    result = conn.execute(query)
    if result.description is None:
        return "Query executed successfully (no results returned)."
    columns = [desc[0] for desc in result.description]
    rows = result.fetchmany(MAX_ROWS + 1)
    count = f"more than {MAX_ROWS}" if len(rows) > MAX_ROWS else str(len(rows))
    return f"{format_result(columns, rows)}\n\nQuery returned {count} rows, {len(columns)} columns."


def load_csv(conn: duckdb.DuckDBPyConnection, csv_path: Path, table_name: str) -> str:
    table = quote_identifier(table_name)
    # Atomic replacement preserves an existing dataset when parsing/import fails.
    conn.execute("BEGIN")
    conn.execute(f"DROP TABLE IF EXISTS {table}")
    conn.execute(
        f"CREATE TABLE {table} AS SELECT * FROM read_csv(?, "
        "header=true, auto_detect=true, null_padding=true, ignore_errors=true, parallel=false)",
        [str(csv_path)],
    )
    conn.execute("COMMIT")
    schema = conn.execute(f"DESCRIBE {table}").fetchmany(MAX_ROWS)
    count = conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
    columns = ", ".join(f"{col[0]} ({col[1]})" for col in schema)
    return f"Table '{table_name}' created ({count} rows). Columns: {columns}"


def describe(conn: duckdb.DuckDBPyConnection, table_name: str | None) -> str:
    if table_name is None:
        tables = conn.execute("SHOW TABLES").fetchmany(MAX_ROWS + 1)
        if not tables:
            return "No tables loaded. Use load_dataset to import a CSV first."
        lines = ["Tables:"]
        for (name,) in tables[:MAX_ROWS]:
            count = conn.execute(f"SELECT count(*) FROM {quote_identifier(name)}").fetchone()[0]
            lines.append(f"- {name} ({count} rows)")
        if len(tables) > MAX_ROWS:
            lines.append("...(truncated)")
        return "\n".join(lines)
    table = quote_identifier(table_name)
    try:
        schema = conn.execute(f"DESCRIBE {table}").fetchmany(MAX_ROWS)
        count = conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
        lines = [
            f"Table '{table_name}' ({count} rows):",
            "",
            "Column | Type | Nullable",
            "--- | --- | ---",
        ]
        lines.extend(f"{col[0]} | {col[1]} | {col[2]}" for col in schema)
        return "\n".join(lines)
    except duckdb.CatalogException:
        return f"Table '{table_name}' not found. Use describe_table() with no args to list tables."


def apply_resource_limits() -> None:
    """Linux production has a hard address-space ceiling, including result values."""
    resource.setrlimit(resource.RLIMIT_CPU, (20, 20))
    resource.setrlimit(resource.RLIMIT_FSIZE, (MAX_DATABASE_BYTES, MAX_DATABASE_BYTES))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    if sys.platform == "linux":
        resource.setrlimit(resource.RLIMIT_AS, (1024 * 1024 * 1024, 1024 * 1024 * 1024))


def main() -> None:
    apply_resource_limits()
    request = json.load(sys.stdin)
    db_path = Path(request["db_path"])
    csv_path = Path(request["csv_path"]) if request.get("csv_path") else None
    try:
        conn = open_connection(db_path, csv_path)
        try:
            if request["operation"] == "sql":
                result = execute_sql(conn, request["query"])
            elif request["operation"] == "load":
                result = load_csv(conn, csv_path, request["table_name"])
            elif request["operation"] == "describe":
                result = describe(conn, request.get("table_name"))
            else:
                raise ValueError("Unknown analytics operation")
        finally:
            conn.close()
    except (duckdb.Error, ValueError, OSError) as exc:
        result = f"SQL error: {exc}"
    print(json.dumps(result[: MAX_CHARS + 200]))


if __name__ == "__main__":
    main()
