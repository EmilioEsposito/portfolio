# October 2026 Portfolio adapters

Read [current status](../../../docs/incidents/2026-10-21/README.md),
[execution notes](../../../docs/incidents/2026-10-21/operations.md), and the
[reusable custody playbook](../../../docs/credentials/README.md) first.

| Script | Scope and boundary |
|---|---|
| `delivery.py` | Selected values to fixed Portfolio Railway targets or primary local files; private intent/readback, no deployment |
| `mint_google.py` | Exact incident Google account after excessive-role removal; explicit restoration, private one-time capture, no delivery |
| `rotate_neon.py` | Exact incident branch/role password reset into a new cycle; no delivery or session termination |

Each command supports `--help` without contacting a provider. Invoke by canonical
path, for example `python3 scripts/incidents/2026-10-21/delivery.py --help`.
These adapters import `scripts/credentials/recovery.py`; they do not contain a
second custody implementation. They retain original operation journal filenames
so interrupted operations are still detected after the move.

Review hard-coded targets before later reuse. Never delete a journal to force a
retry; first reconcile the issuer and private capture. No secret files live here.
