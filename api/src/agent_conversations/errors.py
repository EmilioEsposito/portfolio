"""Stable HTTP responses for approval failures from inference providers."""

import anthropic
import openai
from pydantic_ai.exceptions import ModelHTTPError

LLMAPIError = (anthropic.APIError, openai.APIError, ModelHTTPError)


def _llm_error_response(
    e: Exception,
    context: str = "request",
) -> tuple[int, str]:
    """Map an LLM provider API error to an HTTP status + user-facing message.

    Returns (status_code, user_message) tuple.
    """
    status_error_types = (anthropic.APIStatusError, openai.APIStatusError, ModelHTTPError)
    connection_error_types = (
        anthropic.APIConnectionError,
        anthropic.APITimeoutError,
        openai.APIConnectionError,
        openai.APITimeoutError,
    )
    if isinstance(e, status_error_types):
        status = getattr(e, "status_code", 500)
        # 529 = Overloaded (Anthropic), 429 = Rate limited → both retriable.
        if status in (529, 429):
            return 503, "The AI service is temporarily overloaded. Please try again in a moment."
        # Any other provider-side 5xx → surface as 502 (bad gateway).
        if 500 <= status < 600:
            return 502, "The AI service is temporarily unavailable. Please try again."
        # 4xx → our misconfiguration (auth, bad request, etc.).
        return 500, "An internal error occurred. Please try again."
    if isinstance(e, connection_error_types):
        return 503, "Unable to reach the AI service. Please try again."
    return 500, "An internal error occurred. Please try again."
