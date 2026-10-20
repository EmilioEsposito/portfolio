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

## Validation and release sequence

Local frozen installs passed. Sernia MCP: 264 tests passed, four live tests deselected.
The five existing Google CA-selection tests passed using a synthetic localhost database URL.
Read-only SPKI inspection of installed certifi, requests, httplib2 and httpx defaults
found none of the three relevant vendor-listed root keys in the patched bundle.
httpx2 was independently confirmed to construct a system `truststore.SSLContext`.
Full Docker builds remain unverified: this executor's Docker network cannot resolve
package hosts. No TLS checks were disabled to work around that failure.

1. Review this focused branch separately from the pending unrelated dependency work.
   Opening a draft PR currently runs `neon_workflow.yml`, including `branch_type: default`
   database copying; obtain approval for that side effect or a separately reviewed safe
   preview policy before opening it. Do not weaken CI or race-cancel provisioning.
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
