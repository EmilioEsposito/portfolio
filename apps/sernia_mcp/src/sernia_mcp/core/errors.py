"""Harness-neutral exceptions for ``core`` functions.

The MCP tool wrappers in ``sernia_mcp.tools`` translate these to ``ToolError``.
"""


class CoreError(Exception):
    """Base class for core errors."""


class NotFoundError(CoreError):
    """The requested resource does not exist."""


class ValidationError(CoreError):
    """Caller passed invalid arguments."""


class ExternalServiceError(CoreError):
    """An upstream API (Google, OpenPhone, ClickUp) returned an error."""
