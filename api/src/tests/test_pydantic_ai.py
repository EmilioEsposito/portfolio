from __future__ import annotations

import json
import os

import pytest


@pytest.mark.parametrize(
    "idea",
    [
        "I want an AI accountability coach for indie founders",
        "Build a journaling companion that keeps remote teams aligned",
    ],
)
def test_portfolio_route_streams_response(client, idea: str) -> None:
    os.environ.pop("OPENAI_API_KEY", None)

    payload = {
        "id": "req-test",
        "trigger": "submit-message",
        "messages": [
            {
                "id": "msg-1",
                "role": "user",
                "parts": [
                    {"type": "text", "text": idea},
                ],
            }
        ],
    }

    with client.stream(
        "POST",
        "/api/pydantic-ai/portfolio",
        json=payload,
        headers={"accept": "text/event-stream"},
    ) as response:
        chunks = [segment for segment in response.iter_text() if segment]

    assert response.status_code == 200

    events: list[dict] = []
    transcript_parts: list[str] = []
    for chunk in chunks:
        for line in chunk.splitlines():
            if not line.startswith("data: "):
                continue
            data = line.removeprefix("data: ")
            if data == "[DONE]":
                continue
            parsed = json.loads(data)
            events.append(parsed)
            if parsed.get("type") == "text-delta":
                transcript_parts.append(parsed.get("delta", ""))

    transcript = "".join(transcript_parts)

    assert any(event.get("type") == "start" for event in events)
    assert "Key experience pillars" in transcript
    assert chunks[-1].strip().endswith("data: [DONE]")
