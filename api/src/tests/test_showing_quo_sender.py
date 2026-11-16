"""How the Quo text sender reads Quo's answers. Quo is replaced by ``httpx.MockTransport``.

The answers are shaped as Quo's API reference documents them; nothing here
has been checked against a live send.
"""

import json

import httpx
import pytest

from api.src.sernia_ai.config import QUO_SHARED_EXTERNAL_PHONE_ID
from api.src.showing_bookings.providers import NotSent, SentText
from api.src.showing_bookings.quo import QuoTextSender

TO = "+14125550117"


class Quo:
    """Answers every request with one response, and keeps the requests."""

    def __init__(self, status=202, body=None, error: Exception | None = None):
        self.status, self.body, self.error = status, body, error
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.error:
            raise self.error
        return httpx.Response(self.status, json=self.body)

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url="https://api.openphone.com", transport=httpx.MockTransport(self)
        )


def sender(quo: Quo) -> QuoTextSender:
    return QuoTextSender(client_factory=quo.client)


def accepted(message_id="AC123abc", status="queued") -> dict:
    return {
        "data": {
            "id": message_id,
            "to": [TO],
            "from": "+14125550100",
            "text": "Booked.",
            "phoneNumberId": QUO_SHARED_EXTERNAL_PHONE_ID,
            "conversationId": "CN123",
            "direction": "outgoing",
            "userId": None,
            "status": status,
            "createdAt": "2027-04-12T18:14:00Z",
            "updatedAt": "2027-04-12T18:14:00Z",
        }
    }


def error(status: int, code: str, title: str) -> dict:
    return {
        "message": title,
        "code": code,
        "status": status,
        "docs": "https://quo.com/docs",
        "title": title,
    }


@pytest.mark.asyncio
async def test_a_text_goes_from_the_shared_team_line():
    quo = Quo(202, accepted())

    assert await sender(quo).send_text(TO, "Booked.") == SentText("AC123abc", "queued")
    (request,) = quo.requests
    assert (request.method, request.url.path) == ("POST", "/v1/messages")
    assert json.loads(request.content) == {
        "content": "Booked.",
        "from": QUO_SHARED_EXTERNAL_PHONE_ID,
        "to": [TO],
    }


@pytest.mark.asyncio
async def test_by_default_it_uses_the_shared_openphone_client(monkeypatch):
    from api.src.open_phone import service

    quo = Quo(202, accepted())
    monkeypatch.setattr(service, "_openphone_client", quo.client)

    await QuoTextSender().send_text(TO, "Booked.")
    assert len(quo.requests) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("code", "title", "reason"),
    [
        ("0201400", "User Opted Out", "opted_out"),
        ("0202400", "Invalid `to` Phone Number", "invalid_number"),
        ("0203400", "Unreachable `to` Phone Number", "unreachable_number"),
    ],
)
async def test_a_refused_number_is_not_sent_and_not_retried(code, title, reason):
    with pytest.raises(NotSent) as refused:
        await sender(Quo(400, error(400, code, title))).send_text(TO, "Booked.")
    assert (refused.value.reason, refused.value.retryable) == (reason, False)
    assert title in refused.value.detail


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "code"),
    [(400, "0200400"), (400, "0206400"), (401, "0200401"), (402, "0200402"), (403, "0202403")],
)
async def test_other_refusals_are_not_sent_and_not_retried(status, code):
    with pytest.raises(NotSent) as refused:
        await sender(Quo(status, error(status, code, "Refused"))).send_text(TO, "Booked.")
    assert (refused.value.reason, refused.value.retryable) == ("rejected", False)


@pytest.mark.asyncio
async def test_rate_limited_is_not_sent_and_can_be_retried():
    with pytest.raises(NotSent) as refused:
        await sender(Quo(429, error(429, "0200429", "Too Many Requests"))).send_text(TO, "Hi")
    assert (refused.value.reason, refused.value.retryable) == ("rate_limited", True)


@pytest.mark.asyncio
async def test_quo_out_of_reach_is_not_sent_and_can_be_retried():
    quo = Quo(error=httpx.ConnectError("refused"))
    with pytest.raises(NotSent) as refused:
        await sender(quo).send_text(TO, "Hi")
    assert (refused.value.reason, refused.value.retryable) == ("unavailable", True)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "quo",
    [
        Quo(500, error(500, "0200500", "Unexpected Error")),
        Quo(408, {}),
        Quo(202, {"data": {}}),
        Quo(error=httpx.ReadTimeout("no answer")),
    ],
    ids=["server error", "request timeout", "no message id", "read timeout"],
)
async def test_an_answer_that_leaves_doubt_is_not_a_refusal(quo):
    """The outbox saves these as unknown: the text may have gone out."""
    with pytest.raises(Exception) as raised:
        await sender(quo).send_text(TO, "Booked.")
    assert not isinstance(raised.value, NotSent)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("to", "body", "reason"),
    [("4125550117", "Hi", "invalid_number"), (TO, " ", "rejected"), (TO, "x" * 1601, "rejected")],
)
async def test_a_malformed_text_is_never_sent(to, body, reason):
    """Checked here, before Quo: not E.164, blank, or over Quo's length limit."""
    quo = Quo(202, accepted())
    with pytest.raises(NotSent) as refused:
        await sender(quo).send_text(to, body)
    assert refused.value.reason == reason and quo.requests == []


@pytest.mark.asyncio
async def test_an_accepted_text_is_only_accepted():
    """A 202 is acceptance, not delivery. On 2026-11-15 Quo accepted a send to a
    well-formed number that does not exist; the status read is what catches it."""
    quo = Quo(202, accepted(status="queued"))
    texts = sender(quo)

    sent = await texts.send_text("+14125550100", "Booked.")
    assert sent == SentText("AC123abc", "queued")

    quo.status, quo.body = 200, accepted(status="undelivered")
    assert await texts.text_status(sent.provider_id) == "undelivered"


@pytest.mark.asyncio
async def test_a_texts_status_is_read_by_its_id():
    quo = Quo(200, accepted(status="delivered"))

    assert await sender(quo).text_status("AC123abc") == "delivered"
    (request,) = quo.requests
    assert (request.method, request.url.path) == ("GET", "/v1/messages/AC123abc")


@pytest.mark.asyncio
async def test_a_status_read_needs_a_quo_message_id():
    quo = Quo(200, accepted())
    with pytest.raises(ValueError):
        await sender(quo).text_status("../contacts")
    assert quo.requests == []


# Group texts: one message to several numbers, which share one thread.

ROOMMATE = "+14125550143"


@pytest.mark.asyncio
async def test_a_group_text_names_every_number_in_one_message():
    quo = Quo(202, accepted())

    sent = await sender(quo).send_group_text([TO, ROOMMATE, TO], "Showing booked.")

    assert sent == SentText("AC123abc", "queued")
    (request,) = quo.requests
    assert json.loads(request.content) == {
        "content": "Showing booked.",
        "from": QUO_SHARED_EXTERNAL_PHONE_ID,
        "to": [TO, ROOMMATE],
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "numbers",
    [[TO], [f"+1412555{index:04d}" for index in range(150, 161)]],
    ids=["one number", "eleven numbers"],
)
async def test_a_group_text_needs_two_to_ten_numbers(numbers):
    quo = Quo(202, accepted())

    with pytest.raises(NotSent) as refused:
        await sender(quo).send_group_text(numbers, "Showing booked.")

    assert refused.value.reason == "rejected"
    assert quo.requests == []


@pytest.mark.asyncio
async def test_a_group_text_with_one_bad_number_sends_nothing():
    quo = Quo(202, accepted())

    with pytest.raises(NotSent) as refused:
        await sender(quo).send_group_text([TO, "4125550143"], "Showing booked.")

    assert refused.value.reason == "invalid_number"
    assert quo.requests == []


@pytest.mark.asyncio
async def test_a_refused_group_text_is_not_sent():
    quo = Quo(400, error(400, "0201400", "User Opted Out"))

    with pytest.raises(NotSent) as refused:
        await sender(quo).send_group_text([TO, ROOMMATE], "Showing booked.")

    assert refused.value.reason == "opted_out"
