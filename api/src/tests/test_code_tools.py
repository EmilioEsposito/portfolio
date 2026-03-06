"""Tests for run_python code tool — verifies print capture and helper functions."""

import pytest
from api.src.sernia_ai.tools.code_tools import run_python


class _FakeCtx:
    class deps:
        pass


CTX = _FakeCtx()


@pytest.mark.asyncio
async def test_print_only_captured():
    """print() output should be returned to the LLM, not lost to stdout."""
    result = await run_python(CTX, 'print("hello")\nprint("world")')
    assert "hello" in result
    assert "world" in result
    assert "(no return value)" not in result


@pytest.mark.asyncio
async def test_print_plus_expression():
    """Both print output and the last expression value should appear."""
    result = await run_python(CTX, 'print("debug")\n42')
    assert "debug" in result
    assert "42" in result


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
async def test_slot_filtering_pattern():
    """Reproduces the exact pattern the agent uses for Zillow tour slot filtering."""
    code = '''
slots = ["Sat 1PM", "Sun 4:50PM", "Mon 10:45AM"]
available = []
for s in slots:
    available.append(s)
    print(f"  - {s}")
print(f"Total: {len(available)}")
'''
    result = await run_python(CTX, code)
    assert "Sat 1PM" in result
    assert "Sun 4:50PM" in result
    assert "Total: 3" in result
