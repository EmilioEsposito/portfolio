"""
Tests for the VercelAIAdapter-based chat endpoint.
"""
import json
import uuid
import pytest
from fastapi.testclient import TestClient
from api.index import app


@pytest.fixture
def client():
    """Test client fixture"""
    return TestClient(app)


def test_chat_emilio_endpoint_streaming_format(client):
    """
    Test that the /api/ai/chat-emilio endpoint returns properly formatted SSE stream.
    
    Verifies:
    - Response status is 200
    - Content-Type is text/event-stream
    - x-vercel-ai-ui-message-stream header is present
    - Stream contains expected SSE events (start, text-start, text-delta, text-end, finish, [DONE])
    """
    # Prepare request body matching Vercel AI SDK UIMessage format
    # VercelAIAdapter expects UIMessage format with id, role, and parts array
    request_body = {
        "trigger": "submit-message",
        "id": str(uuid.uuid4()),
        "messages": [
            {
                "id": str(uuid.uuid4()),
                "role": "user",
                "parts": [
                    {
                        "type": "text",
                        "text": "What technologies does Emilio work with?",
                    }
                ],
            }
        ]
    }
    
    # Make streaming request
    with client.stream(
        "POST",
        "/api/ai/chat-emilio",
        json=request_body,
        headers={"Accept": "text/event-stream"},
    ) as response:
        # Verify response status
        if response.status_code != 200:
            # Read error response for debugging
            error_lines = list(response.iter_lines())
            error_body = "\n".join(error_lines)
            print(f"\nError response (status {response.status_code}): {error_body}")
            print(f"Response headers: {dict(response.headers)}")
            pytest.fail(f"Expected 200, got {response.status_code}: {error_body}")
        
        assert response.status_code == 200
        
        # Verify content type
        assert response.headers["content-type"] == "text/event-stream; charset=utf-8"
        
        # Verify Vercel AI header
        assert response.headers.get("x-vercel-ai-ui-message-stream") == "v1"
        
        # Collect all SSE events and print raw streaming output
        print("\n" + "="*80)
        print("RAW STREAMING OUTPUT:")
        print("="*80)
        events = []
        for line in response.iter_lines():
            if line:
                events.append(line)
                # Print each line on a newline
                print(line)
        print("="*80 + "\n")
        
        # Verify we got events
        assert len(events) > 0, "No events received in stream"
        
        # Parse SSE events (format: "data: {...}" or "data: [DONE]")
        parsed_events = []
        for event_line in events:
            if event_line.startswith("data: "):
                data_content = event_line[6:]  # Remove "data: " prefix
                if data_content == "[DONE]":
                    parsed_events.append({"type": "done"})
                else:
                    try:
                        parsed_events.append(json.loads(data_content))
                    except json.JSONDecodeError:
                        # Skip malformed JSON
                        continue
        
        # Verify we have parsed events
        assert len(parsed_events) > 0, "No valid events parsed from stream"
        
        # Verify event sequence
        event_types = [event.get("type") for event in parsed_events if isinstance(event, dict)]
        
        # Should have start event
        assert "start" in event_types, f"Missing 'start' event. Got: {event_types}"
        
        # Should have text-start event
        assert "text-start" in event_types, f"Missing 'text-start' event. Got: {event_types}"
        
        # Should have text-delta events (at least one)
        assert "text-delta" in event_types, f"Missing 'text-delta' event. Got: {event_types}"
        
        # Should have text-end event
        assert "text-end" in event_types, f"Missing 'text-end' event. Got: {event_types}"
        
        # Should have finish event
        assert "finish" in event_types, f"Missing 'finish' event. Got: {event_types}"
        
        # Should end with [DONE]
        assert parsed_events[-1].get("type") == "done", "Stream should end with [DONE]"
        
        # Verify start event exists (messageId is optional, VercelAIAdapter may not include it)
        start_event = next((e for e in parsed_events if e.get("type") == "start"), None)
        assert start_event is not None, "Start event not found"
        # messageId may or may not be present depending on VercelAIAdapter version
        
        # Verify text events have consistent id
        text_start = next((e for e in parsed_events if e.get("type") == "text-start"), None)
        text_end = next((e for e in parsed_events if e.get("type") == "text-end"), None)
        
        if text_start and text_end:
            assert "id" in text_start, "text-start missing id"
            assert "id" in text_end, "text-end missing id"
            assert text_start["id"] == text_end["id"], "text-start and text-end should have same id"
        
        # Verify text-delta events have delta field
        text_deltas = [e for e in parsed_events if e.get("type") == "text-delta"]
        for delta_event in text_deltas:
            assert "delta" in delta_event, "text-delta event missing delta field"
            assert "id" in delta_event, "text-delta event missing id field"
        
        print(f"\n✓ Received {len(parsed_events)} events")
        # print(f"✓ Event types: {event_types}")
        print(f"✓ Stream format is correct")


def test_chat_emilio_endpoint_empty_messages(client):
    """Test endpoint handles empty messages gracefully"""
    request_body = {
        "trigger": "submit-message",
        "id": str(uuid.uuid4()),
        "messages": []
    }
    
    with client.stream(
        "POST",
        "/api/ai/chat-emilio",
        json=request_body,
        headers={"Accept": "text/event-stream"},
    ) as response:
        # Should still return 200, but may have error event
        assert response.status_code == 200
        
        events = []
        for line in response.iter_lines():
            if line:
                events.append(line)
        
        # Should have some response (even if error)
        assert len(events) > 0


def test_chat_emilio_endpoint_multiple_messages(client):
    """Test endpoint handles conversation history"""
    request_body = {
        "trigger": "submit-message",
        "id": str(uuid.uuid4()),
        "messages": [
            {
                "id": str(uuid.uuid4()),
                "role": "user",
                "parts": [{"type": "text", "text": "Hello"}],
            },
            {
                "id": str(uuid.uuid4()),
                "role": "assistant",
                "parts": [{"type": "text", "text": "Hi there!"}],
            },
            {
                "id": str(uuid.uuid4()),
                "role": "user",
                "parts": [{"type": "text", "text": "What's your name?"}],
            },
        ]
    }
    
    with client.stream(
        "POST",
        "/api/ai/chat-emilio",
        json=request_body,
        headers={"Accept": "text/event-stream"},
    ) as response:
        assert response.status_code == 200
        assert response.headers["content-type"] == "text/event-stream; charset=utf-8"
        
        # Should receive valid stream
        events = []
        for line in response.iter_lines():
            if line and line.startswith("data: "):
                events.append(line)
        
        assert len(events) > 0

