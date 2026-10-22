# October 2026 execution notes

These fixed-target adapters were reviewed for this incident. They are not generic
provider defaults or authorization for a future rotation. Current status lives in
[the tracker](README.md); common custody rules live in [the playbook](../../credentials/README.md).

## Authorization history

During a hosting hold, Railway remains off limits until the owner explicitly releases it: no plugin,
API (including status APIs), CLI, dashboard, credential retrieval, upload, build,
deployment, integration reconnection, or CI that calls Railway. Public status web
pages are allowed. An operational recovery notice is not authorization to act.
The GitHub Railway app was removed by the owner; reconnect only at an authorized cutover. A PR
can still call Railway through `.github/workflows/neon_workflow.yml`, independently
of that app. Use `[skip ci]` for preparatory pushes; do not open a PR or merge until its
automation is safe. The October 22 owner instruction explicitly authorized
Portfolio recovery; the incident tracker records execution and remaining gates. Do not change CI credentials to force a run.

Ventures provider resources and PR #437 belong to their other chat. Shared org
management keys must be coordinated; do not reuse its private files or mint
competing replacements. Its implementation informed this custody design.

## Authorized installation adapters

`scripts/incidents/2026-10-21/delivery.py` installs explicitly selected names from a custody
cycle into the primary checkout or fixed Portfolio Railway targets. Railway values
travel through stdin, use `replace=false` and `skipDeploys=true`, and are independently
read back. A private intent journal blocks uncertain replay. This does not deploy
or prove runtime health. Local updates preserve unrelated assignments, back up the
original privately, reject symlinks/tracked targets, and verify literal readback.

`scripts/incidents/2026-10-21/mint_google.py` captures a new key directly into a 0600 private
file after checking account identity and removal of excessive project roles. It
requires explicit `--restore-account`; the account is enabled before key creation.
An existing intent blocks another mint until IAM and private files are reconciled.
The application variable contains **base64-encoded JSON**, not raw JSON.

`scripts/incidents/2026-10-21/rotate_neon.py` resets the app role on an explicitly selected
Portfolio branch and captures both replacement connection URLs into a new cycle.
It copies only that environment's selected non-database values from the named
source cycle. A private intent blocks retries after uncertainty; reconcile the
provider and captured response before proceeding. Delivery, old-password rejection,
and surviving-session checks remain separate steps.

Example (names and paths only; never paste values into commands):

```sh
python3 scripts/incidents/2026-10-21/delivery.py local --cycle <cycle> --environment local --service fastapi --names SESSION_SECRET_KEY CRON_SECRET
python3 scripts/incidents/2026-10-21/delivery.py railway --cycle <cycle> --environment development --service fastapi --names DATABASE_URL DATABASE_URL_UNPOOLED
python3 scripts/incidents/2026-10-21/mint_google.py --cycle <cycle> --restore-account
python3 scripts/incidents/2026-10-21/rotate_neon.py --source-cycle <source-cycle> --cycle <new-cycle> --environment local
```

Environments without a Quo webhook can start without its signing key; the webhook
route continues to fail closed with HTTP 403. Do not fill absent development/local
webhook configuration with production's signing key.
