"""Tests for DuckDB data workbench tools and CSV export utility."""

import csv
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from api.src.sernia_ai.tools.data_export import (
    _sanitize_name,
    _validate_conversation_id,
    write_dataset,
)
from api.src.sernia_ai.tools.duckdb_tools import (
    _get_connection,
    cleanup_stale_data,
    describe_table,
    duckdb_toolset,
    list_datasets,
    load_dataset,
    run_sql,
)
from api.src.sernia_ai.tools.duckdb_worker import format_result as _format_result

# =============================================================================
# Smoke Tests
# =============================================================================


class TestSmoke:
    """Fast import/wiring checks."""

    def test_module_imports(self):
        """duckdb_tools and data_export modules import without error."""

    def test_toolset_has_four_tools(self):
        """duckdb_toolset exposes exactly 4 tools."""
        names = set(duckdb_toolset.tools.keys())
        assert names == {"list_datasets", "load_dataset", "describe_table", "run_sql"}

    def test_agent_imports_with_duckdb_toolset(self):
        """sernia_agent loads with the new toolset registered."""
        from api.src.sernia_ai.general_agent import sernia_agent

        # Just verify it doesn't crash on import
        assert sernia_agent is not None


# =============================================================================
# Helpers
# =============================================================================


def _make_ctx(conversation_id: str, tmp_path: Path) -> MagicMock:
    """Create a mock RunContext with deps."""
    ctx = MagicMock()
    ctx.deps = MagicMock()
    ctx.deps.conversation_id = conversation_id
    ctx.deps.workspace_path = tmp_path
    return ctx


def _write_csv(path: Path, headers: list[str], rows: list[list[str]]) -> None:
    """Write a CSV file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(headers)
        writer.writerows(rows)


# =============================================================================
# data_export tests
# =============================================================================


class TestSanitizeName:
    def test_basic(self):
        assert _sanitize_name("My Sheet") == "my_sheet"

    def test_special_chars(self):
        assert _sanitize_name("Rent Roll (2025)") == "rent_roll_2025"

    def test_empty_fallback(self):
        assert _sanitize_name("!!!") == "dataset"

    def test_truncation(self):
        long_name = "a" * 100
        assert len(_sanitize_name(long_name)) == 64

    def test_consecutive_underscores(self):
        assert _sanitize_name("a---b___c") == "a_b_c"


class TestValidateConversationId:
    def test_valid_uuid(self):
        _validate_conversation_id("abc123-def456")  # Should not raise

    def test_rejects_dot_dot(self):
        with pytest.raises(ValueError, match="\\.\\."):
            _validate_conversation_id("../etc/passwd")

    def test_rejects_slash(self):
        with pytest.raises(ValueError, match="/"):
            _validate_conversation_id("foo/bar")

    def test_rejects_backslash(self):
        with pytest.raises(ValueError, match=r"\\\\"):
            _validate_conversation_id("foo\\bar")

    def test_rejects_null(self):
        with pytest.raises(ValueError):
            _validate_conversation_id("foo\x00bar")

    def test_rejects_empty(self):
        with pytest.raises(ValueError, match="empty"):
            _validate_conversation_id("")


class TestWriteDataset:
    def test_writes_csv(self, tmp_path, monkeypatch):
        monkeypatch.setattr("api.src.sernia_ai.tools.data_export.DATA_BASE", tmp_path)
        path, count = write_dataset(
            "conv-123", "tenants", ["Name", "Unit"], [["Alice", "A1"], ["Bob", "B2"]]
        )
        assert path.exists()
        assert count == 2
        with open(path, encoding="utf-8") as f:
            reader = csv.reader(f)
            rows = list(reader)
        assert rows[0] == ["Name", "Unit"]
        assert len(rows) == 3  # header + 2 data rows

    def test_strips_blank_rows(self, tmp_path, monkeypatch):
        monkeypatch.setattr("api.src.sernia_ai.tools.data_export.DATA_BASE", tmp_path)
        path, count = write_dataset(
            "conv-123",
            "blanks",
            ["A", "B"],
            [["1", "2"], [], ["", ""], [" ", " "], ["3", "4"]],
        )
        assert count == 2  # only the two non-blank rows
        with open(path, encoding="utf-8") as f:
            reader = csv.reader(f)
            rows = list(reader)
        assert len(rows) == 3  # header + 2 data rows

    def test_sanitizes_name(self, tmp_path, monkeypatch):
        monkeypatch.setattr("api.src.sernia_ai.tools.data_export.DATA_BASE", tmp_path)
        path, _ = write_dataset("conv-123", "My Sheet!", ["A"], [["1"]])
        assert path.stem == "my_sheet"

    def test_path_traversal_rejected(self, tmp_path, monkeypatch):
        monkeypatch.setattr("api.src.sernia_ai.tools.data_export.DATA_BASE", tmp_path)
        with pytest.raises(ValueError):
            write_dataset("../evil", "data", ["A"], [["1"]])


# =============================================================================
# duckdb_tools tests
# =============================================================================


class TestListDatasets:
    def test_empty(self, tmp_path, monkeypatch):
        monkeypatch.setattr("api.src.sernia_ai.tools.duckdb_tools.DATA_BASE", tmp_path)
        ctx = _make_ctx("conv-1", tmp_path)
        result = list_datasets(ctx)
        assert "No datasets" in result

    def test_with_files(self, tmp_path, monkeypatch):
        monkeypatch.setattr("api.src.sernia_ai.tools.duckdb_tools.DATA_BASE", tmp_path)
        data_dir = tmp_path / "conv-1"
        _write_csv(data_dir / "tenants.csv", ["Name", "Unit"], [["Alice", "A1"], ["Bob", "B2"]])
        ctx = _make_ctx("conv-1", tmp_path)
        result = list_datasets(ctx)
        assert "tenants" in result
        assert "2 rows" in result
        assert "Name" in result


class TestLoadDataset:
    def test_sibling_prefix_traversal_rejected(self, tmp_path, monkeypatch):
        monkeypatch.setattr("api.src.sernia_ai.tools.duckdb_tools.DATA_BASE", tmp_path)
        sibling = tmp_path / "conv-1-other" / "secret.csv"
        _write_csv(sibling, ["Private"], [["should not be read"]])
        ctx = _make_ctx("conv-1", tmp_path)
        assert load_dataset(ctx, "../conv-1-other/secret") == "Error: Invalid dataset path."

    def test_symlink_escape_rejected(self, tmp_path, monkeypatch):
        monkeypatch.setattr("api.src.sernia_ai.tools.duckdb_tools.DATA_BASE", tmp_path)
        sibling = tmp_path / "conv-1-other" / "secret.csv"
        _write_csv(sibling, ["Private"], [["should not be read"]])
        own_dir = tmp_path / "conv-1"
        own_dir.mkdir()
        (own_dir / "link.csv").symlink_to(sibling)
        ctx = _make_ctx("conv-1", tmp_path)
        assert load_dataset(ctx, "link") == "Error: Invalid dataset path."

    def test_load(self, tmp_path, monkeypatch):
        monkeypatch.setattr("api.src.sernia_ai.tools.duckdb_tools.DATA_BASE", tmp_path)
        monkeypatch.setattr("api.src.sernia_ai.tools.duckdb_tools.DUCKDB_BASE", tmp_path / "db")
        # Also patch data_export so write_dataset uses same base
        monkeypatch.setattr("api.src.sernia_ai.tools.data_export.DATA_BASE", tmp_path)

        cid = "conv-load"
        data_dir = tmp_path / cid
        _write_csv(data_dir / "rents.csv", ["Unit", "Rent"], [["A1", "1200"], ["B2", "1500"]])

        ctx = _make_ctx(cid, tmp_path)
        result = load_dataset(ctx, "rents")
        assert "Table 'rents' created" in result
        assert "2 rows" in result
        assert "Unit" in result

    def test_nonexistent(self, tmp_path, monkeypatch):
        monkeypatch.setattr("api.src.sernia_ai.tools.duckdb_tools.DATA_BASE", tmp_path)
        monkeypatch.setattr("api.src.sernia_ai.tools.duckdb_tools.DUCKDB_BASE", tmp_path / "db")
        ctx = _make_ctx("conv-missing", tmp_path)
        result = load_dataset(ctx, "nope")
        assert "not found" in result

    def test_custom_table_name(self, tmp_path, monkeypatch):
        monkeypatch.setattr("api.src.sernia_ai.tools.duckdb_tools.DATA_BASE", tmp_path)
        monkeypatch.setattr("api.src.sernia_ai.tools.duckdb_tools.DUCKDB_BASE", tmp_path / "db")
        data_dir = tmp_path / "conv-custom"
        _write_csv(data_dir / "data.csv", ["X"], [["1"]])
        ctx = _make_ctx("conv-custom", tmp_path)
        result = load_dataset(ctx, "data", table_name="my_table")
        assert "Table 'my_table' created" in result


class TestDescribeTable:
    def test_list_all_tables(self, tmp_path, monkeypatch):
        monkeypatch.setattr("api.src.sernia_ai.tools.duckdb_tools.DATA_BASE", tmp_path)
        monkeypatch.setattr("api.src.sernia_ai.tools.duckdb_tools.DUCKDB_BASE", tmp_path / "db")

        cid = "conv-desc"
        # Create a table directly
        conn = _get_connection(cid)
        conn.execute("CREATE TABLE test_tbl (a INTEGER, b TEXT)")
        conn.execute("INSERT INTO test_tbl VALUES (1, 'x'), (2, 'y')")
        conn.close()

        ctx = _make_ctx(cid, tmp_path)
        result = describe_table(ctx, table_name=None)
        assert "test_tbl" in result
        assert "2 rows" in result

    def test_describe_specific(self, tmp_path, monkeypatch):
        monkeypatch.setattr("api.src.sernia_ai.tools.duckdb_tools.DATA_BASE", tmp_path)
        monkeypatch.setattr("api.src.sernia_ai.tools.duckdb_tools.DUCKDB_BASE", tmp_path / "db")

        cid = "conv-desc2"
        conn = _get_connection(cid)
        conn.execute("CREATE TABLE props (address TEXT, units INTEGER)")
        conn.execute("INSERT INTO props VALUES ('123 Main', 4)")
        conn.close()

        ctx = _make_ctx(cid, tmp_path)
        result = describe_table(ctx, table_name="props")
        assert "address" in result
        assert "units" in result
        assert "1 rows" in result

    def test_no_tables(self, tmp_path, monkeypatch):
        monkeypatch.setattr("api.src.sernia_ai.tools.duckdb_tools.DATA_BASE", tmp_path)
        monkeypatch.setattr("api.src.sernia_ai.tools.duckdb_tools.DUCKDB_BASE", tmp_path / "db")
        ctx = _make_ctx("conv-empty", tmp_path)
        result = describe_table(ctx, table_name=None)
        assert "No tables" in result


class TestRunSql:
    def test_select(self, tmp_path, monkeypatch):
        monkeypatch.setattr("api.src.sernia_ai.tools.duckdb_tools.DATA_BASE", tmp_path)
        monkeypatch.setattr("api.src.sernia_ai.tools.duckdb_tools.DUCKDB_BASE", tmp_path / "db")

        cid = "conv-sql"
        conn = _get_connection(cid)
        conn.execute("CREATE TABLE t (x INT, y TEXT)")
        conn.execute("INSERT INTO t VALUES (1, 'a'), (2, 'b'), (3, 'c')")
        conn.close()

        ctx = _make_ctx(cid, tmp_path)
        result = run_sql(ctx, "SELECT * FROM t ORDER BY x")
        assert "3 rows" in result
        assert "a" in result

    def test_aggregate(self, tmp_path, monkeypatch):
        monkeypatch.setattr("api.src.sernia_ai.tools.duckdb_tools.DATA_BASE", tmp_path)
        monkeypatch.setattr("api.src.sernia_ai.tools.duckdb_tools.DUCKDB_BASE", tmp_path / "db")

        cid = "conv-agg"
        conn = _get_connection(cid)
        conn.execute("CREATE TABLE sales (unit TEXT, amount INT)")
        conn.execute("INSERT INTO sales VALUES ('A1', 100), ('A1', 200), ('B1', 300)")
        conn.close()

        ctx = _make_ctx(cid, tmp_path)
        result = run_sql(
            ctx, "SELECT unit, SUM(amount) as total FROM sales GROUP BY unit ORDER BY unit"
        )
        assert "A1" in result
        assert "300" in result  # A1 total
        assert "B1" in result

    def test_create_table(self, tmp_path, monkeypatch):
        monkeypatch.setattr("api.src.sernia_ai.tools.duckdb_tools.DATA_BASE", tmp_path)
        monkeypatch.setattr("api.src.sernia_ai.tools.duckdb_tools.DUCKDB_BASE", tmp_path / "db")
        ctx = _make_ctx("conv-create", tmp_path)
        result = run_sql(ctx, "CREATE TABLE new_tbl (id INT)")
        # DuckDB returns a result with Count column for DDL
        assert "SQL error" not in result

    def test_error(self, tmp_path, monkeypatch):
        monkeypatch.setattr("api.src.sernia_ai.tools.duckdb_tools.DATA_BASE", tmp_path)
        monkeypatch.setattr("api.src.sernia_ai.tools.duckdb_tools.DUCKDB_BASE", tmp_path / "db")
        ctx = _make_ctx("conv-err", tmp_path)
        result = run_sql(ctx, "SELECT * FROM nonexistent_table")
        assert "SQL error" in result


class TestCrossTurnPersistence:
    def test_two_connections_see_same_data(self, tmp_path, monkeypatch):
        """Two separate connections to the same DB file see the same data."""
        monkeypatch.setattr("api.src.sernia_ai.tools.duckdb_tools.DUCKDB_BASE", tmp_path / "db")

        cid = "conv-persist"
        # Connection 1: create and insert
        conn1 = _get_connection(cid)
        conn1.execute("CREATE TABLE persist_test (val INT)")
        conn1.execute("INSERT INTO persist_test VALUES (42)")
        conn1.close()

        # Connection 2: read
        conn2 = _get_connection(cid)
        result = conn2.execute("SELECT val FROM persist_test").fetchone()
        conn2.close()
        assert result[0] == 42


class TestFormatResult:
    def test_basic(self):
        result = _format_result(["a", "b"], [(1, 2), (3, 4)])
        assert "a | b" in result
        assert "1 | 2" in result

    def test_truncation(self):
        rows = [(i, f"val_{i}") for i in range(200)]
        result = _format_result(["id", "val"], rows, max_rows=100)
        lines = result.strip().split("\n")
        # header + separator + 100 data rows = 102
        assert len(lines) <= 102


class TestCleanup:
    def test_cleanup_stale_data(self, tmp_path, monkeypatch):
        monkeypatch.setattr("api.src.sernia_ai.tools.duckdb_tools.DUCKDB_BASE", tmp_path / "db")
        monkeypatch.setattr("api.src.sernia_ai.tools.duckdb_tools.DATA_BASE", tmp_path / "data")

        # Create stale files
        db_dir = tmp_path / "db"
        db_dir.mkdir()
        stale_file = db_dir / "old.duckdb"
        stale_file.write_text("stale")
        import os
        import time

        old_time = time.time() - 48 * 3600  # 48 hours ago
        os.utime(stale_file, (old_time, old_time))

        # Create fresh file
        fresh_file = db_dir / "fresh.duckdb"
        fresh_file.write_text("fresh")

        cleanup_stale_data(max_age_hours=24)
        assert not stale_file.exists()
        assert fresh_file.exists()


@pytest.fixture
def analytics(tmp_path, monkeypatch):
    import api.src.sernia_ai.tools.duckdb_tools as tools

    monkeypatch.setattr(tools, "DATA_BASE", tmp_path / "data")
    monkeypatch.setattr(tools, "DUCKDB_BASE", tmp_path / "db")
    return _make_ctx("security-test", tmp_path), tools


class TestAnalyticsBoundary:
    @pytest.mark.parametrize(
        "reader",
        ["read_text", "read_blob", "read_csv", "read_csv_auto", "read_json_auto", "read_parquet"],
    )
    def test_host_file_reads_denied(self, analytics, tmp_path, reader):
        ctx, _ = analytics
        secret = tmp_path / "host-only.csv"
        secret.write_text("value\nSYNTHETIC_HOST_CANARY\n")
        result = run_sql(ctx, f"SELECT * FROM {reader}('{secret}')")
        assert result.startswith("SQL error:"), result
        assert "SYNTHETIC_HOST_CANARY" not in result

    def test_nested_query_and_persisted_view_cannot_read_host(self, analytics, tmp_path):
        ctx, _ = analytics
        secret = tmp_path / "host-only.txt"
        secret.write_text("SYNTHETIC_HOST_CANARY")
        query = f"SELECT * FROM read_text('{secret}')"
        nested = query.replace("'", "''")
        assert run_sql(ctx, f"SELECT * FROM query('{nested}')").startswith("SQL error:")
        assert run_sql(ctx, f"CREATE VIEW probe AS {query}").startswith("SQL error:")
        # A view persisted by the old unrestricted implementation stays denied.
        import duckdb

        conn = duckdb.connect(str(analytics[1].DUCKDB_BASE / "security-test.duckdb"))
        conn.execute(f"CREATE VIEW legacy_probe AS {query}")
        conn.close()
        result = run_sql(ctx, "SELECT * FROM legacy_probe")
        assert result.startswith("SQL error:")
        assert "SYNTHETIC_HOST_CANARY" not in result
        assert describe_table(ctx, "legacy_probe").startswith("SQL error:")

    @pytest.mark.parametrize(
        "query",
        [
            "SET enable_external_access=true",
            "RESET enable_external_access",
            "SET lock_configuration=false",
            "SET memory_limit='10GB'",
            "SET allowed_paths=['/tmp']",
            "PRAGMA enable_profiling='json'",
            "INSTALL httpfs",
            "LOAD httpfs",
            "SELECT 1; SELECT 2",
            "CREATE PERSISTENT SECRET probe (TYPE HTTP, BEARER_TOKEN 'synthetic')",
        ],
    )
    def test_configuration_extensions_and_persistent_secrets_denied(self, analytics, query):
        ctx, _ = analytics
        assert run_sql(ctx, query).startswith("SQL error:")
        assert "42" in run_sql(ctx, "SELECT 42")

    def test_engine_settings_cannot_be_unlocked_directly(self, analytics):
        import duckdb

        ctx, _ = analytics
        conn = _get_connection(ctx.deps.conversation_id)
        try:
            for query in [
                "SET lock_configuration=false",
                "RESET ALL",
                "SET memory_limit='10GB'",
                "SET enable_external_access=true",
            ]:
                with pytest.raises(duckdb.Error):
                    conn.execute(query)
        finally:
            conn.close()

    def test_network_denied_without_request(self, analytics):
        import socket

        ctx, _ = analytics
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen()
            listener.settimeout(0.1)
            url = f"http://127.0.0.1:{listener.getsockname()[1]}/data.csv"
            assert run_sql(ctx, f"SELECT * FROM read_csv('{url}')").startswith("SQL error:")
            with pytest.raises(TimeoutError):
                listener.accept()

    def test_file_writes_and_attach_denied(self, analytics, tmp_path):
        ctx, tools = analytics
        destination = tmp_path / "forbidden.csv"
        for query in [
            f"COPY (SELECT 42) TO '{destination}'",
            f"EXPORT DATABASE '{tmp_path / 'export'}'",
            f"ATTACH '{tmp_path / 'outside.duckdb'}' AS outside",
            f"COPY (SELECT 42) TO '{tools.DUCKDB_BASE / 'security-test.duckdb.wal'}'",
            f"EXPLAIN ANALYZE COPY (SELECT 42) TO '{tools.DUCKDB_BASE / 'security-test.duckdb.wal'}'",
        ]:
            assert run_sql(ctx, query).startswith("SQL error:")
        assert not destination.exists()
        assert not (tmp_path / "outside.duckdb").exists()
        assert not (tmp_path / "export").exists()
        assert "42" in run_sql(ctx, "SELECT 42")

    def test_controlled_csv_and_quoted_identifiers(self, analytics):
        ctx, tools = analytics
        # Quotes in a validated path remain data; table names cannot execute SQL.
        ctx.deps.conversation_id = "conversation'quote"
        path = tools.DATA_BASE / ctx.deps.conversation_id / "rents.csv"
        _write_csv(path, ["Unit", "Rent"], [["A", "1200"], ["B", "1500"]])
        table = 'rent"; DROP TABLE protected; --'
        assert "SQL error:" not in run_sql(ctx, "CREATE TABLE protected AS SELECT 7 AS value")
        assert "2 rows" in load_dataset(ctx, "rents", table)
        assert "2 rows" in describe_table(ctx, table)
        assert table in describe_table(ctx)
        assert "7" in run_sql(ctx, "SELECT * FROM protected")
        quoted = '"' + table.replace('"', '""') + '"'
        assert "2700" in run_sql(ctx, f"SELECT SUM(Rent) FROM {quoted}")
        # The import exception does not leak into subsequent SQL connections.
        assert run_sql(
            ctx, f"SELECT * FROM read_csv('{str(path).replace(chr(39), chr(39) * 2)}')"
        ).startswith("SQL error:")

    def test_cross_turn_analytics_join_window_and_reload(self, analytics):
        ctx, tools = analytics
        path = tools.DATA_BASE / ctx.deps.conversation_id / "rents.csv"
        _write_csv(path, ["Unit", "Rent"], [["A", "1200"], ["B", "1500"]])
        assert "2 rows" in load_dataset(ctx, "rents")
        assert "SQL error:" not in run_sql(
            ctx, "CREATE TABLE units AS SELECT 'A' AS unit, 'Maryland' AS property"
        )
        result = run_sql(
            ctx,
            "WITH matched AS (SELECT r.Unit, r.Rent, u.property FROM rents r JOIN units u ON r.Unit = u.unit) SELECT property, SUM(Rent) OVER () AS total FROM matched",
        )
        assert "Maryland | 1200" in result
        _write_csv(path, ["Unit", "Rent"], [["A", "1300"]])
        assert "1 rows" in load_dataset(ctx, "rents")
        assert "1300" in run_sql(ctx, "SELECT SUM(Rent) FROM rents")
        other = _make_ctx("another-conversation", ctx.deps.workspace_path)
        assert run_sql(other, "SELECT * FROM rents").startswith("SQL error:")

    def test_large_result_is_bounded_without_exact_count(self, analytics):
        ctx, _ = analytics
        result = run_sql(ctx, "SELECT i FROM range(10000000) t(i)")
        assert "more than 100 rows" in result
        assert "\n99\n" in result
        assert "\n100\n" not in result
        assert len(result) < 8200

    def test_fetch_does_not_materialize_full_result(self):
        from api.src.sernia_ai.tools.duckdb_worker import execute_sql

        conn = MagicMock()
        statement = MagicMock()
        statement.type.name = "SELECT"
        conn.extract_statements.return_value = [statement]
        result = conn.execute.return_value
        result.description = [("value",)]
        result.fetchmany.return_value = [(i,) for i in range(101)]
        assert "more than 100 rows" in execute_sql(conn, "SELECT * FROM large_table")
        result.fetchmany.assert_called_once_with(101)
        result.fetchall.assert_not_called()

    def test_timeout_kills_query_and_next_turn_works(self, analytics, monkeypatch):
        import time

        ctx, tools = analytics
        monkeypatch.setattr(tools, "QUERY_TIMEOUT_SECONDS", 0.5)
        start = time.monotonic()
        result = run_sql(
            ctx, "SELECT SUM(a.i * b.i) FROM range(10000000) a(i), range(10000000) b(i)"
        )
        assert "time limit" in result
        assert time.monotonic() - start < 5
        monkeypatch.setattr(tools, "QUERY_TIMEOUT_SECONDS", 30)
        assert "42" in run_sql(ctx, "SELECT 42")

    def test_import_size_and_query_size_limits(self, analytics):
        ctx, tools = analytics
        path = tools.DATA_BASE / ctx.deps.conversation_id / "large.csv"
        path.parent.mkdir(parents=True)
        with path.open("wb") as file:
            file.truncate(tools.MAX_CSV_BYTES + 1)
        assert "10 MiB" in load_dataset(ctx, "large")
        assert "character limit" in run_sql(ctx, " " * (tools.MAX_QUERY_CHARS + 1))

    def test_busy_workers_fail_fast(self, analytics, monkeypatch):
        import threading

        ctx, tools = analytics
        monkeypatch.setattr(tools, "_WORKER_SLOTS", threading.BoundedSemaphore(0))
        assert "busy" in run_sql(ctx, "SELECT 42")

    def test_list_does_not_follow_host_symlink(self, analytics, tmp_path):
        ctx, tools = analytics
        secret = tmp_path / "host.csv"
        secret.write_text("SYNTHETIC_HOST_CANARY\nvalue\n")
        directory = tools.DATA_BASE / ctx.deps.conversation_id
        directory.mkdir(parents=True)
        (directory / "link.csv").symlink_to(secret)
        assert "SYNTHETIC_HOST_CANARY" not in list_datasets(ctx)


@pytest.mark.skipif(sys.platform != "linux", reason="Production OS address-space limit")
def test_oversized_value_cannot_exhaust_api_process(analytics):
    ctx, _ = analytics
    result = run_sql(ctx, "SELECT repeat('x', 1500000000)")
    assert result.startswith("SQL error:")
    assert "42" in run_sql(ctx, "SELECT 42")
