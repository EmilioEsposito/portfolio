"""What each scheduled job is for, in the words the Scheduler page shows.

Each job's ``JobInfo`` is written beside its registration and passed to
``upsert_job(..., info=...)``. ``upsert_job`` records it here under the job's id
and uses ``info.name`` as the APScheduler job name. ``GET /api/schedulers/get_jobs``
reads it back. A job added without one (a one-off text or email the agent
schedules) still lists, under its APScheduler name, with no description.

The registry lives in this process. The API runs one process, which both
registers the jobs at startup and answers the page.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class JobInfo:
    """A scheduled job described for a person, not a programmer.

    name: what a person would call it ("Zillow lead emails: process the queue").
    summary: one sentence, shown on the collapsed card.
    description: what each run does, who or what it touches, how often and why,
        and what would stop if the job were paused or removed. Plain words; no
        person's name, number or email.
    """

    name: str
    summary: str
    description: str


_BY_JOB_ID: dict[str, JobInfo] = {}


def remember(job_id: str, info: JobInfo) -> None:
    """Record ``info`` for ``job_id``. Registering again replaces it."""
    _BY_JOB_ID[job_id] = info


def info_for(job_id: str) -> JobInfo | None:
    """The description registered for ``job_id``, if any."""
    return _BY_JOB_ID.get(job_id)
