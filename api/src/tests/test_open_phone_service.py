"""
Tests for OpenPhone service modules.

Moved verbatim from their service modules:
- ``api/src/open_phone/escalate.py`` (escalation tests)
- ``api/src/open_phone/service.py`` (contact upsert / send message tests)
- ``api/src/open_phone/routes.py`` (emoji detection test)

Live tests hit real third-party APIs and only run when explicitly requested:

    pytest -m live api/src/tests/test_open_phone_service.py -v -s
"""

from datetime import datetime

import pytest
import pytz
from dotenv import find_dotenv, load_dotenv

load_dotenv(find_dotenv(".env"), override=False)

from api.src.contact.service import ContactCreate
from api.src.open_phone.escalate import analyze_for_twilio_escalation
from api.src.open_phone.routes import contains_emoji
from api.src.open_phone.service import send_message, upsert_openphone_contact

# ---------------------------------------------------------------------------
# Escalation tests (from api/src/open_phone/escalate.py)
# ---------------------------------------------------------------------------


@pytest.mark.live
@pytest.mark.asyncio
async def test_explicit_keyword_escalation():
    """
    Test function to verify Twilio escalation functionality.
    """
    # Test data
    open_phone_event = {
        "event_id": "1234567890",
        "event_type": "message.incoming",
        "message_text": "fire in the building",
        "from_number": "+14123703505",
        "to_number": "+14129001989",
        "event_timestamp": datetime.now(pytz.timezone("US/Eastern")),
    }
    successful_escalations = await analyze_for_twilio_escalation(
        open_phone_event, escalate_to_numbers=["+14123703550"], mock=True
    )
    print(successful_escalations)
    assert successful_escalations == 1


@pytest.mark.live
@pytest.mark.asyncio
async def test_ai_escalation_positive():
    open_phone_event = {
        "event_id": "1234567890",
        "event_type": "message.incoming",
        "message_text": "There is a crazy person screaming about hurting people in the building!",
        "from_number": "+14123703505",
        "to_number": "+14129001989",
        "event_timestamp": datetime.now(pytz.timezone("US/Eastern")),
    }
    should_escalate = await analyze_for_twilio_escalation(
        open_phone_event, escalate_to_numbers=["+14123703550"], mock=True
    )
    print(should_escalate)

    assert should_escalate == 1


@pytest.mark.asyncio
async def test_ai_escalation_negative():
    open_phone_event = {
        "event_id": "1234567890",
        "event_type": "message.incoming",
        "message_text": "I lost my keys and can't get in! Can someone bring me a spare ASAP??",
        "from_number": "+14123703505",
        "to_number": "+14129001989",
        "event_timestamp": datetime.now(pytz.timezone("US/Eastern")),
    }
    should_escalate = await analyze_for_twilio_escalation(
        open_phone_event, escalate_to_numbers=["+14123703550"], mock=True
    )
    print(should_escalate)

    assert should_escalate == 0


# ---------------------------------------------------------------------------
# Contact / messaging tests (from api/src/open_phone/service.py)
# ---------------------------------------------------------------------------


@pytest.mark.live
@pytest.mark.asyncio
async def test_upsert_openphone_contact():
    contact_create = ContactCreate(
        slug="test-lead-contact-random",
        phone_number="+19291231234",
        first_name="Test First",
        last_name="Test Last",
        email="test@test.com",
        notes="API-Test",
        company="Test",
        role="Test",
    )

    response = await upsert_openphone_contact(contact_create)
    print(response.json())
    assert response.status_code == 201 or response.status_code == 200


@pytest.mark.live
@pytest.mark.asyncio
async def test_send_message():
    response = await send_message(
        message="Hello, this is a test message",
        to_phone_number="+14123703550",
        from_phone_number="+14129101500",
    )
    print(response.json())
    assert response.status_code == 202


# ---------------------------------------------------------------------------
# Emoji detection test (from api/src/open_phone/routes.py)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_contains_emoji():
    assert await contains_emoji("👍")
    assert await contains_emoji("👍🏻")
    assert await contains_emoji("blah blah blah 👍")
    assert await contains_emoji("👍🏻 blah blah blah")
    assert await contains_emoji("👍👍👍👍👍")
    assert not await contains_emoji("Hello")
    assert not await contains_emoji("Hello José")
