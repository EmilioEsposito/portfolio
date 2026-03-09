"""
Test that simultaneous APScheduler job firings produce separate traces.

Verifies the _new_trace wrapper correctly detaches from the parent context
when multiple jobs fire at the same instant (the real-world scenario where
ClickUp + scheduled checks both fire at 8am ET).

Run with:
    pytest api/src/tests/test_apscheduler_tracing.py -v -s
"""

import asyncio

import pytest
from opentelemetry import trace

from api.src.apscheduler_service.service import _new_trace


def _get_current_trace_id() -> str:
    span = trace.get_current_span()
    ctx = span.get_span_context()
    return format(ctx.trace_id, "032x") if ctx.trace_id else ""


@pytest.mark.asyncio
async def test_new_trace_creates_separate_trace_ids():
    """Each _new_trace-wrapped call should get its own trace_id."""
    collected: list[str] = []

    async def job_a():
        collected.append(("a", _get_current_trace_id()))

    async def job_b():
        collected.append(("b", _get_current_trace_id()))

    wrapped_a = _new_trace(job_a)
    wrapped_b = _new_trace(job_b)

    await asyncio.gather(wrapped_a(), wrapped_b())

    trace_a = next(tid for name, tid in collected if name == "a")
    trace_b = next(tid for name, tid in collected if name == "b")

    print(f"\nJob A trace: {trace_a}")
    print(f"Job B trace: {trace_b}")

    assert trace_a != "", "Job A should have a trace_id"
    assert trace_b != "", "Job B should have a trace_id"
    assert trace_a != trace_b, "Simultaneous jobs must have different trace_ids"


@pytest.mark.asyncio
async def test_new_trace_detaches_from_parent():
    """Wrapped jobs should NOT inherit the caller's trace context."""
    import logfire

    parent_trace_id = None
    child_trace_id = None

    async def inner_job():
        nonlocal child_trace_id
        child_trace_id = _get_current_trace_id()

    wrapped = _new_trace(inner_job)

    # Run inside a parent span to simulate the lifespan/startup context
    with logfire.span("fake_parent"):
        parent_trace_id = _get_current_trace_id()
        await wrapped()

    print(f"\nParent trace: {parent_trace_id}")
    print(f"Child trace:  {child_trace_id}")

    assert parent_trace_id != "", "Parent should have a trace_id"
    assert child_trace_id != "", "Child should have a trace_id"
    assert child_trace_id != parent_trace_id, (
        "Wrapped job must NOT inherit parent trace_id"
    )


@pytest.mark.asyncio
async def test_simultaneous_jobs_under_parent_span_all_separate():
    """Simulate the real scenario: multiple jobs fire while lifespan span is active."""
    import logfire

    traces: dict[str, str] = {}

    async def make_job(name: str):
        async def job():
            traces[name] = _get_current_trace_id()
            await asyncio.sleep(0.01)  # simulate work
        return job

    job_a = _new_trace(await make_job("clickup"))
    job_b = _new_trace(await make_job("scheduled_check"))
    job_c = _new_trace(await make_job("zillow_email"))

    with logfire.span("fake_lifespan"):
        parent_trace = _get_current_trace_id()
        # Fire all three simultaneously
        await asyncio.gather(job_a(), job_b(), job_c())

    print(f"\nParent (lifespan): {parent_trace}")
    for name, tid in traces.items():
        print(f"  {name}: {tid}")

    all_ids = [parent_trace] + list(traces.values())
    assert len(set(all_ids)) == 4, (
        f"Expected 4 unique trace_ids (1 parent + 3 jobs), got {len(set(all_ids))}: {all_ids}"
    )
