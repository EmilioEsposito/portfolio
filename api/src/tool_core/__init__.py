"""
Harness-agnostic async tool functions.

Each module exports plain async functions (no RunContext, no ApprovalRequired)
that can be called from any harness: pydantic-ai agents, FastMCP servers, HTTP
endpoints, CLI scripts, tests.

Signature rules (see /Users/eesposito/.claude/plans/they-would-need-to-ticklish-peacock.md):

1. No RunContext. Take raw typed args. Return a Pydantic model.
2. `user_email` is an explicit kwarg on anything that does Google delegation.
3. No approval gating in core. Wrappers decide.
4. `db_session: AsyncSession | None = None` on DB-touching cores; fall back
   to `session_context()` when not supplied.
"""
