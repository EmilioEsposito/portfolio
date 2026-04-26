"""Harness-agnostic async tool functions.

Each module exports plain async functions (no MCP context, no PydanticAI
RunContext) that can be called from any harness: a FastMCP server, a CLI
script, a test, or a future PydanticAI tool wrapper.

Signature rules:
  1. No framework context. Take typed args, return a Pydantic model or str.
  2. ``user_email`` is an explicit kwarg on anything that does Google delegation.
  3. No approval gating in core. The MCP tool wrappers in
     ``sernia_mcp.tools`` decide whether to show a HITL approval card.
"""
