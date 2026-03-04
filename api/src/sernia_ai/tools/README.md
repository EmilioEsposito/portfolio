# Sernia AI — Tools

Agent tools for the Sernia AI assistant. Each module exposes a toolset consumed by the main agent.

## Quo (`quo_tools.py`)

SMS and contact management via the OpenPhone API, bridged through FastMCP.

### SMS Tools

| Tool | Line | Approval | Description |
|------|------|----------|-------------|
| `send_internal_sms` | Sernia AI | No | Team-only messages. All recipients must be "Sernia Capital LLC" contacts. |
| `send_external_sms` | Shared External | Yes (HITL) | Tenant/vendor messages. All recipients must be external contacts. |
| `mass_text_tenants` | Shared External | Yes (HITL) | Send the same message to all tenants in one or more properties, with optional unit filter. Auto-groups by unit. |

### Deterministic Gates

Every SMS goes through a chain of gates before sending:

1. **Contact resolution** — Every recipient must exist as a Quo contact. Unknown numbers are blocked.
2. **Internal/external separation** — `send_internal_sms` blocks external contacts; `send_external_sms` blocks internal contacts. This prevents exposing internal phone numbers in tenant threads.
3. **Unit isolation** (external only) — If multiple recipients have Property + Unit # custom fields, they must all share the same `(Property, Unit #)`. Cross-unit group texts are blocked to prevent sharing contact info between unrelated tenants.

### Mass-Texting Pattern (Per-Unit Sharding)

OpenPhone sends multi-recipient messages as **group texts** — all recipients see each other's numbers and replies. This is fine for roommates in the same unit but a privacy violation across units.

**Use `mass_text_tenants`** for building-wide notices. It automatically:
1. Finds matching tenants from the cached contact list by `(Property, Unit #)`
2. Skips internal contacts and contacts without phone numbers
3. Groups by unit and sends one SMS per unit group

Same-unit roommates share a group text. Different units get separate messages.

The `send_external_sms` unit-isolation gate also enforces this for ad-hoc messages — if the agent tries to send cross-unit, it gets a blocking error.

### Contact Custom Fields

Tenant contacts store unit info in `customFields`:
```json
[
  {"name": "Property", "value": "320"},
  {"name": "Unit #", "value": "02"}
]
```

The `_get_contact_unit()` helper extracts this as a `(property, unit)` tuple, returning `None` for non-tenant contacts.

### Other Tools

- **search_contacts** — Fuzzy search by name, phone, or company against a TTL-cached (5 min) contact list.
- **MCP-bridged tools** — `listMessages_v1`, `getContactById_v1`, `createContact_v1`, etc. Contact writes require HITL approval.

## Other Tool Modules

| Module | Description |
|--------|-------------|
| `google_tools.py` | Gmail (search, read, send), Calendar (list, create), Drive (search, read docs/sheets/PDFs) |
| `clickup_tools.py` | Task management (list, search, create, update, delete) |
| `db_search_tools.py` | Search past agent conversations and SMS history |
| `code_tools.py` | Python sandbox (pydantic-monty) for math, formatting, data manipulation |
| `_logging.py` | Shared error logging wrapper for tool failures |
