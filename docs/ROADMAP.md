# Open work

These are exploratory follow-ups, not current system behavior or delivery commitments.
The module READMEs describe the shipped architecture.

- Sernia MCP tool coverage and migration questions: [service backlog](../apps/sernia_mcp/TODOS.md).
- Sernia AI: evaluate event debouncing, conversation deduplication, notification
  routing, and voice-transcript processing as operational needs justify them.
- Leasing: evaluate event-driven Gmail processing before replacing scheduled
  checks. Keep existing escalation and leasing workflows until replacements are verified.
- Tests: consider opt-in ClickUp custom-field create/update coverage with dedicated test data.
- Document automation: explore template-based DOCX generation.

The earlier Sernia implementation plan and DBOS checklist are retained in Git
history. APScheduler remains active; see [scheduler status](../api/src/schedulers/README.md).
