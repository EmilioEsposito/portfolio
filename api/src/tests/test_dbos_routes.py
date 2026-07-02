"""
Tests for the DBOS service routes.

Moved verbatim from ``api/src/dbos_service/routes.py``.

Note: DBOS is currently disabled in this repo ($75/month DB keep-alive
costs) — see ``api/src/schedulers/README.md`` for details and re-enabling
instructions.
"""

from pprint import pprint

import pytest

# get_jobs reads the DBOS workflow registry, which is populated as an import
# side effect. Under the old *.py pytest collection every module (including
# the example workflows) was imported implicitly; import them explicitly now.
from api.src.dbos_service.examples import hello_dbos  # noqa: F401
from api.src.dbos_service.routes import get_jobs


@pytest.mark.asyncio
async def test_get_jobs():
    jobs = await get_jobs()
    pprint(jobs)
    assert len(jobs) > 0
