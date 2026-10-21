"""Exercise the real SDK JSON-schema parsing without network or downstream actions."""

import json
from datetime import datetime
from typing import Any
from unittest.mock import patch

import httpx
import pytest
from openai import OpenAI
from pydantic import ValidationError

from api.src.zillow_email import service


@pytest.fixture
def messages() -> list[service.EmailMessageDetail]:
    return [
        service.EmailMessageDetail(
            subject="332 Example Street Apt 01",
            email_timestamp_et=datetime(2026, 10, 20, 9),
            email_day_of_week_et_str="Tuesday",
            body_html="<p>My name is Alice Garcia. Can I view this apartment?</p>",
            from_address="alice@example.com",
            to_address="all@serniacapital.com",
        )
    ]


def sdk_client(payload: dict[str, Any], schema_name: str) -> OpenAI:
    def respond(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["model"] == "openai/gpt-6-luna"
        response_format = body["response_format"]
        assert response_format["type"] == "json_schema"
        assert response_format["json_schema"]["name"] == schema_name
        assert response_format["json_schema"]["strict"] is True
        assert body["messages"][0]["role"] == "system"
        assert "Alice Garcia" in body["messages"][1]["content"]
        return httpx.Response(
            200,
            json={
                "id": "synthetic",
                "object": "chat.completion",
                "created": 0,
                "model": body["model"],
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": json.dumps(payload)},
                    }
                ],
            },
        )

    return OpenAI(
        api_key="unused",
        base_url="https://openrouter.invalid/api/v1",
        http_client=httpx.Client(transport=httpx.MockTransport(respond)),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("reply, scheduled", [(True, False), (False, True), (False, False)])
async def test_assessment_schema_and_return_contract(
    messages: list[service.EmailMessageDetail], reply: bool, scheduled: bool
) -> None:
    payload = dict(
        should_reply=reply, reason="We should follow this policy.", appointment_scheduled=scheduled
    )
    with sdk_client(payload, "ShouldReply") as client:
        with patch.object(service, "openrouter_client", return_value=client):
            assert await service.ai_assess_thread("synthetic", messages) == (
                reply,
                payload["reason"],
                scheduled,
            )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {
            "building_number": 332,
            "unit_number": 1,
            "lead_first_name": "Alice",
            "lead_last_name": "Garcia",
            "lead_phone_number": "+14125550123",
            "appointment_date": "2026-10-23",
            "appointment_time": "2:00 PM",
        },
        dict.fromkeys(service.CollectedThreadInfo.model_fields),
    ],
)
async def test_collection_schema_and_return_contract(
    messages: list[service.EmailMessageDetail], payload: dict[str, Any]
) -> None:
    with sdk_client(payload, "CollectedThreadInfo") as client:
        with patch.object(service, "openrouter_client", return_value=client):
            info, thread = await service.ai_collect_thread_info("synthetic", messages)
    assert isinstance(info, service.CollectedThreadInfo)
    assert info.model_dump() == payload
    assert "Alice Garcia" in thread
    assert "2026-10-20 09:00:00" in thread


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "task, schema_name, payload",
    [
        (service.ai_assess_thread, "ShouldReply", {"should_reply": True}),
        (service.ai_collect_thread_info, "CollectedThreadInfo", {"building_number": "unknown"}),
    ],
)
async def test_invalid_response_is_rejected(
    messages: list[service.EmailMessageDetail], task: Any, schema_name: str, payload: dict[str, Any]
) -> None:
    with sdk_client(payload, schema_name) as client:
        with patch.object(service, "openrouter_client", return_value=client):
            with pytest.raises(ValidationError):
                await task("synthetic", messages)
