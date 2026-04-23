"""Harness-neutral exceptions for tool_core functions."""


class CoreError(Exception):
    """Base class for tool_core errors. Wrappers translate to their harness's error type."""


class NotFoundError(CoreError):
    """The requested resource does not exist."""


class ValidationError(CoreError):
    """Caller passed invalid arguments."""


class ExternalServiceError(CoreError):
    """An upstream API (Google, OpenPhone, ClickUp) returned an error."""
