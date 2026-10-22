"""Bounded, categorical OAuth failure telemetry; never log protocol payloads."""

from __future__ import annotations

import json
import logging

from starlette.types import ASGIApp, Message, Receive, Scope, Send

logger = logging.getLogger(__name__)
MAX_ERROR_BYTES = 4096
_ERROR_CODES = frozenset(
    {
        "invalid_client",
        "invalid_grant",
        "invalid_request",
        "invalid_scope",
        "unauthorized_client",
        "unsupported_grant_type",
    }
)
_JWT_LOGGER_NAME = "fastmcp.server.auth.providers.jwt"
_JWT_FAILURES = {
    "Token validation failed: JWT signature/format invalid": "signature_or_format",
    "Token validation failed: unsupported critical JWT header": "critical_header",
    "Bearer token rejected for client %s: token expired": "expired",
    "Bearer token rejected for client %s: issuer mismatch (got %r, expected %r)": "issuer",
    "Bearer token rejected for client %s: audience mismatch (got %r, expected %r)": "audience",
    "Bearer token rejected for client %s: missing required scopes (has %s, requires %s)": "scope",
    "JWKS fetch blocked by SSRF protection: %s": "jwks_fetch",
    "JWKS key processing failed: %s": "jwks_processing",
    "JWKS key lookup failed: key ID '%s' not found": "jwks_key_missing",
    "JWKS key lookup failed: key ID '%s' is present but its key type is unsupported": "jwks_key_type",
    "Token validation failed: %s": "validation_error",
}


class _JWTDiagnosticsFilter(logging.Filter):
    """Sanitize at the originating logger, before any handler sees a record."""

    def filter(self, record: logging.LogRecord) -> bool:
        if record.name != _JWT_LOGGER_NAME:
            return False
        reason = _JWT_FAILURES.get(record.msg) if isinstance(record.msg, str) else None
        if reason is None and record.levelno < logging.INFO:
            return False
        reason = reason or "unknown_event"
        # Do not format the original message or examine its arguments: those
        # contain claims, URLs, key IDs and exception text. Rebuild the record
        # so cached messages, exceptions, stacks and arbitrary extras vanish.
        safe = logging.LogRecord(
            logger.name,
            max(record.levelno, logging.WARNING),
            __file__,
            0,
            "OAuth JWT verifier diagnostic: %s",
            ({"reason": reason},),
            None,
        )
        record.__dict__.clear()
        record.__dict__.update(safe.__dict__)
        try:
            # FastMCP stops propagation at its own logger. Forward only this
            # reconstructed record through our normal application logger.
            logger.handle(record)
        except Exception:
            pass  # A telemetry sink failure must never affect verification.
        return False


def install_jwt_diagnostics() -> None:
    """Enable only sanitized verifier failures, never global OAuth debug logs.

    Exact templates are pinned to FastMCP 4.1.3. Unknown debug records are
    dropped; unknown higher-level events retain only a static category. All
    originals are dropped, and sanitized records use the application logger.
    Install before serving requests. Repeated installation is harmless.
    """
    verifier_logger = logging.getLogger(_JWT_LOGGER_NAME)
    if not any(isinstance(item, _JWTDiagnosticsFilter) for item in verifier_logger.filters):
        verifier_logger.addFilter(_JWTDiagnosticsFilter())
    verifier_logger.setLevel(logging.DEBUG)


def _category(body: bytearray, oversized: bool) -> dict[str, str]:
    """Map untrusted JSON to a finite vocabulary, dropping every original value."""
    if oversized:
        return {"error": "unknown", "reason": "oversized_response"}
    try:
        payload = json.loads(body)
    except (ValueError, UnicodeError, RecursionError):
        return {"error": "unknown", "reason": "malformed_response"}
    if not isinstance(payload, dict):
        return {"error": "unknown", "reason": "malformed_response"}

    supplied_error = payload.get("error")
    error = (
        supplied_error
        if isinstance(supplied_error, str) and supplied_error in _ERROR_CODES
        else "unknown"
    )
    description = payload.get("error_description")
    text = description.lower() if isinstance(description, str) else ""
    # Match specific failure families before the generic assertion/grant cases.
    # Strings are used only for classification; none become log arguments.
    if "missing client_assertion" in text or "missing assertion" in text:
        reason = "missing_assertion"
    elif "client_assertion_type" in text:
        reason = "assertion_type"
    elif "replay" in text or "already used" in text:
        reason = "replay"
    elif "pkce" in text or "code_verifier" in text or "code challenge" in text:
        reason = "pkce"
    elif "assertion" in text and any(
        word in text for word in ("lifetime", "expired", "future", "exp claim")
    ):
        reason = "assertion_lifetime"
    elif "assertion" in text:
        reason = "invalid_assertion"
    elif "redirect_uri" in text or "redirect uri" in text:
        reason = "redirect_uri"
    elif "client_id" in text:
        reason = "client_identity"
    elif error == "invalid_grant":
        reason = "grant"
    else:
        reason = "unspecified"
    return {"error": error, "reason": reason}


class OAuthDiagnosticsMiddleware:
    """Observe only POST /token 400/401 responses without changing ASGI traffic.

    Request bodies, headers and query strings are never read. Successful response
    bodies are never inspected. Error bodies are buffered only up to 4 KiB, then
    discarded; the log record contains only status and allowlisted categories.
    These categories aid diagnosis, not security decisions or audit attribution.
    """

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            scope["type"] != "http"
            or scope.get("method") != "POST"
            or scope.get("path") != "/token"
        ):
            await self.app(scope, receive, send)
            return

        status = 0
        body = bytearray()
        oversized = False

        async def observe(message: Message) -> None:
            nonlocal status, oversized
            if message["type"] == "http.response.start":
                status = message["status"]
            if message["type"] == "http.response.body" and status in (400, 401):
                chunk = message.get("body", b"")
                if not oversized:
                    if len(body) + len(chunk) > MAX_ERROR_BYTES:
                        body.clear()
                        oversized = True
                    else:
                        body.extend(chunk)
                await send(message)
                if not message.get("more_body", False):
                    category = _category(body, oversized)
                    body.clear()
                    try:
                        logger.warning("OAuth token failure: %s", {"status": status, **category})
                    except Exception:
                        # Diagnostics must not turn a completed OAuth rejection
                        # into an application error if a logging sink fails.
                        pass
                return
            await send(message)

        await self.app(scope, receive, observe)
