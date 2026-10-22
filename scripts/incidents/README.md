# Dated incident tooling

Dated directories contain fixed provider identities and reviewed operations for
one incident. They are operational snapshots, not defaults for another account or
future rotation. Reusable private custody belongs in [credentials/](../credentials/README.md).

| Incident | Public tracker | Executable catalog |
|---|---|---|
| October 21, 2026 | [Recovery tracker](../../docs/incidents/2026-10-21/README.md) | [Adapters](2026-10-21/README.md) |

Read the current tracker, exact provider mapping and private journal before running
an adapter. A past successful command does not authorize replay. Source cleanup
never authorizes moving/deleting private bundles or resetting uncertain journals.
Prune executable history only after unresolved operations no longer depend on it;
preserve the evidence record and retention obligations.
