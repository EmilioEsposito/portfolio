# eMudhra CA response — October 20, 2026

This is **partial remediation**, pending review, rebuild, and deployment. It is not a
production all-clear. The incident concerns outbound TLS trust, not evidence that this
repository's own private keys were stolen.

## Verified fixes and scope

- [Mozilla incident](https://blog.mozilla.org/security/2026/10/20/protecting-firefox-users-following-the-compromise-of-emudhra-intermediate-certificates/): eight compromised intermediate CA keys.
- [certifi advisory](https://github.com/certifi/python-certifi/security/advisories/GHSA-6q48-6c7w-9gw2): initial fix 2026.10.18. Use
  [2026.10.19](https://github.com/certifi/python-certifi/commit/662df63), which restores unaffected emSign roots.
- [uv advisory](https://github.com/astral-sh/uv/security/advisories/GHSA-hq85-5cpj-mr62): 0.13.4 updates its separate bundled CA store.

The root lockfile moves certifi from 2026.2.25 to 2026.10.19; the independent Sernia MCP
lockfile moves from 2026.4.22. Other locked packages and resolution markers are unchanged.
Docker builds, the repository's Cursor image, and both CI setup-uv steps pin uv 0.13.4.
The standalone Logfire workflow also requires certifi >=2026.10.19 when installing requests.
FastAPI Docker/CI installation now uses the frozen lockfile. Existing Python versions and
Debian variants are preserved. No trust overrides, TLS bypasses, credentials, or provider
configuration changes are included.

## Trust paths and remaining exposure

| Consumer | Trust source | What this change covers |
|---|---|---|
| requests, httpx 0.28, httplib2 with no custom CA configuration | certifi | Patched after reinstall/rebuild and process restart |
| TypeSafe inference via httpx2/httpcore2 | OS truststore by default | **Unpatched by certifi**; needs verified OS/base-image remediation |
| Python ssl, aiohttp, urllib, runtime git/curl | OS/OpenSSL store | **Unpatched by certifi** |
| React Router Node, Expo build/runtime, Node-based CI actions | Node/other bundled store | **No patched Node release verified**; follow [Node PR 66871](https://github.com/nodejs/node/pull/66871) |
| uv package downloads | uv's bundled roots unless overridden | Patched by 0.13.4; system/custom overrides need their own audit |

`api/src/google/common/ca_bundle.py` honors `HTTPLIB2_CA_CERTS`, `SSL_CERT_FILE`, and
`REQUESTS_CA_BUNDLE`. Requests also honors `CURL_CA_BUNDLE`; httpx/httpx2 honor
`SSL_CERT_FILE`/`SSL_CERT_DIR`. Updating certifi does not repair those external bundles.
Node's `NODE_EXTRA_CA_CERTS` and `--use-system-ca` add trust; they do not remove its
compromised bundled trust. Do not use them as this incident's workaround.
GitHub Actions' embedded Node runtimes and the pip bootstrap's own trust are separate
provider/toolchain exposures; installing the patched application bundle does not fix them.

Read-only Railway evidence on October 20: FastAPI deployment
`f5d123bc-c6eb-4ef4-b396-f82bb994a323`, commit
`17d1bfffb40ac119bc63117537d71c7ac6ad400c`, logged `certifi==2026.2.25`.
FastAPI and React Router production configuration listed no CA override variable names.
Sernia MCP is not instantiated in production; development tracks `dev` and runs deployment
`247d6228-095e-478c-92f8-72cd8d6ac3b2`, with no CA override variables listed.
Railway's two legacy cron functions use `ghcr.io/railwayapp/function-bun:1.2.5`, not this
repository's Dockerfiles; their trust remediation requires a separate provider review.
The separate resume repository is outside this change.

A freshly pulled official `python:3.13-slim-bookworm` image (manifest
`sha256:a1165e272e578941b84abc79e4ab38a0305cd12803a5c4247979ac7655f4d641`)
still trusts emSign ECC Root CA-G3 and the AAA RSA root relevant to cross-sign paths.
This is evidence about the Bookworm image family, not an assertion that every deployed
image is byte-identical. Matching public-key hashes were checked against
[Homebrew's vendor response](https://github.com/Homebrew/homebrew-core/blob/main/Formula/c/ca-certificates.rb).
Absence of one root is not proof that every alternate chain is rejected.
Direct deployed-container inventory was not performed: this executor has no existing
Railway SSH identity, and creating/registering one is outside the authorized scope.
The official Debian Bookworm security package index still offers
`ca-certificates 20250419~deb12u1`; no incident-specific Debian package fix was verified.

## Existing preview access boundaries

Full-data branching is an intentional testing feature, not a defect. A normal Neon branch
is an isolated copy-on-write database: writes do not change the parent. The workflow does
not specify `parent_branch`, so it uses the project's default. Read-only metadata on
October 20 identifies that default as `production` (`br-small-forest-af9ve5ur`) in
`sweet-butterfly-10388995`, not the separately named `development` branch. Existing
`pr-329-claude/trusting-gates-p0qx8q` has that same production parent and a two-week expiry.

The parent is unprotected. Neon documents that normal branches inherit Postgres roles and
passwords in this case; the action retrieves the existing `neondb_owner` role/password and
constructs connection strings for the new endpoint. It does not create a new database user,
invite an account, rotate credentials or grant new membership. The project-member inventory
shows one admin. URLs are passed through Actions into the corresponding existing Railway
project's PR environment, with credential values kept out of logs. See
[Neon branch isolation and role inheritance](https://neon.com/docs/manage/branches) and the
[official action implementation](https://github.com/neondatabase/create-branch-action/blob/v6/src/branch.ts).

Railway creates the normal public preview hostname and inherits its configured application
variables. The CA patch does not change authentication or access rules. The existing PR #329
API returned 401 for unauthenticated Sernia conversation-history access; code applies Clerk
sign-in plus verified Sernia-domain checks to Sernia and contact routes. This is a bounded
check, not a complete application authorization audit. No customer rows or credential values
were retrieved for this assessment.

One distinct provisioning detail remains relevant to a strict no-new-credentials constraint:
the existing Railway PR environment includes `ci-bucket`. Railway's
[documented bucket model](https://docs.railway.com/storage-buckets#buckets-in-environments)
provides isolated credentials per environment, so normal bucket provisioning can generate a
new preview storage key pair without adding a new project member. This is separate from
Neon's inherited database password. No bucket keys were requested or modified in this review.
Preserve the existing preview design; resolve any approval needed for that automatic
credential lifecycle explicitly rather than replacing full-data branching.

## Validation and release sequence

Local frozen installs passed. Sernia MCP: 264 tests passed, four live tests deselected.
The five existing Google CA-selection tests passed using a synthetic localhost database URL.
Read-only SPKI inspection of installed certifi, requests, httplib2 and httpx defaults
found none of the three relevant vendor-listed root keys in the patched bundle.
httpx2 was independently confirmed to construct a system `truststore.SSLContext`.
Full Docker builds remain unverified: this executor's Docker network cannot resolve
package hosts. No TLS checks were disabled to work around that failure.

1. Review this focused branch separately from unrelated dependency work. Preserve the
   repository's intentional full-data Neon preview lifecycle; this CA change does not
   alter preview triggers, branch type, users, grants or application authorization.
2. After review and Railway build recovery, validate a permitted preview. Freshly rebuild
   FastAPI and Sernia MCP from the reviewed SHA with current base images, without reusing
   old dependency layers. Capture image digest, uv version, installed certifi version,
   default TLS contexts and actual OS/custom CA bundles.
3. Keep OS/httpx2/Node exposure explicitly open until a vendor fix is verified for each
   path. A certifi-only preview success does not close those gaps.
4. Only after approval, merge/deploy through the normal controls. FastAPI production tracks
   main, so merging is itself a release boundary. Verify terminal deployment success and
   exact commit, health endpoint and read-only provider TLS checks; restart all workers
   so old SSL contexts and persistent sessions are discarded.
5. Retain the previous artifact for availability recovery, but label it CA-vulnerable;
   rolling back requires an explicit security decision, not an automatic all-clear.
