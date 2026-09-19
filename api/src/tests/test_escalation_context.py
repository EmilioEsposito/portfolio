from datetime import UTC, datetime

import httpx
import pytest

from api.src.open_phone import escalation_context as context


def incoming() -> dict:
    return {
        "phone_number_id": "PN1",
        "conversation_id": "CN1",
        "from_number": "+15550000001",
        "to_number": "+15550000002,+15550000003",
        "event_timestamp": datetime(2026, 8, 9, 5, 2, 3, tzinfo=UTC),
        "event_data": {"data": {"object": {"id": "current", "createdAt": "2026-08-09T05:02:02Z"}}},
    }


def message(id: str, **changes) -> dict:
    return {
        "id": id,
        "conversationId": "CN1",
        "createdAt": "2026-08-09T00:00:00Z",
        "updatedAt": "2026-08-09T00:00:01Z",
        "text": "ceiling is cracked",
        "direction": "incoming",
        "from": "+15550000001",
        **changes,
    }


def mock_api(monkeypatch, messages: list, *, error: bool = False) -> list:
    requests = []
    original = httpx.AsyncClient

    def handler(request):
        requests.append(request)
        if error:
            return httpx.Response(503)
        if request.url.path == "/v1/phone-numbers/PN1":
            return httpx.Response(200, json={"data": {"number": "+15550000002"}})
        assert request.url.params.get_list("participants") == ["+15550000001", "+15550000003"]
        assert request.url.params["createdBefore"] == "2026-08-09T05:02:02+00:00"
        return httpx.Response(200, json={"data": messages})

    monkeypatch.setenv("OPEN_PHONE_API_KEY", "test")
    monkeypatch.setattr(
        context.httpx,
        "AsyncClient",
        lambda **kw: original(transport=httpx.MockTransport(handler), **kw),
    )
    return requests


def test_actual_eastern_conversion():
    assert (
        context.event_time("2026-08-09T05:02:03.617Z").isoformat()
        == "2026-08-09T01:02:03.617000-04:00"
    )
    assert context.event_time("2026-01-09T05:02:03Z").hour == 0


@pytest.mark.asyncio
async def test_history_excludes_current_future_edits_media_and_duplicates(monkeypatch):
    mock_api(
        monkeypatch,
        [
            message("past"),
            message("past"),
            message("current"),
            message("future", createdAt="2026-08-09T05:03:00Z"),
            message("edited", updatedAt="2026-08-10T00:00:00Z"),
            message("photo", text="", media=[{"url": "unused"}]),
        ],
    )
    result = await context.fetch_escalation_history(incoming())
    assert result.status == "partial"
    assert len(result.messages) == 1
    assert result.messages[0].text == "ceiling is cracked"
    assert result.messages[0].same_sender is True


@pytest.mark.asyncio
async def test_mismatched_thread_rejected(monkeypatch):
    mock_api(monkeypatch, [message("wrong", conversationId="CN2")])
    result = await context.fetch_escalation_history(incoming())
    assert result.status == "thread_mismatch"
    assert not result.messages


@pytest.mark.asyncio
async def test_api_failure_falls_back(monkeypatch):
    mock_api(monkeypatch, [], error=True)
    result = await context.fetch_escalation_history(incoming())
    assert result.status == "unavailable"
    assert not result.messages


@pytest.mark.asyncio
async def test_missing_identity_does_not_fetch(monkeypatch):
    requests = mock_api(monkeypatch, [])
    assert (await context.fetch_escalation_history({})).status == "missing_thread_identity"
    assert requests == []


@pytest.mark.asyncio
async def test_history_bounded_and_chronological(monkeypatch):
    rows = [message(str(i), createdAt=f"2026-08-09T00:{i:02d}:00Z") for i in range(30)]
    mock_api(monkeypatch, list(reversed(rows)))
    result = await context.fetch_escalation_history(incoming())
    assert result.status == "partial"
    assert len(result.messages) == 20
    assert result.messages[0].timestamp.endswith("20:10:00-04:00")
    assert result.messages[-1].timestamp.endswith("20:29:00-04:00")


@pytest.mark.asyncio
async def test_history_timeout_is_bounded(monkeypatch):
    import asyncio

    original = httpx.AsyncClient

    async def handler(request):
        await asyncio.sleep(0.1)
        return httpx.Response(200)

    monkeypatch.setenv("OPEN_PHONE_API_KEY", "test")
    monkeypatch.setattr(context, "HISTORY_TIMEOUT", 0.01)
    monkeypatch.setattr(
        context.httpx,
        "AsyncClient",
        lambda **kw: original(transport=httpx.MockTransport(handler), **kw),
    )
    assert (await context.fetch_escalation_history(incoming())).status == "unavailable"


@pytest.mark.parametrize(
    "seconds,within", [(0, True), (1740, True), (1800, True), (1800.001, False), (1860, False)]
)
def test_precomputed_timing_boundary(seconds, within):
    from datetime import timedelta

    current = datetime(2026, 9, 18, 5, tzinfo=UTC)
    state = {
        "timestamp": current.isoformat(),
        "prior_messages": [
            {
                "timestamp": (current - timedelta(seconds=seconds)).isoformat(),
                "direction": "outgoing",
                "text": "Acknowledged",
            }
        ],
    }
    result = context.add_history_timing(state)
    message = result["prior_messages"][0]
    assert message["seconds_before_current_message"] == seconds
    assert message["within_previous_30_minutes"] is within
    assert result["history_timing"]["outbound_message_within_previous_30_minutes"] is within
    assert "within_previous_30_minutes" not in state["prior_messages"][0]


def test_precomputed_timing_dst_and_last_outbound_not_last_message():
    state = {
        "timestamp": "2026-11-01T01:10:00-05:00",
        "prior_messages": [
            {"timestamp": "2026-11-01T01:35:00-04:00", "direction": "outgoing"},
            {"timestamp": "2026-11-01T01:09:00-05:00", "direction": "incoming"},
        ],
    }
    result = context.add_history_timing(state)
    timing = result["history_timing"]
    assert timing["seconds_since_last_message_in_supplied_history"] == 60
    assert timing["seconds_since_last_outbound_in_supplied_history"] == 2100
    assert timing["outbound_message_within_previous_30_minutes"] is False


def test_precomputed_missing_history_is_unknown():
    result = context.add_history_timing({"timestamp": "2026-09-18T01:00:00-04:00"})
    assert result["history_timing"]["seconds_since_last_outbound_in_supplied_history"] is None
    assert result["history_timing"]["seconds_since_last_message_in_supplied_history"] is None


def test_precomputed_timing_rejects_future():
    with pytest.raises(ValueError, match="future"):
        context.add_history_timing(
            {
                "timestamp": "2026-09-18T01:00:00-04:00",
                "prior_messages": [
                    {"timestamp": "2026-09-18T01:00:01-04:00", "direction": "outgoing"}
                ],
            }
        )
