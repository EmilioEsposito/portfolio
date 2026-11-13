"""Tests for the run_python code tool.

These run real snippets in real Monty worker subprocesses (the binary ships in
the pydantic-monty-runtime wheel), so they cover the tool's contract end to end:
output assembly, the sandbox's lack of file/network/env reach, resource limits,
and recovery after a worker is lost.
"""

import asyncio
import os
import signal

import pytest
import pytest_asyncio

from api.src.sernia_ai.tools import code_tools
from api.src.sernia_ai.tools.code_tools import (
    _OUTPUT_CAP,
    run_python,
    shutdown_code_sandbox,
    warm_code_sandbox,
)


class _FakeCtx:
    class deps:
        pass


CTX = _FakeCtx()

ERROR_PREFIX = "Code execution error: "


@pytest_asyncio.fixture(autouse=True, scope="module")
async def _stop_pool_after_module():
    """Leave no worker subprocesses behind for the rest of the test session."""
    yield
    await shutdown_code_sandbox()


# ---------------------------------------------------------------------------
# Output contract
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_print_only_captured():
    """print() output should be returned to the LLM, not lost to stdout."""
    result = await run_python(CTX, 'print("hello")\nprint("world")')
    assert result == "hello\nworld"


@pytest.mark.asyncio
async def test_print_plus_expression():
    """Print output comes first, then the last expression value."""
    result = await run_python(CTX, 'print("debug")\n42')
    assert result == "debug\n42"


@pytest.mark.asyncio
async def test_expression_only():
    result = await run_python(CTX, "2 + 2")
    assert result == "4"


@pytest.mark.asyncio
async def test_no_return_value():
    """Code with no print and no final expression → fallback message."""
    result = await run_python(CTX, "x = 1")
    assert result == "(no return value)"


@pytest.mark.asyncio
async def test_stderr_print_is_captured():
    result = await run_python(CTX, 'import sys\nprint("warn", file=sys.stderr)')
    assert result == "warn"


@pytest.mark.asyncio
async def test_calls_do_not_share_state():
    """Each call gets a fresh session — a variable from one call is gone in the next."""
    assert await run_python(CTX, "leftover = 7") == "(no return value)"
    result = await run_python(CTX, "leftover")
    assert result.startswith(ERROR_PREFIX + "NameError")


@pytest.mark.asyncio
async def test_slot_filtering_pattern():
    """Reproduces the exact pattern the agent uses for Zillow tour slot filtering."""
    code = """
slots = ["Sat 1PM", "Sun 4:50PM", "Mon 10:45AM"]
available = []
for s in slots:
    available.append(s)
    print(f"  - {s}")
print(f"Total: {len(available)}")
"""
    result = await run_python(CTX, code)
    assert "Sat 1PM" in result
    assert "Sun 4:50PM" in result
    assert "Total: 3" in result


# ---------------------------------------------------------------------------
# Truncation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_long_expression_is_truncated():
    result = await run_python(CTX, f"'x' * {_OUTPUT_CAP + 1000}")
    assert result == "x" * _OUTPUT_CAP + "\n...(truncated)"


@pytest.mark.asyncio
async def test_long_print_output_is_truncated():
    """A print loop far past the cap returns the first _OUTPUT_CAP characters."""
    result = await run_python(CTX, "for i in range(50_000):\n    print('row', i)")
    assert result.startswith("row 0\nrow 1\n")
    assert result.endswith("\n...(truncated)")
    assert len(result) == _OUTPUT_CAP + len("\n...(truncated)")


@pytest.mark.asyncio
async def test_output_exactly_at_cap_is_not_truncated():
    result = await run_python(CTX, f"'x' * {_OUTPUT_CAP}")
    assert result == "x" * _OUTPUT_CAP


# ---------------------------------------------------------------------------
# Standard-library imports (Monty 1.x) and the older helper functions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stdlib_imports_replace_helpers():
    """json / re / math / datetime work as imports, giving real objects back."""
    code = """
import json
import math
import re
from datetime import datetime

data = json.loads('{"unit": "4B", "rent": "$1,850"}')
rent = int(re.sub(r"[^0-9]", "", data["rent"]))
delta = datetime.fromisoformat("2026-03-07T13:00:00-05:00") - datetime.fromisoformat(
    "2026-03-06T17:24:00-05:00"
)
json.dumps({"unit": data["unit"], "rent": rent, "hours": round(delta.total_seconds() / 3600, 1),
            "root": math.sqrt(144)})
"""
    result = await run_python(CTX, code)
    assert result == '{"unit": "4B", "rent": 1850, "hours": 19.6, "root": 12.0}'


@pytest.mark.asyncio
async def test_stdlib_imports_new_in_monty_1x():
    """collections / itertools / decimal / csv did not exist in the 0.0.9 sandbox."""
    code = """
import csv
import itertools
from collections import Counter
from decimal import Decimal

rows = list(csv.reader(["unit,rent", "4B,1850.10", "2A,1425.15", "4B,25.00"]))[1:]
total = sum((Decimal(r[1]) for r in rows), Decimal("0"))
counts = Counter(r[0] for r in rows)
pairs = list(itertools.combinations(sorted(counts), 2))
(str(total), counts["4B"], pairs)
"""
    result = await run_python(CTX, code)
    assert result == "('3300.25', 2, [('2A', '4B')])"


@pytest.mark.asyncio
async def test_third_party_import_is_an_error():
    result = await run_python(CTX, "import requests")
    assert result == ERROR_PREFIX + "ModuleNotFoundError: No module named 'requests' (line 1)"


@pytest.mark.asyncio
async def test_hours_between():
    code = 'hours_between("2026-03-06T17:24:00-05:00", "2026-03-07T13:00:00-05:00")'
    result = await run_python(CTX, code)
    assert float(result) == pytest.approx(19.6, abs=0.1)


@pytest.mark.asyncio
async def test_days_between():
    code = 'days_between("2026-03-06T17:24:00-05:00", "2026-03-07T13:00:00-05:00")'
    result = await run_python(CTX, code)
    assert result == "0"  # floor — this is why hours_between was added


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ('add_days("2026-01-01", "30")', "2026-01-31T00:00:00"),
        ('format_date(parse_date("2025-06-15"), "%b %d, %Y")', "Jun 15, 2025"),
        ('iso_to_epoch(epoch_to_iso("1719446400000"))', "1719446400000"),
        ("json_loads('{\"a\": [1, 2]}')", "{'a': [1, 2]}"),
        ("json_dumps(\"{'a': 1}\")", '{"a": 1}'),
        ("json_dumps(json_loads('{\"a\": [1, 2]}'))", '{"a": [1, 2]}'),
        ("re_findall(r'\\d+', 'a1b22')", "['1', '22']"),
        ("re_sub(r'\\s+', ' ', 'a   b')", "a b"),
        ('math_fn("sqrt", "144")', "12.0"),
        ('math_fn("pow", "2", "10")', "1024.0"),
    ],
)
async def test_helper_functions_keep_their_0_0_9_results(code: str, expected: str):
    """Skills written against the old sandbox call these by name, with no import."""
    assert await run_python(CTX, code) == expected


@pytest.mark.asyncio
async def test_now_iso_helper_returns_a_parseable_timestamp():
    result = await run_python(
        CTX, "from datetime import datetime\ndatetime.fromisoformat(now_iso()).year"
    )
    assert int(result) >= 2026


@pytest.mark.asyncio
async def test_helper_in_a_long_loop_stays_under_the_suspension_limit():
    """Each helper call is a host round trip; Monty's default limit of 1000 would stop this."""
    code = "sum(int(days_between('2026-01-01', add_days('2026-01-01', str(i % 5)))) for i in range(1500))"
    assert await run_python(CTX, code) == "3000"


@pytest.mark.asyncio
async def test_json_dumps_helper_does_not_evaluate_code():
    """json_dumps runs in the API process, so its argument must never be executed."""
    marker = "SERNIA_CODE_TOOL_EVAL_PROBE"
    os.environ.pop(marker, None)
    payload = f"__import__('os').environ.__setitem__('{marker}', '1')"
    result = await run_python(CTX, f"json_dumps({payload!r})")
    assert marker not in os.environ
    assert result == payload  # returned unchanged, as any non-literal string is


# ---------------------------------------------------------------------------
# Errors come back as text, never as exceptions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_syntax_error():
    result = await run_python(CTX, "x = 1\ndef f(:\n    pass")
    assert result.startswith(ERROR_PREFIX + "SyntaxError: ")
    assert result.endswith("(line 2)")


@pytest.mark.asyncio
async def test_runtime_error_names_the_line():
    result = await run_python(CTX, "x = 1\ny = 0\nx / y")
    assert result == ERROR_PREFIX + "ZeroDivisionError: division by zero (line 3)"


@pytest.mark.asyncio
async def test_raised_exception_inside_function():
    result = await run_python(CTX, "def f():\n    raise ValueError('boom')\n\nf()")
    assert result == ERROR_PREFIX + "ValueError: boom (line 2)"


@pytest.mark.asyncio
async def test_error_in_helper_function_is_returned():
    result = await run_python(CTX, 'parse_date("not a date")')
    assert result.startswith(ERROR_PREFIX + "ValueError")


# ---------------------------------------------------------------------------
# No file, environment, or network access
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("code", "error"),
    [
        ("open('/etc/passwd').read()", "PermissionError"),
        ("open('notes.txt', 'w').write('x')", "PermissionError"),
        ("from pathlib import Path\nPath('/').exists()", "PermissionError"),
        ("import os\nos.listdir('/')", "PermissionError"),
        ("import os\nos.getenv('PATH')", "RuntimeError"),
        ("import os\ndict(os.environ)", "RuntimeError"),
        ("import os\nos.system('echo hi')", "AttributeError"),
        ("import socket", "ModuleNotFoundError"),
        ("import urllib.request", "ModuleNotFoundError"),
        ("import http.client", "ModuleNotFoundError"),
        ("import subprocess", "ModuleNotFoundError"),
        ("__import__('os')", "NameError"),
    ],
)
async def test_no_file_env_or_network_access(code: str, error: str):
    result = await run_python(CTX, code)
    assert result.startswith(ERROR_PREFIX + error), result


# ---------------------------------------------------------------------------
# Resource limits
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_infinite_loop_times_out(monkeypatch):
    monkeypatch.setitem(code_tools._LIMITS, "max_feed_duration_secs", 0.3)
    result = await run_python(CTX, "while True:\n    pass")
    assert result.startswith(ERROR_PREFIX + "TimeoutError")
    # The session that timed out is discarded; the next call is unaffected.
    assert await run_python(CTX, "1 + 1") == "2"


@pytest.mark.asyncio
async def test_memory_limit():
    result = await run_python(CTX, "data = []\nwhile True:\n    data.append('x' * 100_000)")
    assert result.startswith(ERROR_PREFIX + "MemoryError")
    assert await run_python(CTX, "1 + 1") == "2"


@pytest.mark.asyncio
async def test_long_sleep_is_refused():
    result = await run_python(CTX, "import time\ntime.sleep(3600)")
    assert result.startswith(ERROR_PREFIX + "TimeoutError")


@pytest.mark.asyncio
async def test_unbounded_recursion_is_an_error():
    result = await run_python(CTX, "def f(n):\n    return f(n + 1)\nf(0)")
    assert result.startswith(ERROR_PREFIX + "RecursionError")


# ---------------------------------------------------------------------------
# Pool lifecycle and recovery
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_concurrent_calls_beyond_pool_size():
    results = await asyncio.gather(*(run_python(CTX, f"{i} * 2") for i in range(12)))
    assert results == [str(i * 2) for i in range(12)]


@pytest.mark.asyncio
async def test_worker_killed_mid_call_is_reported_and_replaced(monkeypatch):
    """An OOM-kill or crash of the worker is an error string; the pool recovers."""
    await shutdown_code_sandbox()  # start from a pool with exactly one worker
    pool = await code_tools._get_pool()
    async with pool.checkout() as session:
        await session.feed_run("1")
        worker_pid = session.worker_pid
    assert worker_pid is not None

    def kill_worker() -> int:
        os.kill(worker_pid, signal.SIGKILL)
        return 1

    monkeypatch.setitem(code_tools._EXTERNAL_FUNCTIONS, "kill_worker", kill_worker)
    result = await run_python(CTX, "kill_worker()\n'unreachable'")
    assert result == ERROR_PREFIX + (
        "the sandbox process crashed while running this code; it has been restarted"
    )
    assert await run_python(CTX, "1 + 1") == "2"


@pytest.mark.asyncio
async def test_worker_killed_while_idle_is_replaced():
    await shutdown_code_sandbox()
    pool = await code_tools._get_pool()
    async with pool.checkout() as session:
        await session.feed_run("1")
        worker_pid = session.worker_pid
    assert worker_pid is not None
    os.kill(worker_pid, signal.SIGKILL)
    await asyncio.sleep(0.1)
    assert await run_python(CTX, "1 + 1") == "2"


@pytest.mark.asyncio
async def test_hard_deadline_kills_a_wedged_worker(monkeypatch):
    """With the in-sandbox time limit out of the picture, the host deadline still ends the call."""
    await shutdown_code_sandbox()
    monkeypatch.setattr(code_tools, "_REQUEST_TIMEOUT_SECS", 0.5)
    monkeypatch.setitem(code_tools._LIMITS, "max_feed_duration_secs", None)
    try:
        result = await run_python(CTX, "while True:\n    pass")
        assert result == ERROR_PREFIX + (
            "TimeoutError: the sandbox did not respond within 0.5s and was stopped"
        )
        assert await run_python(CTX, "1 + 1") == "2"
    finally:
        await shutdown_code_sandbox()  # drop the pool built with the short deadline


@pytest.mark.asyncio
async def test_shutdown_then_next_call_starts_a_new_pool():
    assert await run_python(CTX, "1 + 1") == "2"
    await shutdown_code_sandbox()
    assert code_tools._pool is None
    await shutdown_code_sandbox()  # idempotent
    assert await run_python(CTX, "2 + 2") == "4"


@pytest.mark.asyncio
async def test_warm_code_sandbox_runs_a_snippet():
    await warm_code_sandbox()
    assert code_tools._pool is not None


@pytest.mark.asyncio
async def test_missing_worker_binary_is_an_error_string(monkeypatch, tmp_path):
    """A broken install must not raise into the agent run."""
    await shutdown_code_sandbox()
    monkeypatch.setattr(code_tools, "_monty_binary", lambda: tmp_path / "monty")
    result = await run_python(CTX, "1 + 1")
    assert result.startswith(ERROR_PREFIX + "sandbox unavailable")
    assert code_tools._pool is None
