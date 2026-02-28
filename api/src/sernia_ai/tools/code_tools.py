"""
Code execution tools — secure Python via Monty.

Uses pydantic-monty (a minimal, secure Python interpreter written in Rust)
to let the agent run simple Python code for data manipulation, formatting,
counting, math, etc. — without filesystem or network access.

Monty only supports sys/typing as imports (it's a Rust-based subset of Python).
Common stdlib operations (datetime, json, re, math) are exposed as callable
external functions so the sandbox can use them.
"""

import json as _json
import math as _math
import re as _re
from datetime import datetime, timedelta

import logfire
import pydantic_monty
from pydantic_ai import FunctionToolset, RunContext

from api.src.sernia_ai.deps import SerniaDeps

code_toolset = FunctionToolset()

# Cap output to avoid blowing up context
_OUTPUT_CAP = 5_000

# ---------------------------------------------------------------------------
# External functions bridging host-side stdlib into the sandbox
# ---------------------------------------------------------------------------
# Monty's Rust runtime doesn't include stdlib modules. These functions are
# registered as external_functions so sandbox code can call them directly.


def _now_iso() -> str:
    return datetime.now().isoformat()


def _parse_date(s: str) -> str:
    return datetime.fromisoformat(s).isoformat()


def _format_date(iso: str, fmt: str) -> str:
    return datetime.fromisoformat(iso).strftime(fmt)


def _days_between(iso_a: str, iso_b: str) -> str:
    return str((datetime.fromisoformat(iso_b) - datetime.fromisoformat(iso_a)).days)


def _add_days(iso: str, days: str) -> str:
    return (datetime.fromisoformat(iso) + timedelta(days=int(days))).isoformat()


def _epoch_to_iso(epoch_ms: str) -> str:
    return datetime.fromtimestamp(int(epoch_ms) / 1000).isoformat()


def _iso_to_epoch(iso: str) -> str:
    return str(int(datetime.fromisoformat(iso).timestamp() * 1000))


def _json_loads(s: str) -> str:
    return repr(_json.loads(s))


def _json_dumps(s: str) -> str:
    try:
        obj = eval(s)  # noqa: S307 — sandbox repr output only
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


_EXTERNAL_FUNCTIONS: dict[str, callable] = {
    "now_iso": _now_iso,
    "parse_date": _parse_date,
    "format_date": _format_date,
    "days_between": _days_between,
    "add_days": _add_days,
    "epoch_to_iso": _epoch_to_iso,
    "iso_to_epoch": _iso_to_epoch,
    "json_loads": _json_loads,
    "json_dumps": _json_dumps,
    "re_findall": _re_findall,
    "re_sub": _re_sub,
    "math_fn": _math_fn,
}

_EXT_FN_NAMES = list(_EXTERNAL_FUNCTIONS.keys())


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
    slicing, math, dataclasses). No filesystem or network access.

    The last expression in the code is returned as the result.
    Use print() to include intermediate output.

    Monty does NOT support import statements. Instead, these helper functions
    are available directly (no imports needed):

    **Datetime**:
    - now_iso() → current datetime as ISO string
    - parse_date("2025-06-15") → ISO datetime string
    - format_date(iso, "%Y-%m-%d") → formatted string
    - days_between(iso_a, iso_b) → days as string (b minus a)
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
    try:
        m = pydantic_monty.Monty(
            code,
            external_functions=_EXT_FN_NAMES,
        )
        result = await pydantic_monty.run_monty_async(
            m,
            external_functions=_EXTERNAL_FUNCTIONS,
        )

        output = str(result) if result is not None else "(no return value)"
        if len(output) > _OUTPUT_CAP:
            output = output[:_OUTPUT_CAP] + "\n...(truncated)"
        return output
    except pydantic_monty.MontyError as e:
        logfire.info(f"run_python MontyError: {e}")
        return f"Code execution error: {e}"
