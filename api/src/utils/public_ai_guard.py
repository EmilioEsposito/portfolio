"""Bound public inference independently of prompts, clients, and stream duration.

Counters are per worker; use an OpenRouter key credit limit as the durable
account-wide backstop. Forwarded headers are deliberately not trusted here.
"""

import asyncio
import json
import time
from collections import deque

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from api.src.utils.llm import public_inference_available

PUBLIC_PATHS = frozenset(
    {
        "/api/ai-demos/chat-emilio",
        "/api/ai-demos/chat-weather",
        "/api/ai-demos/multi-agent-chat",
        "/api/google/gmail/generate_email_response",
    }
)
MAX_BODY = 24000


def validate_public_body(data: object) -> None:
    if not isinstance(data, dict):
        raise ValueError("Expected a JSON object.")
    if "messages" not in data:
        return  # Email schema supplies its own strict limits.
    messages = data["messages"]
    if not isinstance(messages, list) or not 1 <= len(messages) <= 16:
        raise ValueError("Use 1–16 messages; start a new conversation when it gets longer.")
    total = 0
    for message in messages:
        if not isinstance(message, dict) or message.get("role") not in {"user", "assistant"}:
            raise ValueError("Only user and assistant messages are accepted.")
        parts = message.get("parts")
        if not isinstance(parts, list) or not 1 <= len(parts) <= 8:
            raise ValueError("Messages must contain text.")
        safe = []
        for part in parts:
            if not isinstance(part, dict):
                raise ValueError("Invalid message part.")
            # Browser tool results, system instructions, and attachments never
            # become model context. Historical assistant text is untrusted too.
            if part.get("type") != "text":
                continue
            text = part.get("text")
            if not isinstance(text, str) or len(text) > 4000:
                raise ValueError("Keep each message below 4,000 characters.")
            total += len(text)
            safe.append({"type": "text", "text": text})
        message["parts"] = safe
    if total > 12000:
        raise ValueError("Conversation is too long; start a new conversation.")
    if messages[-1].get("role") != "user" or not any(
        p["text"].strip() for p in messages[-1]["parts"]
    ):
        raise ValueError("End the conversation with a non-empty user message.")


class PublicAIGuard:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app
        self.hits: dict[str, deque[float]] = {}
        self.global_hits: deque[float] = deque()
        self.active = 0

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            scope["type"] != "http"
            or scope.get("method") != "POST"
            or scope["path"].rstrip("/") not in PUBLIC_PATHS
        ):
            await self.app(scope, receive, send)
            return

        async def reject(status: int, detail: str) -> None:
            await JSONResponse(
                {"detail": detail},
                status_code=status,
                headers={"Retry-After": "60"} if status == 429 else {},
            )(scope, receive, send)

        body = bytearray()
        try:
            async with asyncio.timeout(10):
                while True:
                    event = await receive()
                    if event["type"] == "http.disconnect":
                        return
                    body.extend(event.get("body", b""))
                    if len(body) > MAX_BODY:
                        await reject(413, "Request is too large.")
                        return
                    if not event.get("more_body"):
                        break
        except TimeoutError:
            await reject(408, "Request timed out.")
            return
        try:
            data = json.loads(body)
            validate_public_body(data)
        except (ValueError, TypeError, UnicodeDecodeError) as exc:
            await reject(400, str(exc)[:180])
            return

        if not public_inference_available():
            await reject(503, "Public AI is temporarily unavailable. Please try again later.")
            return

        now = time.monotonic()
        self.hits = {
            key: deque(t for t in times if t > now - 3600)
            for key, times in self.hits.items()
            if times and times[-1] > now - 3600
        }
        while self.global_hits and self.global_hits[0] <= now - 86400:
            self.global_hits.popleft()
        # ASGI client comes from the server's trusted proxy configuration; raw
        # X-Forwarded-For from the HTTP request is never used as an identity.
        client = str((scope.get("client") or ("unknown",))[0])
        hits = self.hits.setdefault(client, deque())
        if (
            self.active >= 4
            or len(self.global_hits) >= 300
            or len(hits) >= 30
            or sum(t > now - 60 for t in hits) >= 6
        ):
            await reject(429, "The demo has reached its usage limit. Please try again later.")
            return
        hits.append(now)
        self.global_hits.append(now)
        self.active += 1
        replayed = False
        started = False

        async def bounded_receive() -> dict:
            nonlocal replayed
            if not replayed:
                replayed = True
                return {
                    "type": "http.request",
                    "body": json.dumps(data).encode(),
                    "more_body": False,
                }
            return await receive()

        async def tracked_send(message: dict) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            async with asyncio.timeout(60):
                await self.app(scope, bounded_receive, tracked_send)
        except TimeoutError:
            if not started:
                await reject(504, "This run took too long. Please try a shorter question.")
            else:
                await send({"type": "http.response.body", "body": b"", "more_body": False})
        finally:
            self.active -= 1
