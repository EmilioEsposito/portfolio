"""Thin upstream API clients vendored into this service.

Each module here wraps a single third-party API (Gmail, OpenPhone, ClickUp,
Google service-account auth) with the minimum surface needed by ``core/``.

These are vendored from the FastAPI monorepo (api/src/google/, api/src/open_phone/,
api/src/utils/) so the MCP service is self-contained — no monorepo path
dependency. Sync points and intentional drift are documented in CLAUDE.md.
"""
