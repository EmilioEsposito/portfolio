"""
Live integration test for the ClickUp service module.

Moved verbatim from ``api/src/clickup/service.py``. Marked ``live`` so it
only runs when explicitly requested:

    pytest -m live api/src/tests/test_clickup_service.py -v -s
"""

import pytest
from dotenv import find_dotenv, load_dotenv

load_dotenv(find_dotenv(".env"), override=False)

from api.src.clickup.service import get_peppino_view_tasks


@pytest.mark.live
@pytest.mark.asyncio
async def test_get_peppino_view_tasks():
    await get_peppino_view_tasks()
