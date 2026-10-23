# October 2026 eMudhra/emSign trust audit

Audited 2026-10-22 from freshly fetched `origin/main` at
`58bd239e6ec4280f0db6e1cb6bee541429e84611`. **This is a source/build remediation,
not a claim that production or existing developer/cloud environments are fixed.**
No production deploy/restart, provider configuration change, credential rotation,
or Dependabot alert dismissal was performed. No evidence of interception was
identified in this bounded repository/build audit; it was not a forensic review.

## Official guidance checked

- [Python Security guidance](https://discuss.python.org/t/information-for-python-users-regarding-emudhra-emsign-ca-certificate-security-incident/109576): certifi **2026.10.19**, pip **26.2.2**, uv **0.13.4** minimum.
- [certifi advisory](https://github.com/certifi/python-certifi/security/advisories/GHSA-6q48-6c7w-9gw2): 2026.10.18 first removed roots; 2026.10.19 preserves unaffected emSign roots.
- [pip release notes](https://github.com/pypa/pip/blob/26.2.2/NEWS.rst): its **vendored** certifi is updated to 2026.10.19. Updating application certifi or OS roots does not repair old pip.
- [uv advisory](https://github.com/astral-sh/uv/security/advisories/GHSA-hq85-5cpj-mr62): uv has an independent bundled trust store; native/custom trust requires a separately fixed OS/custom bundle.
- [Node security release](https://nodejs.org/en/blog/vulnerability/october-2026-security-releases) and [22.23.4 notes](https://nodejs.org/en/blog/release/v22.23.4): fixed bundled roots and compromised-key rejection. Local Node 22.23.2 contained G3, so this directly affected the same repository's web/install tooling.

## Surfaces and durable changes

| Surface | Before | Remediation |
|---|---|---|
| Root and standalone MCP `uv.lock` | Both already had certifi 2026.10.19 | Retain it; add a resolver floor without adding an unused direct dependency; require uv >=0.13.4 |
| API single-stage image | `python:3.11-slim`, floating uv `latest`, unfrozen sync, inherited pip | Bookworm Python 3.11 retained; refreshed OS CA package with explicit G3 distrust; hash-pinned pip bootstrap; uv 0.13.4 before venv/sync; `--locked`; base and venv checked |
| MCP builder and final image | Floating uv/Python builder; separate runtime with unchecked base pip/OS roots | Common patched `trusted-python` base; pinned uv only in builder; verify final venv and base interpreter independently |
| Cursor snapshot | Ubuntu 22.04, Node 20, floating uv, ordinary pip upgrade, seeded venv pip | G3 distrust before HTTPS bootstrap; Node 22 with trust check; pinned uv; hash-pinned pip for both Python 3.10/3.11 base interpreters and seeded venv; snapshot rebuild still required |
| Python test CI | Unpinned setup-uv, reusable caches, one unfrozen sync | uv 0.13.4, new `emsign-2026-10-22` cache namespace, explicit Python/pip bootstrap, OS distrust, pre/post-install checks, locked installs |
| Scheduled Logfire workflow | `pip install requests` using setup-python's inherited pip | Hash-pinned pip bootstrap first, then requests with certifi floor and trust check |
| Claude/Codex/Cursor setup and worktree provisioner | Assumed host/snapshot installers were safe | Fail closed on outdated installers/active bad roots **before Python dependency downloads**; locked cloud syncs |
| Web Docker base/deps/build/final stages | Node 22.23.2 inherited by all stages | Node 22.23.4 plus OS CA refresh/distrust and bundled/effective-root check before Corepack/pnpm; all stages inherit it |
| Expo Docker stage | Node 20, unpinned global pnpm, missing patch inputs | Same Node 22.23.4 trust base; Corepack uses repository package-manager pin; copy existing patch/config inputs |
| Local Node / frontend CI | `.nvmrc` major 22, engines >=22.22 | `.nvmrc` 22.23.4, matching engine minimum and CI version; launcher refuses old/bad bundled roots |
| Railway config | API/MCP/web Docker contexts, source watch patterns | Preserve contexts and runtime settings; watch shared trust helpers where needed; no provider changes |

Other workflows were inspected: Neon PR lifecycle and automation policy use Python
stdlib; sync-dev uses Git/shell. They do not install Python packages. GitHub's own
action runtimes and hosted-runner base snapshots remain GitHub's responsibility.
`docker-compose.yml` uses these Dockerfiles and PostgreSQL 17 (CI uses PostgreSQL
16); neither database image installs this application's Python environment.
No conda, pixi, sigstore, or KSMBD server provisioning was found in tracked source.
**Application rebuilds do not patch a host kernel; host KSMBD exposure belongs to
infrastructure owners.**

## Effective CA paths and local evidence

Clean API, MCP, web, Expo and Cursor builds used `docker build --pull --no-cache`, TLS verification
intact. Final runtime containers were inspected with `--network none`, without
application startup, migrations, credentials, volumes, or provider traffic.

- Python images: Python 3.11.17; OpenSSL file `/usr/lib/ssl/cert.pem` and directory
  `/usr/lib/ssl/certs`, 148 active roots, G3 absent.
- Both final app venvs: certifi 2026.10.19 at
  `/app/.venv/lib/python3.11/site-packages/certifi/cacert.pem`, 120 roots, G3 absent.
  httplib2 resolves to that same file. Requests/httpx use certifi by default;
  httpx2 uses OS `truststore` by default. API botocore's effective `where()` also
  resolves to certifi (its shipped fallback file is inactive while certifi exists).
- Both Python base images initially supplied **pip 24.0**, even after pulling fresh.
  Both final base interpreters: pip 26.2.2; **pip vendored certifi 2026.10.19** at
  `/usr/local/lib/python3.11/site-packages/pip/_vendor/certifi/cacert.pem`, 120 roots.
  App venvs do not contain pip. API uv is 0.13.4; MCP runtime contains no uv.
- Cursor final image: inherited Ubuntu Python 3.10.12 and 3.11.0rc1 plus its
  seeded venv all have pip 26.2.2 / vendored certifi 2026.10.19, uv 0.13.4 and
  120 active directory-backed system roots without G3. Node is 22.23.4. The
  preexisting Ubuntu prerelease Python 3.11 package remains a separate development
  image modernization gap; only its trust/bootstrap behavior was validated here,
  not full Cursor project startup or its browser-install layout.
- Worktree app venvs: Python 3.11.11, certifi 2026.10.19, no pip; Homebrew OpenSSL
  bundle `/opt/homebrew/etc/openssl@3/cert.pem` has 183 roots and no G3.
  A separate temporary tool venv was bootstrapped with the PSF hash-pinned wheel:
  pip 26.2.2 / vendored certifi 2026.10.19 / uv 0.13.4. The preexisting global uv
  0.7.2 was **not** overwritten; use a fixed installer before future provisioning.
- Node 22.23.2's bundled roots contained G3. Official Node 22.23.4 was installed
  alongside it with nvm's SHA-256 verification; bundled/effective default roots
  contain 116 certificates and no G3. Web/Expo final Alpine OS bundles contain
  118 certificates and no G3. The shell's global default was not changed.
- macOS `/etc/ssl/cert.pem` still contains G3. It is separate from the inspected
  Homebrew Python bundle and Node's bundled defaults. macOS Keychain/native
  `truststore`, other host interpreters, and unrelated environments were not
  remediated; their owners must apply OS/runtime updates.

Checks performed: backend suite **944 passed, 6 skipped, 113 deselected**; standalone
MCP **333 passed, 4 deselected**; worktree lifecycle **29 passed**; offline trust
guards **5 passed**; JS security/consumer regressions **12 passed**; web tests,
typecheck and production build. Live provider tests were not run. Tests cover the
existing braces/node-forge patches, Metro assets, Expo's xcode/uuid integration,
and Mermaid's KaTeX integration. Browser QA uses the local architecture page;
a Mermaid auto-render/hydration race was fixed by rendering from React's effect.

## Dependabot disposition

No open Dependabot PRs existed. GitHub reported **120 open alerts** across 37
packages. Targeted uv/pnpm resolution fixes **112 alert records** by version;
compatible pins and existing patches are preserved. No lockfiles were hand-edited.
Security floors stop later targeted resolves from silently downgrading fixes.

Python updates include pypdf, graphql-core, aiohttp, multidict, fsspec, apprise,
strawberry-graphql, oauthlib, hpack/h2, pydantic-settings, prefect, pytest, authlib
(MCP), and OpenTelemetry. The OTel fix requires matching SDK/exporter/instrumentation
versions, Logfire 5.2.0 and Mistral SDK 3.3.0; the resolver cannot retain Mistral
2.3.1 because it pins the old OTel semantic conventions. Prefect 3.6.28 requires
pydocket >=0.19.0. FastAPI remains at its original 0.135.3. PydanticAI/graph/evals
advance only to **1.107.6**, resolving fourteen alerts while retaining v1 APIs.

JS updates include DOMPurify, MCP SDK, Vite, Mermaid, KaTeX, compression, selector
parser, source-map-js, AI SDK provider-utils, qs, humanfs, Hono's Node server,
body-parser, Babel core, ip-address and uuid. KaTeX and uuid consumer compatibility
is covered by focused tests rather than assuming a cross-major override is safe.

Eight alert records remain visible by version; **do not dismiss them wholesale**:

| Alerts | Status / smallest follow-up |
|---|---|
| #660, #661 (`pydantic-ai` / slim; GHSA-v36g-jcw9-x7cw) | Fixed releases 1.107.7–1.107.9 require `genai-prices<0.1`, conflicting with the existing `genai-prices>=0.1.1` pricing fix. No metadata override, pricing downgrade, or v2 migration. Current Sernia `WebFetch(local=False)` (covered by `test_sernia_agent_wiring.py`) disables the advisory's local HTML conversion path; preserve that guard. Await a compatible v1 backport or deliberately test a v2 migration. |
| #508 (`decode-uri-component`) | Fixed 0.5.0 is ESM; `query-string@7.1.3` in React Navigation requires a callable CommonJS export. Needs a coordinated navigation/query-string migration or reviewed backport, not a blind override. |
| #532, #643, #644 (`stream-json`) | Fixed 3.6.0 changes module format, exported paths and streaming API. Solana's `jayson@4.3.0` requires `stream-json/streamers/StreamValues` and `utils/Verifier`; upgrading it blindly breaks module loading. Coordinate upstream jayson/Solana update or backport. |
| #642 (`braces`), #641 (`node-forge`) | No upstream fixed versions listed. Existing repository pnpm patches remain applied and their exploit regression tests pass; these are locally mitigated, not version-resolved. |

## Exact locked versions for alerted dependencies

These rows compare the fetched main baseline with this branch, including unresolved
versions. The pnpm lockfile also changes peer keys and uv updates its lock revision.

| Lockfile | Package | Before | After |
|---|---|---|---|
| `apps/sernia_mcp/uv.lock` | `authlib` | 1.7.0 | 1.7.1 |
| `apps/sernia_mcp/uv.lock` | `opentelemetry-api` | 1.40.0 | 1.45.1 |
| `apps/sernia_mcp/uv.lock` | `pydantic-settings` | 2.14.0 | 2.14.2 |
| `apps/sernia_mcp/uv.lock` | `pypdf` | 6.19.0 | 6.21.1 |
| `pnpm-lock.yaml` | `@ai-sdk/provider-utils` | 3.0.23 | 3.0.28 |
| `pnpm-lock.yaml` | `@babel/core` | 7.29.0, 7.29.7 | 7.29.6, 7.29.7 |
| `pnpm-lock.yaml` | `@hono/node-server` | 1.19.13 | 1.19.15 |
| `pnpm-lock.yaml` | `@humanfs/node` | 0.16.7 | 0.16.8 |
| `pnpm-lock.yaml` | `@modelcontextprotocol/sdk` | 1.29.0 | 1.32.0 |
| `pnpm-lock.yaml` | `body-parser` | 1.20.4, 2.2.2 | 1.20.6, 2.3.0 |
| `pnpm-lock.yaml` | `braces` | 3.0.3 | 3.0.3 |
| `pnpm-lock.yaml` | `compression` | 1.8.1 | 1.8.2 |
| `pnpm-lock.yaml` | `decode-uri-component` | 0.2.2 | 0.2.2 |
| `pnpm-lock.yaml` | `dompurify` | 3.3.3 | 3.4.17 |
| `pnpm-lock.yaml` | `ip-address` | 10.7.3 | 10.7.4 |
| `pnpm-lock.yaml` | `katex` | 0.16.45 | 0.18.2 |
| `pnpm-lock.yaml` | `mermaid` | 11.14.0 | 11.16.1 |
| `pnpm-lock.yaml` | `node-forge` | 1.4.0 | 1.4.0 |
| `pnpm-lock.yaml` | `postcss-selector-parser` | 7.1.1 | 7.1.6 |
| `pnpm-lock.yaml` | `qs` | 6.14.2, 6.15.0 | 6.16.0 |
| `pnpm-lock.yaml` | `source-map-js` | 1.2.1 | 1.2.2 |
| `pnpm-lock.yaml` | `stream-json` | 1.9.1 | 1.9.1 |
| `pnpm-lock.yaml` | `uuid` | 11.1.0, 7.0.3, 8.3.2 | 11.1.1, 14.0.3 |
| `pnpm-lock.yaml` | `vite` | 7.3.6 | 7.3.7 |
| `uv.lock` | `aiohttp` | 3.14.3 | 3.14.4 |
| `uv.lock` | `apprise` | 1.9.9 | 1.11.0 |
| `uv.lock` | `fsspec` | 2026.3.0 | 2026.6.0 |
| `uv.lock` | `graphql-core` | 3.2.8 | 3.2.12 |
| `uv.lock` | `h2` | 4.3.0 | 4.4.1 |
| `uv.lock` | `hpack` | 4.1.0 | 4.2.0 |
| `uv.lock` | `multidict` | 6.7.1 | 6.9.1 |
| `uv.lock` | `oauthlib` | 3.3.1 | 4.0.0 |
| `uv.lock` | `opentelemetry-api` | 1.39.1 | 1.45.1 |
| `uv.lock` | `prefect` | 3.6.25 | 3.6.28 |
| `uv.lock` | `pydantic-ai` | 1.107.1 | 1.107.6 |
| `uv.lock` | `pydantic-ai-slim` | 1.107.1 | 1.107.6 |
| `uv.lock` | `pydantic-settings` | 2.13.1 | 2.14.2 |
| `uv.lock` | `pypdf` | 6.19.0 | 6.21.1 |
| `uv.lock` | `pytest` | 9.0.2 | 9.0.3 |
| `uv.lock` | `strawberry-graphql` | 0.313.0 | 0.327.2 |

## Clean rebuild / redeploy checklist (requires separate authorization)

1. Recheck the official advisories at execution time. Review this PR and its CI
   results before merge. A merge alone does not change a running process/image.
2. Retire old CI caches (`setup-uv` previous namespace), Docker/BuildKit cached
   layers and cache exports, Railway build caches, and Cursor/cloud snapshots.
   Do not broadly delete shared caches or other developers' environments without
   identifying ownership. The new CI suffix avoids restoring old uv caches.
3. Pull fresh base images and rebuild **each** relevant image without cached layers:

   ```sh
   docker build --pull --no-cache -f api/Dockerfile -t portfolio-api:trust-fixed .
   docker build --pull --no-cache -f apps/sernia_mcp/Dockerfile -t portfolio-mcp:trust-fixed apps/sernia_mcp
   docker build --pull --no-cache -f apps/web-react-router/Dockerfile -t portfolio-web:trust-fixed .
   docker build --pull --no-cache -f apps/my-expo-app/Dockerfile -t portfolio-expo:trust-fixed .
   docker build --pull --no-cache -f .cursor/Dockerfile -t portfolio-cursor:trust-fixed .
   ```

   For Railway, explicitly select a fresh build with build cache disabled and
   refreshed base images; verify those settings and logs, not just "redeploy" an
   existing artifact. Build for the deployment architecture; local image evidence
   here is Linux **arm64**, not a claim about a Railway amd64 artifact.
4. Inspect the **final images**, both app and base Python, before any startup:

   ```sh
   docker run --rm --network none portfolio-api:trust-fixed python /opt/python-trust/check_python_trust.py --require-certifi --require-uv
   docker run --rm --network none portfolio-api:trust-fixed /usr/local/bin/python /opt/python-trust/check_python_trust.py --require-uv
   docker run --rm --network none portfolio-mcp:trust-fixed python /opt/python-trust/check_python_trust.py --require-certifi
   docker run --rm --network none portfolio-mcp:trust-fixed /usr/local/bin/python /opt/python-trust/check_python_trust.py
   docker run --rm --network none portfolio-web:trust-fixed node /opt/check-node-trust.cjs
   docker run --rm --network none portfolio-expo:trust-fixed node /opt/check-node-trust.cjs
   ```

5. Audit deployment-supplied CA overrides/mounted trust stores privately. Build-time
   checks cannot see them. Refresh custom roots without dropping required enterprise
   CAs; verify effective Requests, httplib2, botocore, HTTPX/HTTPX2 and pip paths.
6. Record immutable image digests, deploy only after authorization, and verify
   running interpreter/installer versions, trust paths and health checks for
   API/MCP/web in each environment. API startup runs migrations: don't casually
   start the container against a production database just to inspect packages.
7. Recreate old venvs or bootstrap their pip **before** using them. Python's embedded
   `ensurepip` wheel may seed an old pip even after base pip was updated. Use the
   hash-pinned helper for each seeded venv; ordinary uv venvs have no pip by default.
   Upgrade host uv through a verified official release or a fixed pip in a separate
   tooling venv. Run `nvm install` for the repository's pinned Node version.
8. Rebuild and replace Cursor snapshots; refresh Claude/Codex managed images or
   their installers/system CA store before retrying the fail-closed setup guard.
   Existing shared snapshots, production containers, host kernel/Keychain and
   all environments outside this worktree remain coverage gaps until verified.

The Cursor and Expo images are local validation artifacts, not deployments. Source checks are not a substitute for their final-image tests
or macOS/host OS remediation. No secret rotation is warranted solely by finding
outdated trust tooling; separately investigate any concrete interception evidence.
