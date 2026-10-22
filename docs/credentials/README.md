# Credential recovery playbook

Use this for routine rotation and incidents. The current incident is tracked in
[October 2026 tracker](../incidents/2026-10-21/README.md). This repository is public.

## Ownership and authorization

The Portfolio owner may prepare scripts, import owner-supplied replacements, and
generate app-owned secrets locally. The owner has asked the agent to take charge
of provider credential creation when exact identity, scope, and private capture
are established. Do not ask again for routine authorized preparation. An uncertain
provider target, shared credential, destructive reset, or missing capture path is
a real blocker, not a reason to guess. Browser credential-entry/passkey handoffs
still apply when required by the browser tool.

Provider access, deployment and revocation require the owner's current scope. A hosting
hold prohibits provider access and runtime delivery until the owner releases it;
public incident notices alone never grant that authorization. Check the owning
incident tracker before using a dated adapter. Coordinate shared credentials with
the other repository owner instead of reusing its private files.

## Keep public and private evidence separate

The public tracker contains variable names, environment/service mappings,
dependencies, and redacted task status. Keep values, token prefixes/suffixes,
hashes, database hostnames, user/session IDs, exact provider object IDs, audit IPs,
private evidence, recovery-store locators, and detailed unresolved access in the
private incident directory. Never paste secret values into chat, screenshots,
commands, PRs, commits, test fixtures, exception messages, or agent tool results.

Private files use mode 0600 inside owned 0700 directories in the primary checkout's
Git-ignored `.local/credential-recovery/` folder. They are
plaintext, not an encrypted vault. Use FileVault and a separate password-manager
backup; permissions do not protect against another process running as the same
user. Preserve the directory across worktree cleanup. Do not save it in a synced
public folder, artifact upload, or CI cache. Do not cat, source, diff, or screenshot
private inputs, bundles, prepared payloads, or provider responses.

## Offline custody workflow

`scripts/credentials/recovery.py` imports no application modules and makes no network
calls. It never reads existing application `.env` files automatically. The manifest
is a source-derived candidate map, not a verified hosted inventory or a list of all
mandatory startup variables. Every destination requires source and live metadata reconciliation before delivery.
The manifest is the routing contract; it must be updated when consumers change.

```sh
python3 scripts/credentials/recovery.py --cycle 2026-10-21-01 init
python3 scripts/credentials/recovery.py --cycle 2026-10-21-01 generate --environment development
python3 scripts/credentials/recovery.py --cycle 2026-10-21-01 generate --environment production
```

The default store is `<primary-checkout>/.local/credential-recovery/2026-10-21-01/`
(on this workstation, `/Users/eesposito/code/portfolio/.local/credential-recovery/2026-10-21-01/`).
The CLI resolves the primary checkout via Git metadata even from linked worktrees.
It refuses a non-ignored or tracked recovery folder and all other in-repo paths.
Explicit `--root` stores outside repositories remain supported.
Initialization creates private `inbox/development.env`, `inbox/production.env`, and
`inbox/local.env` for the owner to paste Apple Passwords exports into. This does
not edit runtime `.env` files. The agent then imports those files by path, without
viewing contents:

```sh
python3 scripts/credentials/recovery.py --cycle 2026-10-21-01 import-env \
  --environment development \
  --source "/Users/eesposito/code/portfolio/.local/credential-recovery/2026-10-21-01/inbox/development.env"
python3 scripts/credentials/recovery.py --cycle 2026-10-21-01 status
python3 scripts/credentials/recovery.py --cycle 2026-10-21-01 plan \
  --environment development --service fastapi
```

Supported input: literal `NAME=value`, optional `export`, comments, JSON-style
double-quoted strings (including `\n`), and single-quoted multiline values. No
shell commands or `${...}` substitutions are evaluated. Duplicate, empty,
placeholder, unknown, retired, and wrong-environment names fail the whole import.
Do not rename an old key to make it pass. Blank/comment-only inboxes fail until
populated. The only output is names, lifecycle states, counts, and prepared-file
paths. Unexpected errors suppress input and traceback details.

Generation covers independent 384-bit `SESSION_SECRET_KEY` and `CRON_SECRET`
candidates per environment and `SERNIA_MCP_INTERNAL_BEARER_TOKEN` only in manifest-authorized environments.
It does not generate provider-issued credentials, encryption roots, or another
VAPID pair when the owner already has replacements. Rerunning generation preserves
existing values. Importing identical values is idempotent; conflicting values
fail without overwriting. If the owner already saved session/cron candidates,
choose a fresh cycle and import them first. Retain both sets until a deliberate
cutover selects one; never silently override.

For another incident or another post-all-clear rotation, choose a new cycle label.
No implicit copy-forward occurs. Inventory any old roots/session dependencies
before starting; new private generation does not invalidate an old credential.

## Provider creation and private capture contract

Before minting, resolve exact account/project, instance, mode, environment, old
credential ID, scope/cap, consumers, and an independent admin recovery path. Check
the private ledger and password-manager inventory to avoid duplicates. Prefer
restricted application credentials over organization management keys.

A future provider adapter must:

1. Persist a private operation intent with an operation ID and exact target before
   mutation. Use provider idempotency where available; lock concurrent writers.
2. Keep bootstrap credentials in process memory from an explicit private file or
   existing authorized CLI session. Never pass values in argv or tool arguments.
3. Use a fixed official endpoint with TLS verification, bounded time/body size,
   no redirects, and suppressed subprocess stdout/stderr and raw errors. Never
   log full request/response objects. Do not evaluate browser code to bypass UI
   credential handling requirements.
4. Capture the one-time value directly into a mode-0600 file, atomically save and
   read back privately, then record only known metadata in agent output.
5. Independently verify account/mode/scopes using a harmless request. Authentication
   alone is not sufficient evidence of intended scope, runtime readiness, or
   prior-key rejection. Avoid messages, calls, payments, or arbitrary customer reads.
6. On timeout, interruption, or an ambiguous response, mark the operation uncertain
   and reconcile provider object IDs and the local journal. Never blindly retry
   a non-idempotent mint/reset/revoke. A new one-time key without its captured value
   is an orphan requiring explicit reconciliation.

The offline tool implements custody and app-secret generation, **not provider
minting, revocation, or upload adapters**. Do not present it as automated rotation
of every vendor. Add and test each provider adapter against this contract when
its exact current API and account are verified. Most incident replacements already
exist in the owner's password manager; import those first.

## Lifecycle ledger and evidence

Keep separate states for inventory, issuer revocation, local capture, provider
verification, delivery, runtime verification, and residual access. Outcomes are
`unknown`, `reported`, `verified`, `failed`, `uncertain`, or `held`. User reports
are not independent provider proofs. The bundle appends events and status shows
the latest recorded assertion; it is not tamper-proof or an encrypted audit vault.
Free-form evidence stays private and is never printed by status.

```sh
python3 scripts/credentials/recovery.py --cycle 2026-10-21-01 record \
  --environment production --names CLERK_SECRET_KEY \
  --stage issuer_revocation --outcome reported --source /absolute/private/evidence.txt
```

The evidence file must already be owned/mode 0600 in a 0700 directory, either
inside the approved ignored recovery folder or outside repositories.
`record` performs no provider action and cannot establish a live fact by itself.
Capture events are generated only by import/generation. Prepared files do not
advance delivery, revocation, or runtime states.

## Offline delivery preparation

Explicitly select one environment/service and variable set. The tool writes a
fresh immutable private JSON envelope with target labels and selected values,
plus a matching `.env` file. Both are privately read back for equality. The
envelope is an input to a later reviewed adapter, not a Railway API payload.

```sh
python3 scripts/credentials/recovery.py --cycle 2026-10-21-01 prepare \
  --environment production --service fastapi --names SESSION_SECRET_KEY CRON_SECRET
```

Service allowlists prevent backend secrets going to the frontend. Related database
URLs and VAPID keys must be selected together. Database URLs must agree on
role/password/database/Neon endpoint; this does not prove environment ownership.
VAPID pair verification requires the existing project `cryptography` dependency
and checks P-256/public-key agreement without printing either key. Other imports
provide syntax/custody checks only, not provider validation. No dynamic environment
discovery, network preflight, upload, deploy, or activation command exists here.

After explicit hold release, resolve exact hosted IDs and compare expected service
names without printing current values. Deliver dev first with deploy suppressed,
read back equality privately, then deploy a reviewed exact commit with background
automation disabled. Test owner authentication, inbound signatures, shared tools,
and operational flows under controlled conditions. Test the intended disabled
paths too. Promote production only after dependent consumers are coordinated.
Keep activation of schedules, email, SMS, calling, push, and external webhooks
separate from a health check. Revoke residual originals after cutover if still
needed, then verify rejection and handle sessions/refresh tokens/derived access.
Do not roll back by restoring a potentially exposed credential.

## Integration-specific ordering

| Integration | Required recovery work |
|---|---|
| Clerk backend | Same environment key to API and web; match instance/publishable key; sessions, OAuth grants, webhook secrets remain separate. |
| Google OAuth | Replacement client secret to the correct backend and **both Clerk Google provider configurations**; preserve callbacks; fresh consent only when ready. |
| Google service account | Keep disabled; review Editor/Token Creator and domain-wide delegation scopes; avoid recreating broad access by default. Mint/capture a replacement only after identity/scope decisions; enable separately. |
| Neon | Exact production/development/local branch mapping; paired URLs; passwords and existing sessions are independent; preserve backups and historical OAuth-token obligations. |
| Quo/Clerk webhooks | Correct per-environment signing secret and endpoint; avoid mismatched overlap windows or duplicate active endpoints. Verify with controlled fixtures before resuming live delivery. |
| MCP | Use the verified environment/service mapping; OAuth client secret, downstream keys, bearer caller/server, cached grants, and reachability are independent. Verify actual bearer callers before delivery. |
| Session/cron | Session change invalidates signed browser sessions; cron caller/server must change together; bearer strings never go in logged URLs. |
| VAPID | Owner has separate replacement pairs; verify matching pairs per environment; existing browser subscriptions require explicit renewal. Do not silently delete subscription data. |
| OpenRouter | Keep public-demo and operations keys separate and verify spend caps; verify actual sharing rather than assuming environment isolation. |
| Logfire | New write token verification and old-token revocation are separate; preserve project logs. No reason to emit sensitive test telemetry. |
| CI/buckets | Reconcile independent GitHub secrets, provider admin credentials, bucket users, registry auth, and installed-app access. No CI/provider bootstrap from old potentially exposed tokens. |

## Production MCP routing

Production MCP delivery is authorized for the scoped OAuth-only target. The manifest
permits its Clerk OAuth client configuration, base URL, and required integration
credentials. It rejects the internal bearer token and Clerk backend admin key for
production MCP, including historical captured candidates. Custody and delivery
allowlists are separate: retaining an old candidate does not permit installing it.
Use the [incident tracker](../incidents/2026-10-21/README.md) for actual cutover and
runtime verification; an authorized manifest target alone is not deployment proof.

## Offline checks

```sh
python3 -m unittest discover -s api/src/tests -p test_credential_recovery.py -v
git diff --check
```

Tests use synthetic credentials, isolated temporary stores, and blocked sockets.
They cover leaks, permissions/symlinks/hardlinks, concurrent lock refusal, input
parsing, environment routing, idempotency, immutable captures, and pair handling.
These checks do not establish live provider or deployment readiness.

## Provider and incident adapters

The reusable custody CLI is offline. Fixed-target provider mutation and installation
adapters are cataloged in [dated incident tooling](../../scripts/incidents/README.md).
Read the owning tracker and operation journal before running one. A new cycle label
alone does not authorize a new mutation or establish that a historical target is
still correct.
