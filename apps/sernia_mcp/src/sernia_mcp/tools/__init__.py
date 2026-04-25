"""MCP tool wrappers — thin adapters over ``core/`` functions.

Importing this package registers all tools on the global ``mcp`` instance.
The ``server`` module triggers this import after constructing ``mcp``.
"""
from sernia_mcp.tools import clickup, google, quo, workspace  # noqa: F401
