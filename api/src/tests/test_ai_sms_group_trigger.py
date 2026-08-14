"""
Unit tests for group-SMS handling in the AI SMS event trigger.

A message.received on the AI line whose ``to`` includes recipients besides
the AI's own number is a GROUP text: the trigger keys the conversation to
the Quo group conversation (``ai_sms_group_{CN...}``), reconstructs history
from the local ``open_phone_events`` table with sender-name prefixes, and
replies to ALL human participants so the answer lands in the group thread.

⚠️  SMS SAFETY: All SMS tests mock the send call. NEVER send real SMS from
tests. See CLAUDE.md.
"""

from unittest.mock import AsyncMock, patch

import pytest
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart

from api.src.sernia_ai.triggers.ai_sms_event_trigger import (
    _extract_group_participants,
    _group_activities_to_model_messages,
    _group_prompt,
    _send_sms_reply,
    _split_to_numbers,
)

AI_PHONE = "+14125559999"
EMILIO = "+14123703550"
SANA = "+14128770257"


# ===========================================================================
# _split_to_numbers
# ===========================================================================


class TestSplitToNumbers:
    def test_single_string(self):
        assert _split_to_numbers(AI_PHONE) == [AI_PHONE]

    def test_comma_joined_group(self):
        assert _split_to_numbers(f"{AI_PHONE},{SANA}") == [AI_PHONE, SANA]

    def test_comma_joined_with_spaces(self):
        assert _split_to_numbers(f"{AI_PHONE}, {SANA}") == [AI_PHONE, SANA]

    def test_list_passthrough(self):
        assert _split_to_numbers([AI_PHONE, SANA]) == [AI_PHONE, SANA]

    def test_empty_values(self):
        assert _split_to_numbers(None) == []
        assert _split_to_numbers("") == []
        assert _split_to_numbers([]) == []


# ===========================================================================
# _extract_group_participants
# ===========================================================================


class TestExtractGroupParticipants:
    def test_one_to_one_message_yields_no_participants(self):
        """A plain 1:1 SMS to the AI line: to == AI phone only."""
        event = {"from_number": EMILIO, "to_number": AI_PHONE}
        assert _extract_group_participants(event, AI_PHONE) == []

    def test_group_message_yields_other_humans(self):
        """Group SMS from Emilio to [AI, Sana]: Sana is the other human."""
        event = {"from_number": EMILIO, "to_number": f"{AI_PHONE},{SANA}"}
        assert _extract_group_participants(event, AI_PHONE) == [SANA]

    def test_unknown_ai_phone_falls_back_to_1to1(self):
        """When the AI's own number can't be resolved, group detection is
        unsafe (the AI's own to-entry would look like a participant) — the
        trigger must fall back to 1:1 handling."""
        event = {"from_number": EMILIO, "to_number": f"{AI_PHONE},{SANA}"}
        assert _extract_group_participants(event, None) == []

    def test_sender_excluded_from_participants(self):
        event = {"from_number": EMILIO, "to_number": f"{AI_PHONE},{EMILIO},{SANA}"}
        assert _extract_group_participants(event, AI_PHONE) == [SANA]


# ===========================================================================
# _group_activities_to_model_messages
# ===========================================================================


def _activity(
    kind: str,
    msg_id: str,
    sender: str,
    text: str | None,
    created: str = "2026-08-14T12:00:00+00:00",
) -> dict:
    return {
        "_kind": kind,
        "id": msg_id,
        "createdAt": created,
        "text": text,
        "from": sender,
        "to": [],
        "direction": "outgoing" if sender == AI_PHONE else "incoming",
    }


class TestGroupActivitiesToModelMessages:
    NAMES = {EMILIO: "Emilio Esposito", SANA: "Sana Esposito"}

    def test_incoming_prefixed_with_sender_name(self):
        msgs = _group_activities_to_model_messages(
            [_activity("message", "AC1", EMILIO, "Red skies")],
            AI_PHONE,
            self.NAMES,
        )
        assert len(msgs) == 1
        assert isinstance(msgs[0], ModelRequest)
        part = msgs[0].parts[0]
        assert isinstance(part, UserPromptPart)
        assert part.content == _group_prompt("Emilio Esposito", "Red skies")
        assert part.timestamp is not None

    def test_ai_outgoing_becomes_assistant_turn(self):
        msgs = _group_activities_to_model_messages(
            [_activity("message", "AC2", AI_PHONE, "Sailors' delight!")],
            AI_PHONE,
            self.NAMES,
        )
        assert len(msgs) == 1
        assert isinstance(msgs[0], ModelResponse)
        assert isinstance(msgs[0].parts[0], TextPart)
        assert msgs[0].parts[0].content == "Sailors' delight!"

    def test_current_message_excluded(self):
        """The webhook saves the current message to the events table before
        the background task runs — it must be dropped from history (it's the
        run's prompt instead)."""
        msgs = _group_activities_to_model_messages(
            [
                _activity("message", "AC1", EMILIO, "earlier"),
                _activity("message", "ACcurrent", SANA, "the live message"),
            ],
            AI_PHONE,
            self.NAMES,
            exclude_message_id="ACcurrent",
        )
        assert len(msgs) == 1
        assert "earlier" in msgs[0].parts[0].content

    def test_calls_and_empty_texts_skipped(self):
        msgs = _group_activities_to_model_messages(
            [
                _activity("call", "AC3", EMILIO, None),
                _activity("message", "AC4", EMILIO, "   "),
                _activity("message", "AC5", SANA, "real message"),
            ],
            AI_PHONE,
            self.NAMES,
        )
        assert len(msgs) == 1
        assert "real message" in msgs[0].parts[0].content

    def test_unknown_sender_falls_back_to_phone(self):
        msgs = _group_activities_to_model_messages(
            [_activity("message", "AC6", "+19995550000", "hi")],
            AI_PHONE,
            self.NAMES,
        )
        assert msgs[0].parts[0].content == "[+19995550000]: hi"


# ===========================================================================
# System-hint dedup normalization
# ===========================================================================


class TestSystemHintDedup:
    """Persisted prompts carry appended ``[System: ...]`` hints that the
    same message reconstructed from Quo / the events table doesn't have.
    Dedup must strip them, or every hinted turn is re-prepended as
    "missing" on the next run and appears twice."""

    def test_dedup_key_strips_system_hint(self):
        from api.src.sernia_ai.triggers.ai_sms_event_trigger import _dedup_key

        hinted = "[Emilio Esposito]: Red skies [System: This is a GROUP SMS thread with ...]"
        assert _dedup_key(hinted) == "[Emilio Esposito]: Red skies"

    def test_dedup_key_noop_without_hint(self):
        from api.src.sernia_ai.triggers.ai_sms_event_trigger import _dedup_key

        assert _dedup_key("plain message") == "plain message"

    def test_merge_dedups_hinted_db_prompt_against_reconstructed_turn(self):
        """A DB prompt saved WITH a group hint must dedup against the same
        message reconstructed WITHOUT the hint."""
        from api.src.sernia_ai.triggers.ai_sms_event_trigger import (
            _merge_sms_into_history,
        )

        db_history = [
            ModelRequest(
                parts=[
                    UserPromptPart(
                        content="[Emilio Esposito]: Red skies [System: This is a "
                        "GROUP SMS thread with Emilio, Sana. Your reply will be "
                        "sent to the WHOLE group.]"
                    )
                ]
            ),
            ModelResponse(parts=[TextPart(content="Sailors' delight!")]),
        ]
        reconstructed = [
            ModelRequest(parts=[UserPromptPart(content="[Emilio Esposito]: Red skies")]),
            ModelResponse(parts=[TextPart(content="Sailors' delight!")]),
        ]
        merged = _merge_sms_into_history(db_history, reconstructed)
        assert merged == db_history, "hinted DB turn must absorb the reconstructed turn"


# ===========================================================================
# _send_sms_reply — group recipients
# ===========================================================================


class TestSendSmsReplyGroup:
    @pytest.mark.asyncio
    async def test_group_reply_sends_to_all_participants(self):
        with patch(
            "api.src.sernia_ai.triggers.ai_sms_event_trigger.send_message", new=AsyncMock()
        ) as send:
            await _send_sms_reply([EMILIO, SANA], "group answer")
        assert send.await_count == 1
        kwargs = send.await_args.kwargs
        assert kwargs["to_phone_number"] == [EMILIO, SANA]

    @pytest.mark.asyncio
    async def test_single_reply_unchanged(self):
        with patch(
            "api.src.sernia_ai.triggers.ai_sms_event_trigger.send_message", new=AsyncMock()
        ) as send:
            await _send_sms_reply(EMILIO, "solo answer")
        assert send.await_count == 1
        assert send.await_args.kwargs["to_phone_number"] == EMILIO


# ===========================================================================
# get_ai_phone_number — response parsing + caching
# ===========================================================================


class TestGetAiPhoneNumber:
    """Regression: the API returns the E.164 under ``data.number`` — the old
    code read ``data.phoneNumber`` (which doesn't exist), silently returned
    None on every call, and group detection fell back to 1:1 in production
    (2026-08-14). Pin the real response shape and the process-level cache."""

    # Trimmed copy of the real GET /v1/phone-numbers/{id} response.
    REAL_RESPONSE = {
        "data": {
            "id": "PNWvNqsFFy",
            "formattedNumber": "(412) 910-1500",
            "name": "Sernia AI Intern",
            "number": "+14129101500",
            "symbol": "🤖",
        }
    }

    @pytest.fixture(autouse=True)
    def _reset_cache(self, monkeypatch):
        from api.src.open_phone import service as op_service

        monkeypatch.setattr(op_service, "_ai_phone_number", None)

    def _patch_httpx(self, monkeypatch, payload: dict, calls: list):
        import httpx

        from api.src.open_phone import service as op_service

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(str(request.url))
            return httpx.Response(200, json=payload)

        real_async_client = httpx.AsyncClient

        def fake_async_client(**kwargs):
            kwargs["transport"] = httpx.MockTransport(handler)
            return real_async_client(**kwargs)

        monkeypatch.setattr(op_service.httpx, "AsyncClient", fake_async_client)

    @pytest.mark.asyncio
    async def test_parses_number_field_from_real_shape(self, monkeypatch):
        from api.src.open_phone.service import get_ai_phone_number

        calls: list = []
        self._patch_httpx(monkeypatch, self.REAL_RESPONSE, calls)
        monkeypatch.setenv("OPEN_PHONE_API_KEY", "test-key")
        assert await get_ai_phone_number() == "+14129101500"

    @pytest.mark.asyncio
    async def test_caches_after_first_success(self, monkeypatch):
        from api.src.open_phone.service import get_ai_phone_number

        calls: list = []
        self._patch_httpx(monkeypatch, self.REAL_RESPONSE, calls)
        monkeypatch.setenv("OPEN_PHONE_API_KEY", "test-key")
        assert await get_ai_phone_number() == "+14129101500"
        assert await get_ai_phone_number() == "+14129101500"
        assert len(calls) == 1, "second call must hit the process cache, not the API"

    @pytest.mark.asyncio
    async def test_missing_number_returns_none_without_caching(self, monkeypatch):
        from api.src.open_phone.service import get_ai_phone_number

        calls: list = []
        self._patch_httpx(monkeypatch, {"data": {"id": "PNWvNqsFFy"}}, calls)
        monkeypatch.setenv("OPEN_PHONE_API_KEY", "test-key")
        assert await get_ai_phone_number() is None


# ===========================================================================
# send_message — group payload
# ===========================================================================


class TestSendMessageGroupPayload:
    @pytest.mark.asyncio
    async def test_list_recipients_in_one_to_array(self):
        import httpx

        from api.src.open_phone import service as op_service

        captured: list[dict] = []

        def handler(request: httpx.Request) -> httpx.Response:
            import json

            captured.append(json.loads(request.content))
            return httpx.Response(202, json={"data": {"id": "ACtest"}})

        def fake_client():
            return httpx.AsyncClient(
                base_url="https://api.openphone.com",
                transport=httpx.MockTransport(handler),
            )

        with patch.object(op_service, "_openphone_client", fake_client):
            await op_service.send_message(
                message="hello group",
                to_phone_number=[EMILIO, SANA],
                from_phone_number="PNtest",
            )
        assert captured[0]["to"] == [EMILIO, SANA]
        assert captured[0]["from"] == "PNtest"
