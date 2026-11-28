"""A showing agent's reply on the AI line.

Showing agents are texted from the AI line, so their replies reach the AI SMS
trigger. One whose mobile Quo lists as internal is answered by Sernia AI like
any staff member. One Quo does not list as internal is not answered, and the
team is pushed instead of the reply going unseen.

SMS SAFETY: nothing here sends a text. The push is a stand-in.
"""

from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from sqlalchemy import update

from api.src.database.database import AsyncSessionFactory
from api.src.sernia_ai.triggers import ai_sms_event_trigger as trigger
from api.src.showing_bookings.models import ShowingAgentRecord
from api.src.tests.showing_helpers import add_agent, clear_showing_data

AGENT_MOBILE = "+14125550101"  # add_agent's mobile
STRANGER = "+14125550177"
PUSH = "api.src.agent_conversations.push.service.notify_all_sernia_users"


@pytest_asyncio.fixture
async def agent(monkeypatch):
    monkeypatch.setenv("RAILWAY_ENVIRONMENT_NAME", "production")
    await clear_showing_data()
    agent_id = await add_agent()
    yield agent_id
    await clear_showing_data()


def event(from_number: str) -> dict:
    return {"from_number": from_number, "message_text": "Running 10 min late", "event_id": "EV1"}


async def reply(from_number: str, *, internal: dict | None = None) -> AsyncMock:
    """Run the trigger for one reply, with Quo's contact lookup and the push stood in."""
    push = AsyncMock()
    with (
        patch.object(trigger, "is_sernia_ai_enabled", AsyncMock(return_value=True)),
        patch.object(trigger, "_verify_internal_contact", AsyncMock(return_value=internal)),
        patch.object(trigger, "_is_ai_sms_rate_limited", return_value=False),
        patch(PUSH, push),
    ):
        await trigger._handle_ai_sms_event(event(from_number))
    return push


@pytest.mark.asyncio
async def test_an_agent_quo_does_not_list_as_internal_alerts_the_team(agent):
    push = await reply(AGENT_MOBILE)

    push.assert_awaited_once()
    kwargs = push.await_args.kwargs
    assert kwargs["title"] == trigger.SHOWING_AGENT_ALERT_TITLE
    assert kwargs["data"] == {"url": "/team/leads/bookings", "type": "showing_alert"}
    assert AGENT_MOBILE not in kwargs["body"]


@pytest.mark.asyncio
async def test_a_stranger_or_an_inactive_agent_is_ignored_as_before(agent):
    assert not (await reply(STRANGER)).await_count
    async with AsyncSessionFactory() as session:
        await session.execute(update(ShowingAgentRecord).values(active=False))
        await session.commit()
    assert not (await reply(AGENT_MOBILE)).await_count


@pytest.mark.asyncio
async def test_outside_production_nobody_is_pushed(agent, monkeypatch):
    monkeypatch.setenv("RAILWAY_ENVIRONMENT_NAME", "development")
    assert not (await reply(AGENT_MOBILE)).await_count


@pytest.mark.asyncio
async def test_a_failed_push_never_breaks_the_trigger(agent):
    with patch(PUSH, AsyncMock(side_effect=RuntimeError("push service down"))):
        await trigger._alert_if_showing_agent(AGENT_MOBILE)


@pytest.mark.asyncio
async def test_an_internal_agent_goes_on_to_sernia_ai_and_is_not_alerted(agent):
    """The gate lets them through; the conversation itself is stood in here."""
    contact = {"defaultFields": {"firstName": "Jane", "lastName": "Doe"}}
    with (
        patch.object(trigger, "_handle_group_sms", AsyncMock()),
        patch(
            "api.src.open_phone.service.get_ai_phone_number",
            AsyncMock(return_value="+14125559999"),
        ),
        patch.object(trigger, "get_conversation_messages", AsyncMock(return_value=[])),
        patch.object(trigger, "_fetch_sms_thread", AsyncMock(return_value=[])),
        patch.object(trigger, "run_and_finalize", AsyncMock(return_value=None)) as run,
    ):
        push = await reply(AGENT_MOBILE, internal=contact)
    assert not push.await_count
    run.assert_awaited_once()
    assert run.await_args.kwargs["metadata"]["trigger_contact_name"] == "Jane Doe"
