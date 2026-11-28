"""Each scheduled job run in its own Logfire trace, under one span.

Decorate a job's function where it is defined::

    @scheduled_job
    async def process_zillow_email_queue() -> None: ...

Why where it is defined, and not when the job is registered: the production
job store (``SQLAlchemyJobStore``) saves a job's function as an import path
(``module:name``) and imports it again for every run. A wrapper made at
registration time has the same path as the function it wraps (``functools.wraps``
copies it), so the job store brought back the bare function and production runs
had no span: each of a run's queries landed in Logfire as its own one-line
trace. A decorated function is what its module holds under that name, so the
path brings back the traced one.

The path itself must not change for a job that is already stored. While a
deploy runs, the old server shares the job store, and APScheduler deletes a
stored job whose function it cannot import. Decorating keeps the path.

This module imports only Logfire and OpenTelemetry, so a job's module can
import it at the top without pulling in the scheduler.
"""

import functools
from collections.abc import Awaitable, Callable
from typing import ParamSpec, TypeVar

import logfire
from opentelemetry import context as otel_context

P = ParamSpec("P")
R = TypeVar("R")

# Set on a function ``scheduled_job`` returns, so it is never wrapped twice.
TRACED = "__scheduled_job_traced__"


def job_name(func: Callable) -> str:
    """``zillow_leads.queue:process_zillow_email_queue``: the import path, short."""
    module = getattr(func, "__module__", "") or ""
    qualname = getattr(func, "__qualname__", None) or repr(func)
    return f"{module.removeprefix('api.src.')}:{qualname}"


def scheduled_job(func: Callable[P, Awaitable[R]]) -> Callable[P, Awaitable[R]]:
    """Run ``func`` in a new trace, under a span named for it.

    Without this a job run inherits the trace it was started from: the API's
    startup trace, which lasts for hours, so runs could not be found apart.
    """
    if getattr(func, TRACED, False):
        return func
    name = job_name(func)

    @functools.wraps(func)
    async def traced(*args: P.args, **kwargs: P.kwargs) -> R:
        token = otel_context.attach(otel_context.Context())
        try:
            with logfire.span("apscheduler job: {job_name}", job_name=name):
                return await func(*args, **kwargs)
        finally:
            otel_context.detach(token)

    setattr(traced, TRACED, True)
    return traced


def is_traced(func: object) -> bool:
    return bool(getattr(func, TRACED, False))
