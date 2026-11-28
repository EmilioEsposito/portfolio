"""Texts through Quo (formerly OpenPhone), from one of the team's two lines.

Each send names its line (``lines.line_for`` picks it from the notice kind):
``team`` is the shared team line (``QUO_SHARED_EXTERNAL_PHONE_ID``), for
prospects, leads and residents; ``ai`` is the Sernia AI line
(``QUO_SERNIA_AI_PHONE_ID``), for showing agents, whose replies reach Sernia
AI's SMS conversation. Both lines answer a send the same way, and a status
read needs only the message id, whichever line sent it.

Written from Quo's API reference:

- send: ``POST /v1/messages`` with ``content``, ``from`` and ``to``; 202 with
  the message's ``id`` and ``status``. A 202 means accepted and nothing more:
  on 2026-11-15 Quo accepted a send to a well-formed number that does not
  exist. The later status read, not the send, catches a number like that.
  https://www.quo.com/docs/mdx/api-reference/messages/send-a-text-message.md
- a group text: the same call with several numbers in ``to``, which land in
  one shared thread (the Sernia AI's ``send_sms`` has sent these since
  2026-08-13).
- status: ``GET /v1/messages/{id}``; ``status`` is one of queued, sent,
  delivered, undelivered or received.
  https://www.quo.com/docs/mdx/api-reference/messages/get-a-message-by-id.md

Requests go through the shared OpenPhone client in ``open_phone/service.py``,
so they share its rate limit, its timeouts and its rule never to resend a
POST that may have reached Quo. Only ``outbox.py`` may use this class.
"""

import re
from collections.abc import Callable, Sequence

import httpx

from api.src.open_phone.rate_limit import CONNECT_ERRORS
from api.src.sernia_ai.config import QUO_SERNIA_AI_PHONE_ID, QUO_SHARED_EXTERNAL_PHONE_ID
from api.src.showing_bookings.providers import Line, NotSent, SentText

# Quo's codes for a recipient it will not text. Nothing was sent.
REFUSAL_CODES = {
    "0201400": "opted_out",
    "0202400": "invalid_number",
    "0203400": "unreachable_number",
}
MAX_LENGTH = 1600  # Quo's limit for ``content``
MAX_GROUP = 10  # Quo's most numbers in one message's ``to``
# Quo's id for each line, sent as ``from``.
LINE_PHONE_IDS: dict[Line, str] = {
    "team": QUO_SHARED_EXTERNAL_PHONE_ID,
    "ai": QUO_SERNIA_AI_PHONE_ID,
}
_E164 = re.compile(r"\+[1-9]\d{1,14}")
_MESSAGE_ID = re.compile(r"AC[A-Za-z0-9]+")


def _shared_client() -> httpx.AsyncClient:
    # Imported here: open_phone.service reads settings and connects the
    # contact store when imported, which the rest of this module never needs.
    from api.src.open_phone.service import _openphone_client

    return _openphone_client()


def _error(response: httpx.Response) -> tuple[str, str]:
    """Quo's error code and a short description from an error answer."""
    try:
        body = response.json()
    except ValueError:
        body = {}
    if not isinstance(body, dict):
        body = {}
    code = str(body.get("code") or "")
    title = body.get("title") or body.get("message") or response.reason_phrase
    return code, f"Quo answered {response.status_code} {code} {title}".replace("  ", " ").strip()


class QuoTextSender:
    """A ``TextSender`` that sends from the line each send names."""

    def __init__(self, *, client_factory: Callable[[], httpx.AsyncClient] | None = None) -> None:
        self._client_factory = client_factory or _shared_client

    async def send_text(self, to: str, body: str, *, line: Line = "team") -> SentText:
        return await self._send([to], body, line)

    async def send_group_text(
        self, to: Sequence[str], body: str, *, line: Line = "team"
    ) -> SentText:
        numbers = list(dict.fromkeys(to))
        if not 2 <= len(numbers) <= MAX_GROUP:
            raise NotSent("rejected", f"A group text goes to 2 to {MAX_GROUP} numbers.")
        return await self._send(numbers, body, line)

    async def _send(self, to: list[str], body: str, line: Line) -> SentText:
        if not all(_E164.fullmatch(number) for number in to):
            raise NotSent("invalid_number", "The number is not in E.164 form.")
        if not body.strip() or len(body) > MAX_LENGTH:
            raise NotSent("rejected", f"A text must have 1 to {MAX_LENGTH} characters.")
        payload = {"content": body, "from": LINE_PHONE_IDS[line], "to": to}
        try:
            async with self._client_factory() as client:
                response = await client.post("/v1/messages", json=payload)
        except CONNECT_ERRORS as exc:
            # The request never reached Quo, so trying again cannot double-send.
            raise NotSent("unavailable", "Could not reach Quo.", retryable=True) from exc
        # Any other exception (a timeout once the request was sent, a dropped
        # answer) propagates: the text may have gone out.
        return self._read_send_answer(response)

    @staticmethod
    def _read_send_answer(response: httpx.Response) -> SentText:
        status = response.status_code
        if status in (200, 201, 202):
            data = response.json().get("data") or {}
            if not data.get("id"):
                raise RuntimeError("Quo accepted the text but its answer has no message id.")
            return SentText(provider_id=data["id"], provider_status=data.get("status"))
        code, detail = _error(response)
        if status == 400 and code in REFUSAL_CODES:
            raise NotSent(REFUSAL_CODES[code], detail)
        if status == 429:
            raise NotSent("rate_limited", detail, retryable=True)
        if 400 <= status < 500 and status != 408:
            # Quo answered and did not send: a bad request, our account or line.
            raise NotSent("rejected", detail)
        # 408 or 5xx: Quo may have taken the text before failing.
        raise RuntimeError(f"{detail}; the text may have been sent.")

    async def text_status(self, provider_id: str) -> str:
        if not _MESSAGE_ID.fullmatch(provider_id):
            raise ValueError("Not a Quo message id.")
        async with self._client_factory() as client:
            response = await client.get(f"/v1/messages/{provider_id}")
        response.raise_for_status()
        return response.json()["data"]["status"]
