"""
Code execution tools — secure Python via Monty.

Uses pydantic-monty 1.x (a minimal, secure Python interpreter written in Rust)
to let the agent run simple Python code for data manipulation, formatting,
counting, math, etc. — without filesystem or network access.

The interpreter never runs in this process. ``AsyncMonty`` owns a small pool of
``monty`` worker subprocesses (the binary ships in the ``pydantic-monty-runtime``
wheel); each ``run_python`` call checks out a fresh session, feeds it one
snippet, and returns the worker. A worker that crashes or is killed at a limit
is replaced by the pool, so one bad snippet cannot break later calls.

Nothing is mounted and no ``os=`` handler is passed, so the sandbox has no
filesystem, no environment variables, and no network modules. Its only reach
into the host is the helper functions in ``_EXTERNAL_FUNCTIONS``.

Monty's standard-library subset covers json, re, math, datetime and more, so
the helpers are no longer required. They stay registered because skills and
past habits call them.
"""

import ast
import asyncio
import json as _json
import math as _math
import re as _re
import sysconfig
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path

import logfire
import pydantic_monty
from pydantic_ai import FunctionToolset, RunContext

from api.src.sernia_ai.deps import SerniaDeps

code_toolset = FunctionToolset()

# Cap output to avoid blowing up context
_OUTPUT_CAP = 5_000

# ---------------------------------------------------------------------------
# Resource limits
# ---------------------------------------------------------------------------
# Enforced inside the worker for every call. The agent's snippets are small
# data-shaping jobs over tool output that already fits in a model context, so
# they finish in milliseconds and a few MB; these numbers are generous for that
# work and tight enough that a runaway loop costs seconds, not a stuck run.
#
# - max_feed_duration_secs: execution time for one snippet. Time spent inside a
#   helper function or a sleep is not counted (see the two limits below).
# - max_memory: sandbox heap. With _MAX_WORKERS workers the worst case is
#   _MAX_WORKERS x 64 MiB on top of the API process.
# - max_recursion_depth: Monty's default, stated so it is visible here.
# - max_suspensions: host round trips per call. Every helper call is one, and
#   Monty's default of 1000 would break a loop that calls a date helper per
#   row; 10,000 keeps those working while still ending a loop that never stops.
# - max_total_sleep_secs: `time.sleep()` is now importable and the agent has no
#   reason to wait inside a tool call; asking for more raises TimeoutError.
_LIMITS: pydantic_monty.ResourceLimits = {
    "max_feed_duration_secs": 10.0,
    "max_memory": 64 * 1024 * 1024,
    "max_recursion_depth": 1000,
    "max_suspensions": 10_000,
    "max_total_sleep_secs": 5.0,
}

# ---------------------------------------------------------------------------
# Worker pool
# ---------------------------------------------------------------------------
# One pool per process, created on the first run_python call and reused. The
# API shuts it down from its lifespan (api/index.py); a process that exits
# without doing so (pytest, a script) still takes its workers down with it.
_MIN_WORKERS = 1
_MAX_WORKERS = 4
# How long a call waits for a free worker before giving up with an error.
_CHECKOUT_TIMEOUT_SECS = 10.0
# Host-side backstop: a worker that has not answered in this long is killed.
# The in-sandbox time limit normally fires first; this covers an interpreter
# that is wedged somewhere the limit is not checked.
_REQUEST_TIMEOUT_SECS = 30.0

_pool: pydantic_monty.AsyncMonty | None = None
_pool_lock = asyncio.Lock()


def _monty_binary() -> Path:
    """The worker binary installed next to this interpreter.

    Pinned explicitly so a `monty` earlier on PATH, or a MONTY_BIN variable,
    can never be what runs model-written code.
    """
    return Path(sysconfig.get_path("scripts")) / "monty"


async def _get_pool() -> pydantic_monty.AsyncMonty:
    global _pool
    if _pool is None:
        async with _pool_lock:
            if _pool is None:
                pool = pydantic_monty.AsyncMonty(
                    binary_path=_monty_binary(),
                    min_processes=_MIN_WORKERS,
                    max_processes=_MAX_WORKERS,
                    checkout_timeout=_CHECKOUT_TIMEOUT_SECS,
                    request_timeout=_REQUEST_TIMEOUT_SECS,
                )
                await pool.__aenter__()
                _pool = pool
    return _pool


async def warm_code_sandbox() -> None:
    """Start the pool and run one snippet, raising if the sandbox cannot run.

    Called at API startup on hosted environments so a deploy whose image cannot
    run the worker binary fails its health check instead of going live.
    """
    pool = await _get_pool()
    async with pool.checkout(limits=_LIMITS) as session:
        if await session.feed_run("1 + 1") != 2:
            raise RuntimeError("run_python sandbox returned a wrong result for 1 + 1")


async def shutdown_code_sandbox() -> None:
    """Stop the worker pool. A later run_python call starts a new one."""
    global _pool
    pool, _pool = _pool, None
    if pool is not None:
        await pool.__aexit__(None, None, None)


# ---------------------------------------------------------------------------
# External functions bridging host-side stdlib into the sandbox
# ---------------------------------------------------------------------------
# These predate import support in the sandbox and run in the API process, not
# in the worker. They are passed as `external_lookup`, so sandbox code can call
# them by name. Their return types (mostly strings) are unchanged from 0.0.9.


def _now_iso() -> str:
    return datetime.now().isoformat()


def _parse_date(s: str) -> str:
    return datetime.fromisoformat(s).isoformat()


def _format_date(iso: str, fmt: str) -> str:
    return datetime.fromisoformat(iso).strftime(fmt)


def _days_between(iso_a: str, iso_b: str) -> str:
    return str((datetime.fromisoformat(iso_b) - datetime.fromisoformat(iso_a)).days)


def _hours_between(iso_a: str, iso_b: str) -> str:
    delta = datetime.fromisoformat(iso_b) - datetime.fromisoformat(iso_a)
    return str(round(delta.total_seconds() / 3600, 2))


def _add_days(iso: str, days: str) -> str:
    return (datetime.fromisoformat(iso) + timedelta(days=int(days))).isoformat()


def _epoch_to_iso(epoch_ms: str) -> str:
    return datetime.fromtimestamp(int(epoch_ms) / 1000).isoformat()


def _iso_to_epoch(iso: str) -> str:
    return str(int(datetime.fromisoformat(iso).timestamp() * 1000))


def _json_loads(s: str) -> str:
    return repr(_json.loads(s))


def _json_dumps(s: str) -> str:
    # `s` comes from model-written code and this runs in the API process, so it
    # is parsed as a literal and never evaluated.
    try:
        obj = ast.literal_eval(s)
    except Exception:
        return s
    return _json.dumps(obj, default=str)


def _re_findall(pattern: str, string: str) -> str:
    return repr(_re.findall(pattern, string))


def _re_sub(pattern: str, repl: str, string: str) -> str:
    return _re.sub(pattern, repl, string)


def _math_fn(fn_name: str, *args: str) -> str:
    fn = getattr(_math, fn_name, None)
    if fn is None:
        return f"Unknown math function: {fn_name}"
    return str(fn(*(float(a) for a in args)))


_EXTERNAL_FUNCTIONS: dict[str, Callable[..., object]] = {
    "now_iso": _now_iso,
    "parse_date": _parse_date,
    "format_date": _format_date,
    "days_between": _days_between,
    "hours_between": _hours_between,
    "add_days": _add_days,
    "epoch_to_iso": _epoch_to_iso,
    "iso_to_epoch": _iso_to_epoch,
    "json_loads": _json_loads,
    "json_dumps": _json_dumps,
    "re_findall": _re_findall,
    "re_sub": _re_sub,
    "math_fn": _math_fn,
}


class _PrintBuffer:
    """Print sink that keeps the first `limit` characters and drops the rest.

    The tool only ever returns `_OUTPUT_CAP` characters, so there is no reason
    to hold megabytes of output from a print loop in the API process.
    """

    def __init__(self, limit: int) -> None:
        self._limit = limit
        self._parts: list[str] = []
        self._size = 0

    def __call__(self, _stream: str, text: str) -> None:
        room = self._limit - self._size
        if room > 0:
            kept = text[:room]
            self._parts.append(kept)
            self._size += len(kept)

    @property
    def text(self) -> str:
        return "".join(self._parts)


def _describe_error(exc: pydantic_monty.MontyError) -> str:
    """One line for the model: `ErrorType: message`, plus the line when known."""
    if isinstance(exc, pydantic_monty.MontyCrashedError):
        if exc.timed_out:
            return (
                f"TimeoutError: the sandbox did not respond within {_REQUEST_TIMEOUT_SECS:g}s "
                "and was stopped"
            )
        return "the sandbox process crashed while running this code; it has been restarted"

    if isinstance(exc, pydantic_monty.MontySyntaxError | pydantic_monty.MontyRuntimeError):
        message = exc.display(format="type-msg")
        frames = exc.traceback()
        if frames:
            return f"{message} (line {frames[-1].line})"
        return message
    return str(exc)


@code_toolset.tool
async def run_python(
    ctx: RunContext[SerniaDeps],
    code: str,
) -> str:
    """Execute Python code in a secure sandbox and return the result.

    Use this for data manipulation, string formatting, math, counting, sorting,
    filtering, date calculations, or any computation on data from other tools.

    The code runs in Monty — a minimal secure Python interpreter. It supports
    core Python (variables, functions, loops, list/dict comprehensions, f-strings,
    slicing, math, dataclasses, simple classes without inheritance). No filesystem
    or network access. Each call is limited to 10 seconds of execution and 64 MB
    of memory.

    Output: The last expression value AND any print() output are both returned.
    Use print() for multi-line results (e.g. inside loops). If your code has
    no final expression, print() is the only way to produce output.

    Standard-library imports work: json, re, math, datetime, decimal, csv,
    collections, itertools, functools, random, hashlib, base64, copy, bisect,
    time, typing. Not available: zoneinfo, statistics, string, and all
    third-party packages (no pandas, numpy, requests). `datetime.now()` is UTC.

    These helper functions are also available directly (no imports needed):

    **Datetime**:
    - now_iso() → current datetime as ISO string
    - parse_date("2025-06-15") → ISO datetime string
    - format_date(iso, "%Y-%m-%d") → formatted string
    - days_between(iso_a, iso_b) → integer days as string (b minus a, floored)
    - hours_between(iso_a, iso_b) → decimal hours as string (b minus a, e.g. "19.6")
    - add_days(iso, "30") → ISO string with days added
    - epoch_to_iso("1719446400000") → ISO string from epoch milliseconds
    - iso_to_epoch(iso) → epoch milliseconds as string

    **JSON**:
    - json_loads(s) → parsed JSON as Python object
    - json_dumps(obj) → JSON string

    **Regex**:
    - re_findall(pattern, string) → list of matches
    - re_sub(pattern, repl, string) → substituted string

    **Math**:
    - math_fn("sqrt", "144") → "12.0" (also: ceil, floor, log, pow, etc.)

    Args:
        code: Python code to execute. The return value of the last expression is captured.
    """
    printed = _PrintBuffer(_OUTPUT_CAP + 1)
    try:
        pool = await _get_pool()
        # A fresh session per call: nothing carries over between calls, and a
        # session that hit a limit is never fed again.
        async with pool.checkout(limits=_LIMITS) as session:
            result = await session.feed_run(
                code,
                external_lookup=_EXTERNAL_FUNCTIONS,
                print_callback=printed,
            )
    except pydantic_monty.MontyCrashedError as e:
        logfire.warn(f"run_python worker lost: {e}", timed_out=e.timed_out)
        return f"Code execution error: {_describe_error(e)}"
    except pydantic_monty.MontyError as e:
        logfire.info(f"run_python MontyError: {e}")
        return f"Code execution error: {_describe_error(e)}"
    except Exception as e:
        # Not the snippet's fault: the pool could not start, or no worker came
        # free in time. Still returned as text so the run can carry on.
        logfire.exception("run_python sandbox unavailable")
        return f"Code execution error: sandbox unavailable ({type(e).__name__}: {e})"

    # Build output: captured print() lines + final expression value
    parts: list[str] = []
    if printed.text:
        parts.append(printed.text.rstrip("\n"))
    if result is not None:
        parts.append(str(result))
    output = "\n".join(parts) if parts else "(no return value)"

    if len(output) > _OUTPUT_CAP:
        output = output[:_OUTPUT_CAP] + "\n...(truncated)"
    return output
