"""Tests for DuckDB data workbench tools and CSV export utility."""

import csv
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from api.src.sernia_ai.tools.data_export import (
    DATA_BASE,
    _sanitize_name,
    _validate_conversation_id,
    write_dataset,
)
from api.src.sernia_ai.tools.duckdb_tools import (
    DUCKDB_BASE,
    _format_result,
    _get_connection,
    cleanup_stale_data,
    describe_table,
    duckdb_toolset,
    list_datasets,
    load_dataset,
    run_sql,
)


# =============================================================================
# Smoke Tests
# =============================================================================


class TestSmoke:
    """Fast import/wiring checks."""

    def test_module_imports(self):
        """duckdb_tools and data_export modules import without error."""
        import api.src.sernia_ai.tools.duckdb_tools
        import api.src.sernia_ai.tools.data_export

    def test_toolset_has_four_tools(self):
        """duckdb_toolset exposes exactly 4 tools."""
        names = set(duckdb_toolset.tools.keys())
        assert names == {"list_datasets", "load_dataset", "describe_table", "run_sql"}

    def test_agent_imports_with_duckdb_toolset(self):
        """sernia_agent loads with the new toolset registered."""
        from api.src.sernia_ai.agent import sernia_agent
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
            "conv-123", "blanks", ["A", "B"],
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
        result = run_sql(ctx, "SELECT unit, SUM(amount) as total FROM sales GROUP BY unit ORDER BY unit")
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
        import os, time
        old_time = time.time() - 48 * 3600  # 48 hours ago
        os.utime(stale_file, (old_time, old_time))

        # Create fresh file
        fresh_file = db_dir / "fresh.duckdb"
        fresh_file.write_text("fresh")

        cleanup_stale_data(max_age_hours=24)
        assert not stale_file.exists()
        assert fresh_file.exists()
