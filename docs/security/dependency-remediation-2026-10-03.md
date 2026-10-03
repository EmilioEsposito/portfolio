# Dependency security remediation — 2026-10-03

Live paginated GitHub Dependabot inventory: **147 high/critical alerts** (140 high,
7 critical), across the root Python project, standalone Sernia MCP service, and
pnpm workspace. Every affected range listed below is absent from the proposed
lockfiles. GitHub alerts will remain open until this change reaches the default
branch and GitHub refreshes its dependency graph; none were dismissed.

## Compatibility and temporary patches

- Clerk Python SDK 7 is required to permit patched cryptography 50; SDK 5 pins
  cryptography below 47. Backend authorization/import tests passed with SDK 7.
- `image-size` 2 fixes the high-severity image parser alerts. Metro 0.82.5 needs a
  small named-export and file-buffer compatibility patch; a real mobile PNG regression exercises
  the installed Metro path. Remove this patch when upgrading Metro to a release
  that supports image-size 2 directly.
- An additional current npm audit finding, [GHSA-86w9-cpqp-85rv](https://github.com/advisories/GHSA-86w9-cpqp-85rv),
  affects Expo's transitive `node-forge` 1.4.0. The advertised npm fix 1.4.1 was not
  published at verification time. `patches/node-forge@1.4.0.patch` applies the minimal
  nested `DigestAlgorithm` element-count validation proposed in
  [upstream PR 1152](https://github.com/digitalbazaar/forge/pull/1152).
  This is a temporary locally maintained mitigation, not an upstream released fix.
  Regression tests fail on unpatched 1.4.0 and pass with the patch, covering valid
  signatures and extra nested elements both with and without NULL parameters.
  Replace it with a verified upstream release when available. Version-only npm
  audit still reports this one high advisory; it is not suppressed or dismissed.

## Validation

- Backend: 800 passed, 6 skipped, 113 live tests deselected; isolated local database.
- Standalone MCP: 264 passed, 4 live tests deselected.
- Web: 18 tests passed; typecheck and production build passed.
- Security regressions: 4 passed (RSA validation and Metro image asset decoding).
- `uv` and pnpm frozen lockfile installs passed; Ruff check and format check passed.
- Full Expo iOS export remains blocked by `Chunk containing module not found:
  undefined`. The identical assertion reproduces from an unchanged `b838009`
  source archive using its original frozen lockfile and placeholder configuration.
  Both patched Metro buffer/path asset regressions pass; mobile export is not
  represented as passing.
- Current npm audit: zero critical, one high (patched node-forge as described above).
- Python pip-audit still reports lower-severity issues outside this high/critical
  scope; this is not a claim that all dependencies are vulnerability-free.
- No production requests, exploit testing, credential changes, alert dismissals,
  merges, or production deployment were performed.

## Exact alert verification

Each locked version was compared against the live alert's vulnerable range.

| Alert | Severity | Manifest | Package | Locked versions | Advisory |
|---|---|---|---|---|---|
| [#562](https://github.com/EmilioEsposito/portfolio/security/dependabot/562) | critical | `apps/sernia_mcp/uv.lock` | `anyio` | `4.14.2` | [GHSA-82r6-8w77-94w6](https://github.com/advisories/GHSA-82r6-8w77-94w6) |
| [#323](https://github.com/EmilioEsposito/portfolio/security/dependabot/323) | high | `apps/sernia_mcp/uv.lock` | `cryptography` | `50.0.2` | [GHSA-537c-gmf6-5ccf](https://github.com/advisories/GHSA-537c-gmf6-5ccf) |
| [#470](https://github.com/EmilioEsposito/portfolio/security/dependabot/470) | high | `apps/sernia_mcp/uv.lock` | `cryptography` | `50.0.2` | [GHSA-g6cj-pr64-35w5](https://github.com/advisories/GHSA-g6cj-pr64-35w5) |
| [#564](https://github.com/EmilioEsposito/portfolio/security/dependabot/564) | high | `apps/sernia_mcp/uv.lock` | `cryptography` | `50.0.2` | [GHSA-jwv3-5hgf-82ww](https://github.com/advisories/GHSA-jwv3-5hgf-82ww) |
| [#436](https://github.com/EmilioEsposito/portfolio/security/dependabot/436) | high | `apps/sernia_mcp/uv.lock` | `httplib2` | `0.32.0` | [GHSA-j5g9-f88f-gfj3](https://github.com/advisories/GHSA-j5g9-f88f-gfj3) |
| [#393](https://github.com/EmilioEsposito/portfolio/security/dependabot/393) | high | `apps/sernia_mcp/uv.lock` | `joserfc` | `1.7.5` | [GHSA-gg9x-qcx2-xmrh](https://github.com/advisories/GHSA-gg9x-qcx2-xmrh) |
| [#401](https://github.com/EmilioEsposito/portfolio/security/dependabot/401) | high | `apps/sernia_mcp/uv.lock` | `mcp` | `1.30.0` | [GHSA-hvrp-rf83-w775](https://github.com/advisories/GHSA-hvrp-rf83-w775) |
| [#403](https://github.com/EmilioEsposito/portfolio/security/dependabot/403) | high | `apps/sernia_mcp/uv.lock` | `mcp` | `1.30.0` | [GHSA-jpw9-pfvf-9f58](https://github.com/advisories/GHSA-jpw9-pfvf-9f58) |
| [#404](https://github.com/EmilioEsposito/portfolio/security/dependabot/404) | high | `apps/sernia_mcp/uv.lock` | `mcp` | `1.30.0` | [GHSA-vj7q-gjh5-988w](https://github.com/advisories/GHSA-vj7q-gjh5-988w) |
| [#414](https://github.com/EmilioEsposito/portfolio/security/dependabot/414) | high | `apps/sernia_mcp/uv.lock` | `pyasn1` | `0.6.4` | [GHSA-hm4w-wwcw-mr6r](https://github.com/advisories/GHSA-hm4w-wwcw-mr6r) |
| [#415](https://github.com/EmilioEsposito/portfolio/security/dependabot/415) | high | `apps/sernia_mcp/uv.lock` | `pyasn1` | `0.6.4` | [GHSA-8ppf-4f7h-5ppj](https://github.com/advisories/GHSA-8ppf-4f7h-5ppj) |
| [#455](https://github.com/EmilioEsposito/portfolio/security/dependabot/455) | high | `apps/sernia_mcp/uv.lock` | `pyasn1` | `0.6.4` | [GHSA-m4p7-r5rc-7g4j](https://github.com/advisories/GHSA-m4p7-r5rc-7g4j) |
| [#319](https://github.com/EmilioEsposito/portfolio/security/dependabot/319) | high | `apps/sernia_mcp/uv.lock` | `pyjwt` | `2.15.1` | [GHSA-xgmm-8j9v-c9wx](https://github.com/advisories/GHSA-xgmm-8j9v-c9wx) |
| [#584](https://github.com/EmilioEsposito/portfolio/security/dependabot/584) | high | `apps/sernia_mcp/uv.lock` | `pyjwt` | `2.15.1` | [GHSA-p4g4-x82p-q773](https://github.com/advisories/GHSA-p4g4-x82p-q773) |
| [#585](https://github.com/EmilioEsposito/portfolio/security/dependabot/585) | high | `apps/sernia_mcp/uv.lock` | `PyJWT` | `2.15.1` | [GHSA-9v7f-9g4p-ffgj](https://github.com/advisories/GHSA-9v7f-9g4p-ffgj) |
| [#586](https://github.com/EmilioEsposito/portfolio/security/dependabot/586) | critical | `apps/sernia_mcp/uv.lock` | `PyJWT` | `2.15.1` | [GHSA-ffc3-869f-jxw9](https://github.com/advisories/GHSA-ffc3-869f-jxw9) |
| [#425](https://github.com/EmilioEsposito/portfolio/security/dependabot/425) | high | `apps/sernia_mcp/uv.lock` | `pypdf` | `6.19.0` | [GHSA-5xf7-4p34-54qr](https://github.com/advisories/GHSA-5xf7-4p34-54qr) |
| [#426](https://github.com/EmilioEsposito/portfolio/security/dependabot/426) | high | `apps/sernia_mcp/uv.lock` | `pypdf` | `6.19.0` | [GHSA-g867-7843-wf8q](https://github.com/advisories/GHSA-g867-7843-wf8q) |
| [#625](https://github.com/EmilioEsposito/portfolio/security/dependabot/625) | high | `apps/sernia_mcp/uv.lock` | `pypdf` | `6.19.0` | [GHSA-qv6h-rv94-w285](https://github.com/advisories/GHSA-qv6h-rv94-w285) |
| [#626](https://github.com/EmilioEsposito/portfolio/security/dependabot/626) | high | `apps/sernia_mcp/uv.lock` | `pypdf` | `6.19.0` | [GHSA-5jq2-8x83-x246](https://github.com/advisories/GHSA-5jq2-8x83-x246) |
| [#627](https://github.com/EmilioEsposito/portfolio/security/dependabot/627) | high | `apps/sernia_mcp/uv.lock` | `pypdf` | `6.19.0` | [GHSA-fp3h-c4fm-7vvf](https://github.com/advisories/GHSA-fp3h-c4fm-7vvf) |
| [#628](https://github.com/EmilioEsposito/portfolio/security/dependabot/628) | high | `apps/sernia_mcp/uv.lock` | `pypdf` | `6.19.0` | [GHSA-g9cg-prrw-2r8q](https://github.com/advisories/GHSA-g9cg-prrw-2r8q) |
| [#629](https://github.com/EmilioEsposito/portfolio/security/dependabot/629) | high | `apps/sernia_mcp/uv.lock` | `pypdf` | `6.19.0` | [GHSA-jw7q-gvrg-4vj3](https://github.com/advisories/GHSA-jw7q-gvrg-4vj3) |
| [#630](https://github.com/EmilioEsposito/portfolio/security/dependabot/630) | high | `apps/sernia_mcp/uv.lock` | `pypdf` | `6.19.0` | [GHSA-w23x-9jrw-r45c](https://github.com/advisories/GHSA-w23x-9jrw-r45c) |
| [#631](https://github.com/EmilioEsposito/portfolio/security/dependabot/631) | high | `apps/sernia_mcp/uv.lock` | `pypdf` | `6.19.0` | [GHSA-php9-fj8v-98fj](https://github.com/advisories/GHSA-php9-fj8v-98fj) |
| [#632](https://github.com/EmilioEsposito/portfolio/security/dependabot/632) | high | `apps/sernia_mcp/uv.lock` | `pypdf` | `6.19.0` | [GHSA-v247-6f48-mgcj](https://github.com/advisories/GHSA-v247-6f48-mgcj) |
| [#256](https://github.com/EmilioEsposito/portfolio/security/dependabot/256) | high | `apps/sernia_mcp/uv.lock` | `python-multipart` | `0.0.32` | [GHSA-pp6c-gr5w-3c5g](https://github.com/advisories/GHSA-pp6c-gr5w-3c5g) |
| [#322](https://github.com/EmilioEsposito/portfolio/security/dependabot/322) | high | `apps/sernia_mcp/uv.lock` | `python-multipart` | `0.0.32` | [GHSA-5rvq-cxj2-64vf](https://github.com/advisories/GHSA-5rvq-cxj2-64vf) |
| [#396](https://github.com/EmilioEsposito/portfolio/security/dependabot/396) | high | `apps/sernia_mcp/uv.lock` | `soupsieve` | `2.10` | [GHSA-836r-79rf-4m37](https://github.com/advisories/GHSA-836r-79rf-4m37) |
| [#397](https://github.com/EmilioEsposito/portfolio/security/dependabot/397) | high | `apps/sernia_mcp/uv.lock` | `soupsieve` | `2.10` | [GHSA-2wc2-fm75-p42x](https://github.com/advisories/GHSA-2wc2-fm75-p42x) |
| [#265](https://github.com/EmilioEsposito/portfolio/security/dependabot/265) | high | `apps/sernia_mcp/uv.lock` | `urllib3` | `2.8.0` | [GHSA-mf9v-mfxr-j63j](https://github.com/advisories/GHSA-mf9v-mfxr-j63j) |
| [#266](https://github.com/EmilioEsposito/portfolio/security/dependabot/266) | high | `apps/sernia_mcp/uv.lock` | `urllib3` | `2.8.0` | [GHSA-qccp-gfcp-xxvc](https://github.com/advisories/GHSA-qccp-gfcp-xxvc) |
| [#589](https://github.com/EmilioEsposito/portfolio/security/dependabot/589) | high | `apps/sernia_mcp/uv.lock` | `urllib3` | `2.8.0` | [GHSA-8988-9cw3-xx77](https://github.com/advisories/GHSA-8988-9cw3-xx77) |
| [#590](https://github.com/EmilioEsposito/portfolio/security/dependabot/590) | high | `apps/sernia_mcp/uv.lock` | `urllib3` | `2.8.0` | [GHSA-vxq7-64xx-v4gw](https://github.com/advisories/GHSA-vxq7-64xx-v4gw) |
| [#247](https://github.com/EmilioEsposito/portfolio/security/dependabot/247) | high | `pnpm-lock.yaml` | `@clerk/clerk-expo` | `2.20.0` | [GHSA-w24r-5266-9c3c](https://github.com/advisories/GHSA-w24r-5266-9c3c) |
| [#248](https://github.com/EmilioEsposito/portfolio/security/dependabot/248) | high | `pnpm-lock.yaml` | `@clerk/clerk-js` | `5.128.0` | [GHSA-w24r-5266-9c3c](https://github.com/advisories/GHSA-w24r-5266-9c3c) |
| [#251](https://github.com/EmilioEsposito/portfolio/security/dependabot/251) | high | `pnpm-lock.yaml` | `@clerk/clerk-react` | `5.61.10` | [GHSA-w24r-5266-9c3c](https://github.com/advisories/GHSA-w24r-5266-9c3c) |
| [#231](https://github.com/EmilioEsposito/portfolio/security/dependabot/231) | critical | `pnpm-lock.yaml` | `@clerk/shared` | `3.48.0`, `4.31.0` | [GHSA-vqx2-fgx2-5wq9](https://github.com/advisories/GHSA-vqx2-fgx2-5wq9) |
| [#250](https://github.com/EmilioEsposito/portfolio/security/dependabot/250) | high | `pnpm-lock.yaml` | `@clerk/shared` | `3.48.0`, `4.31.0` | [GHSA-w24r-5266-9c3c](https://github.com/advisories/GHSA-w24r-5266-9c3c) |
| [#240](https://github.com/EmilioEsposito/portfolio/security/dependabot/240) | high | `pnpm-lock.yaml` | `@xmldom/xmldom` | `0.8.15` | [GHSA-j759-j44w-7fr8](https://github.com/advisories/GHSA-j759-j44w-7fr8) |
| [#241](https://github.com/EmilioEsposito/portfolio/security/dependabot/241) | high | `pnpm-lock.yaml` | `@xmldom/xmldom` | `0.8.15` | [GHSA-f6ww-3ggp-fr8h](https://github.com/advisories/GHSA-f6ww-3ggp-fr8h) |
| [#242](https://github.com/EmilioEsposito/portfolio/security/dependabot/242) | high | `pnpm-lock.yaml` | `@xmldom/xmldom` | `0.8.15` | [GHSA-x6wf-f3px-wcqx](https://github.com/advisories/GHSA-x6wf-f3px-wcqx) |
| [#243](https://github.com/EmilioEsposito/portfolio/security/dependabot/243) | high | `pnpm-lock.yaml` | `@xmldom/xmldom` | `0.8.15` | [GHSA-2v35-w6hq-6mfw](https://github.com/advisories/GHSA-2v35-w6hq-6mfw) |
| [#543](https://github.com/EmilioEsposito/portfolio/security/dependabot/543) | high | `pnpm-lock.yaml` | `@xmldom/xmldom` | `0.8.15` | [GHSA-4w3w-2rp5-g8jm](https://github.com/advisories/GHSA-4w3w-2rp5-g8jm) |
| [#544](https://github.com/EmilioEsposito/portfolio/security/dependabot/544) | high | `pnpm-lock.yaml` | `@xmldom/xmldom` | `0.8.15` | [GHSA-w2rr-34g9-rvrj](https://github.com/advisories/GHSA-w2rr-34g9-rvrj) |
| [#546](https://github.com/EmilioEsposito/portfolio/security/dependabot/546) | high | `pnpm-lock.yaml` | `@xmldom/xmldom` | `0.8.15` | [GHSA-93r5-fhx6-vmg9](https://github.com/advisories/GHSA-93r5-fhx6-vmg9) |
| [#547](https://github.com/EmilioEsposito/portfolio/security/dependabot/547) | high | `pnpm-lock.yaml` | `@xmldom/xmldom` | `0.8.15` | [GHSA-965w-775f-mr7g](https://github.com/advisories/GHSA-965w-775f-mr7g) |
| [#548](https://github.com/EmilioEsposito/portfolio/security/dependabot/548) | high | `pnpm-lock.yaml` | `@xmldom/xmldom` | `0.8.15` | [GHSA-x4fp-j954-r2f4](https://github.com/advisories/GHSA-x4fp-j954-r2f4) |
| [#549](https://github.com/EmilioEsposito/portfolio/security/dependabot/549) | high | `pnpm-lock.yaml` | `@xmldom/xmldom` | `0.8.15` | [GHSA-8344-3jmq-59r6](https://github.com/advisories/GHSA-8344-3jmq-59r6) |
| [#551](https://github.com/EmilioEsposito/portfolio/security/dependabot/551) | high | `pnpm-lock.yaml` | `@xmldom/xmldom` | `0.8.15` | [GHSA-27p8-2357-5qqv](https://github.com/advisories/GHSA-27p8-2357-5qqv) |
| [#552](https://github.com/EmilioEsposito/portfolio/security/dependabot/552) | high | `pnpm-lock.yaml` | `@xmldom/xmldom` | `0.8.15` | [GHSA-c7q8-3ch8-vqpv](https://github.com/advisories/GHSA-c7q8-3ch8-vqpv) |
| [#406](https://github.com/EmilioEsposito/portfolio/security/dependabot/406) | high | `pnpm-lock.yaml` | `brace-expansion` | `1.1.21`, `5.0.12` | [GHSA-3jxr-9vmj-r5cp](https://github.com/advisories/GHSA-3jxr-9vmj-r5cp) |
| [#444](https://github.com/EmilioEsposito/portfolio/security/dependabot/444) | high | `pnpm-lock.yaml` | `brace-expansion` | `1.1.21`, `5.0.12` | [GHSA-3jxr-9vmj-r5cp](https://github.com/advisories/GHSA-3jxr-9vmj-r5cp) |
| [#449](https://github.com/EmilioEsposito/portfolio/security/dependabot/449) | high | `pnpm-lock.yaml` | `brace-expansion` | `1.1.21`, `5.0.12` | [GHSA-mh99-v99m-4gvg](https://github.com/advisories/GHSA-mh99-v99m-4gvg) |
| [#456](https://github.com/EmilioEsposito/portfolio/security/dependabot/456) | high | `pnpm-lock.yaml` | `brace-expansion` | `1.1.21`, `5.0.12` | [GHSA-3jxr-9vmj-r5cp](https://github.com/advisories/GHSA-3jxr-9vmj-r5cp) |
| [#457](https://github.com/EmilioEsposito/portfolio/security/dependabot/457) | high | `pnpm-lock.yaml` | `brace-expansion` | `1.1.21`, `5.0.12` | [GHSA-mh99-v99m-4gvg](https://github.com/advisories/GHSA-mh99-v99m-4gvg) |
| [#459](https://github.com/EmilioEsposito/portfolio/security/dependabot/459) | high | `pnpm-lock.yaml` | `brace-expansion` | `1.1.21`, `5.0.12` | [GHSA-rgw5-rvv9-x895](https://github.com/advisories/GHSA-rgw5-rvv9-x895) |
| [#474](https://github.com/EmilioEsposito/portfolio/security/dependabot/474) | high | `pnpm-lock.yaml` | `brace-expansion` | `1.1.21`, `5.0.12` | [GHSA-rgw5-rvv9-x895](https://github.com/advisories/GHSA-rgw5-rvv9-x895) |
| [#596](https://github.com/EmilioEsposito/portfolio/security/dependabot/596) | high | `pnpm-lock.yaml` | `brace-expansion` | `1.1.21`, `5.0.12` | [GHSA-6j4f-fj2g-mc7p](https://github.com/advisories/GHSA-6j4f-fj2g-mc7p) |
| [#597](https://github.com/EmilioEsposito/portfolio/security/dependabot/597) | high | `pnpm-lock.yaml` | `brace-expansion` | `1.1.21`, `5.0.12` | [GHSA-6j4f-fj2g-mc7p](https://github.com/advisories/GHSA-6j4f-fj2g-mc7p) |
| [#599](https://github.com/EmilioEsposito/portfolio/security/dependabot/599) | high | `pnpm-lock.yaml` | `brace-expansion` | `1.1.21`, `5.0.12` | [GHSA-qhr7-859c-m2p7](https://github.com/advisories/GHSA-qhr7-859c-m2p7) |
| [#600](https://github.com/EmilioEsposito/portfolio/security/dependabot/600) | high | `pnpm-lock.yaml` | `brace-expansion` | `1.1.21`, `5.0.12` | [GHSA-qhr7-859c-m2p7](https://github.com/advisories/GHSA-qhr7-859c-m2p7) |
| [#514](https://github.com/EmilioEsposito/portfolio/security/dependabot/514) | high | `pnpm-lock.yaml` | `browserslist` | `4.29.3` | [GHSA-73wf-gq98-2v4g](https://github.com/advisories/GHSA-73wf-gq98-2v4g) |
| [#515](https://github.com/EmilioEsposito/portfolio/security/dependabot/515) | high | `pnpm-lock.yaml` | `browserslist` | `4.29.3` | [GHSA-c83g-rgw3-j3cx](https://github.com/advisories/GHSA-c83g-rgw3-j3cx) |
| [#417](https://github.com/EmilioEsposito/portfolio/security/dependabot/417) | high | `pnpm-lock.yaml` | `fast-uri` | `3.1.8` | [GHSA-4c8g-83qw-93j6](https://github.com/advisories/GHSA-4c8g-83qw-93j6) |
| [#418](https://github.com/EmilioEsposito/portfolio/security/dependabot/418) | high | `pnpm-lock.yaml` | `fast-uri` | `3.1.8` | [GHSA-v2hh-gcrm-f6hx](https://github.com/advisories/GHSA-v2hh-gcrm-f6hx) |
| [#476](https://github.com/EmilioEsposito/portfolio/security/dependabot/476) | high | `pnpm-lock.yaml` | `fast-uri` | `3.1.8` | [GHSA-7p8r-x3mc-p8w7](https://github.com/advisories/GHSA-7p8r-x3mc-p8w7) |
| [#504](https://github.com/EmilioEsposito/portfolio/security/dependabot/504) | high | `pnpm-lock.yaml` | `fast-uri` | `3.1.8` | [GHSA-v39h-62p7-jpjc](https://github.com/advisories/GHSA-v39h-62p7-jpjc) |
| [#505](https://github.com/EmilioEsposito/portfolio/security/dependabot/505) | high | `pnpm-lock.yaml` | `fast-uri` | `3.1.8` | [GHSA-q3j6-qgpj-74h6](https://github.com/advisories/GHSA-q3j6-qgpj-74h6) |
| [#528](https://github.com/EmilioEsposito/portfolio/security/dependabot/528) | high | `pnpm-lock.yaml` | `fast-uri` | `3.1.8` | [GHSA-jqff-g426-hqxp](https://github.com/advisories/GHSA-jqff-g426-hqxp) |
| [#529](https://github.com/EmilioEsposito/portfolio/security/dependabot/529) | high | `pnpm-lock.yaml` | `fast-uri` | `3.1.8` | [GHSA-f65p-4m7j-42xc](https://github.com/advisories/GHSA-f65p-4m7j-42xc) |
| [#582](https://github.com/EmilioEsposito/portfolio/security/dependabot/582) | high | `pnpm-lock.yaml` | `fast-uri` | `3.1.8` | [GHSA-qw65-cvwx-89v3](https://github.com/advisories/GHSA-qw65-cvwx-89v3) |
| [#351](https://github.com/EmilioEsposito/portfolio/security/dependabot/351) | high | `pnpm-lock.yaml` | `hono` | `4.13.12` | [GHSA-88fw-hqm2-52qc](https://github.com/advisories/GHSA-88fw-hqm2-52qc) |
| [#566](https://github.com/EmilioEsposito/portfolio/security/dependabot/566) | high | `pnpm-lock.yaml` | `image-size` | `2.0.4` | [GHSA-w3rx-r6r6-pgpr](https://github.com/advisories/GHSA-w3rx-r6r6-pgpr) |
| [#567](https://github.com/EmilioEsposito/portfolio/security/dependabot/567) | high | `pnpm-lock.yaml` | `image-size` | `2.0.4` | [GHSA-5p2g-fcmc-qvqq](https://github.com/advisories/GHSA-5p2g-fcmc-qvqq) |
| [#480](https://github.com/EmilioEsposito/portfolio/security/dependabot/480) | high | `pnpm-lock.yaml` | `ip-address` | `10.7.3` | [GHSA-mwp4-54f8-5fhr](https://github.com/advisories/GHSA-mwp4-54f8-5fhr) |
| [#282](https://github.com/EmilioEsposito/portfolio/security/dependabot/282) | high | `pnpm-lock.yaml` | `js-cookie` | `3.0.7` | [GHSA-qjx8-664m-686j](https://github.com/advisories/GHSA-qjx8-664m-686j) |
| [#433](https://github.com/EmilioEsposito/portfolio/security/dependabot/433) | high | `pnpm-lock.yaml` | `js-yaml` | `3.15.2`, `4.3.2` | [GHSA-52cp-r559-cp3m](https://github.com/advisories/GHSA-52cp-r559-cp3m) |
| [#434](https://github.com/EmilioEsposito/portfolio/security/dependabot/434) | high | `pnpm-lock.yaml` | `js-yaml` | `3.15.2`, `4.3.2` | [GHSA-52cp-r559-cp3m](https://github.com/advisories/GHSA-52cp-r559-cp3m) |
| [#488](https://github.com/EmilioEsposito/portfolio/security/dependabot/488) | high | `pnpm-lock.yaml` | `js-yaml` | `3.15.2`, `4.3.2` | [GHSA-5p4m-2wfm-xmqj](https://github.com/advisories/GHSA-5p4m-2wfm-xmqj) |
| [#489](https://github.com/EmilioEsposito/portfolio/security/dependabot/489) | high | `pnpm-lock.yaml` | `js-yaml` | `3.15.2`, `4.3.2` | [GHSA-5p4m-2wfm-xmqj](https://github.com/advisories/GHSA-5p4m-2wfm-xmqj) |
| [#553](https://github.com/EmilioEsposito/portfolio/security/dependabot/553) | high | `pnpm-lock.yaml` | `js-yaml` | `3.15.2`, `4.3.2` | [GHSA-2883-xcg3-v3hh](https://github.com/advisories/GHSA-2883-xcg3-v3hh) |
| [#554](https://github.com/EmilioEsposito/portfolio/security/dependabot/554) | high | `pnpm-lock.yaml` | `js-yaml` | `3.15.2`, `4.3.2` | [GHSA-2883-xcg3-v3hh](https://github.com/advisories/GHSA-2883-xcg3-v3hh) |
| [#502](https://github.com/EmilioEsposito/portfolio/security/dependabot/502) | high | `pnpm-lock.yaml` | `nanoid` | `3.3.19` | [GHSA-28wg-ghj8-5hjv](https://github.com/advisories/GHSA-28wg-ghj8-5hjv) |
| [#507](https://github.com/EmilioEsposito/portfolio/security/dependabot/507) | high | `pnpm-lock.yaml` | `nanoid` | `3.3.19` | [GHSA-2v37-7h3g-55p8](https://github.com/advisories/GHSA-2v37-7h3g-55p8) |
| [#516](https://github.com/EmilioEsposito/portfolio/security/dependabot/516) | high | `pnpm-lock.yaml` | `nanoid` | `3.3.19` | [GHSA-xwg4-73v4-xw9w](https://github.com/advisories/GHSA-xwg4-73v4-xw9w) |
| [#446](https://github.com/EmilioEsposito/portfolio/security/dependabot/446) | high | `pnpm-lock.yaml` | `postcss` | `8.5.28` | [GHSA-6g55-p6wh-862q](https://github.com/advisories/GHSA-6g55-p6wh-862q) |
| [#448](https://github.com/EmilioEsposito/portfolio/security/dependabot/448) | high | `pnpm-lock.yaml` | `postcss` | `8.5.28` | [GHSA-r28c-9q8g-f849](https://github.com/advisories/GHSA-r28c-9q8g-f849) |
| [#301](https://github.com/EmilioEsposito/portfolio/security/dependabot/301) | critical | `pnpm-lock.yaml` | `shell-quote` | `1.11.0` | [GHSA-w7jw-789q-3m8p](https://github.com/advisories/GHSA-w7jw-789q-3m8p) |
| [#419](https://github.com/EmilioEsposito/portfolio/security/dependabot/419) | high | `pnpm-lock.yaml` | `shell-quote` | `1.11.0` | [GHSA-395f-4hp3-45gv](https://github.com/advisories/GHSA-395f-4hp3-45gv) |
| [#420](https://github.com/EmilioEsposito/portfolio/security/dependabot/420) | high | `pnpm-lock.yaml` | `tar` | `7.5.22` | [GHSA-8x88-c5mf-7j5w](https://github.com/advisories/GHSA-8x88-c5mf-7j5w) |
| [#431](https://github.com/EmilioEsposito/portfolio/security/dependabot/431) | critical | `pnpm-lock.yaml` | `tar` | `7.5.22` | [GHSA-23hp-3jrh-7fpw](https://github.com/advisories/GHSA-23hp-3jrh-7fpw) |
| [#445](https://github.com/EmilioEsposito/portfolio/security/dependabot/445) | high | `pnpm-lock.yaml` | `tar` | `7.5.22` | [GHSA-r292-9mhp-454m](https://github.com/advisories/GHSA-r292-9mhp-454m) |
| [#377](https://github.com/EmilioEsposito/portfolio/security/dependabot/377) | high | `pnpm-lock.yaml` | `undici` | `6.29.0` | [GHSA-vmh5-mc38-953g](https://github.com/advisories/GHSA-vmh5-mc38-953g) |
| [#382](https://github.com/EmilioEsposito/portfolio/security/dependabot/382) | high | `pnpm-lock.yaml` | `undici` | `6.29.0` | [GHSA-hm92-r4w5-c3mj](https://github.com/advisories/GHSA-hm92-r4w5-c3mj) |
| [#383](https://github.com/EmilioEsposito/portfolio/security/dependabot/383) | high | `pnpm-lock.yaml` | `undici` | `6.29.0` | [GHSA-vxpw-j846-p89q](https://github.com/advisories/GHSA-vxpw-j846-p89q) |
| [#384](https://github.com/EmilioEsposito/portfolio/security/dependabot/384) | high | `pnpm-lock.yaml` | `undici` | `6.29.0` | [GHSA-vxpw-j846-p89q](https://github.com/advisories/GHSA-vxpw-j846-p89q) |
| [#460](https://github.com/EmilioEsposito/portfolio/security/dependabot/460) | high | `pnpm-lock.yaml` | `undici` | `6.29.0` | [GHSA-4cwx-7wf7-3272](https://github.com/advisories/GHSA-4cwx-7wf7-3272) |
| [#571](https://github.com/EmilioEsposito/portfolio/security/dependabot/571) | high | `pnpm-lock.yaml` | `undici` | `6.29.0` | [GHSA-w293-vg96-wgc3](https://github.com/advisories/GHSA-w293-vg96-wgc3) |
| [#580](https://github.com/EmilioEsposito/portfolio/security/dependabot/580) | high | `pnpm-lock.yaml` | `undici` | `6.29.0` | [GHSA-rfgv-xxqx-mfg5](https://github.com/advisories/GHSA-rfgv-xxqx-mfg5) |
| [#594](https://github.com/EmilioEsposito/portfolio/security/dependabot/594) | high | `pnpm-lock.yaml` | `undici` | `6.29.0` | [GHSA-rfgv-xxqx-mfg5](https://github.com/advisories/GHSA-rfgv-xxqx-mfg5) |
| [#338](https://github.com/EmilioEsposito/portfolio/security/dependabot/338) | high | `pnpm-lock.yaml` | `vite` | `7.3.6` | [GHSA-fx2h-pf6j-xcff](https://github.com/advisories/GHSA-fx2h-pf6j-xcff) |
| [#333](https://github.com/EmilioEsposito/portfolio/security/dependabot/333) | high | `pnpm-lock.yaml` | `ws` | `6.2.6`, `7.5.13`, `8.22.0` | [GHSA-96hv-2xvq-fx4p](https://github.com/advisories/GHSA-96hv-2xvq-fx4p) |
| [#334](https://github.com/EmilioEsposito/portfolio/security/dependabot/334) | high | `pnpm-lock.yaml` | `ws` | `6.2.6`, `7.5.13`, `8.22.0` | [GHSA-96hv-2xvq-fx4p](https://github.com/advisories/GHSA-96hv-2xvq-fx4p) |
| [#335](https://github.com/EmilioEsposito/portfolio/security/dependabot/335) | high | `pnpm-lock.yaml` | `ws` | `6.2.6`, `7.5.13`, `8.22.0` | [GHSA-96hv-2xvq-fx4p](https://github.com/advisories/GHSA-96hv-2xvq-fx4p) |
| [#468](https://github.com/EmilioEsposito/portfolio/security/dependabot/468) | high | `uv.lock` | `aiohttp` | `3.14.3` | [GHSA-cq5v-8q36-5273](https://github.com/advisories/GHSA-cq5v-8q36-5273) |
| [#560](https://github.com/EmilioEsposito/portfolio/security/dependabot/560) | critical | `uv.lock` | `anyio` | `4.14.2` | [GHSA-82r6-8w77-94w6](https://github.com/advisories/GHSA-82r6-8w77-94w6) |
| [#363](https://github.com/EmilioEsposito/portfolio/security/dependabot/363) | high | `uv.lock` | `cryptography` | `50.0.2` | [GHSA-537c-gmf6-5ccf](https://github.com/advisories/GHSA-537c-gmf6-5ccf) |
| [#469](https://github.com/EmilioEsposito/portfolio/security/dependabot/469) | high | `uv.lock` | `cryptography` | `50.0.2` | [GHSA-g6cj-pr64-35w5](https://github.com/advisories/GHSA-g6cj-pr64-35w5) |
| [#568](https://github.com/EmilioEsposito/portfolio/security/dependabot/568) | high | `uv.lock` | `cryptography` | `50.0.2` | [GHSA-jwv3-5hgf-82ww](https://github.com/advisories/GHSA-jwv3-5hgf-82ww) |
| [#536](https://github.com/EmilioEsposito/portfolio/security/dependabot/536) | high | `uv.lock` | `httpcore2` | `2.13.1` | [GHSA-7mj9-2mp8-4m2p](https://github.com/advisories/GHSA-7mj9-2mp8-4m2p) |
| [#435](https://github.com/EmilioEsposito/portfolio/security/dependabot/435) | high | `uv.lock` | `httplib2` | `0.32.0` | [GHSA-j5g9-f88f-gfj3](https://github.com/advisories/GHSA-j5g9-f88f-gfj3) |
| [#539](https://github.com/EmilioEsposito/portfolio/security/dependabot/539) | high | `uv.lock` | `httpx2` | `2.13.1` | [GHSA-8xx6-hgc6-gc2m](https://github.com/advisories/GHSA-8xx6-hgc6-gc2m) |
| [#235](https://github.com/EmilioEsposito/portfolio/security/dependabot/235) | high | `uv.lock` | `Mako` | `1.4.3` | [GHSA-v92g-xgxw-vvmm](https://github.com/advisories/GHSA-v92g-xgxw-vvmm) |
| [#254](https://github.com/EmilioEsposito/portfolio/security/dependabot/254) | high | `uv.lock` | `Mako` | `1.4.3` | [GHSA-2h4p-vjrc-8xpq](https://github.com/advisories/GHSA-2h4p-vjrc-8xpq) |
| [#400](https://github.com/EmilioEsposito/portfolio/security/dependabot/400) | high | `uv.lock` | `mcp` | `1.30.0` | [GHSA-hvrp-rf83-w775](https://github.com/advisories/GHSA-hvrp-rf83-w775) |
| [#402](https://github.com/EmilioEsposito/portfolio/security/dependabot/402) | high | `uv.lock` | `mcp` | `1.30.0` | [GHSA-jpw9-pfvf-9f58](https://github.com/advisories/GHSA-jpw9-pfvf-9f58) |
| [#405](https://github.com/EmilioEsposito/portfolio/security/dependabot/405) | high | `uv.lock` | `mcp` | `1.30.0` | [GHSA-vj7q-gjh5-988w](https://github.com/advisories/GHSA-vj7q-gjh5-988w) |
| [#412](https://github.com/EmilioEsposito/portfolio/security/dependabot/412) | high | `uv.lock` | `pyasn1` | `0.6.4` | [GHSA-hm4w-wwcw-mr6r](https://github.com/advisories/GHSA-hm4w-wwcw-mr6r) |
| [#413](https://github.com/EmilioEsposito/portfolio/security/dependabot/413) | high | `uv.lock` | `pyasn1` | `0.6.4` | [GHSA-8ppf-4f7h-5ppj](https://github.com/advisories/GHSA-8ppf-4f7h-5ppj) |
| [#454](https://github.com/EmilioEsposito/portfolio/security/dependabot/454) | high | `uv.lock` | `pyasn1` | `0.6.4` | [GHSA-m4p7-r5rc-7g4j](https://github.com/advisories/GHSA-m4p7-r5rc-7g4j) |
| [#316](https://github.com/EmilioEsposito/portfolio/security/dependabot/316) | high | `uv.lock` | `pyjwt` | `2.15.1` | [GHSA-xgmm-8j9v-c9wx](https://github.com/advisories/GHSA-xgmm-8j9v-c9wx) |
| [#612](https://github.com/EmilioEsposito/portfolio/security/dependabot/612) | high | `uv.lock` | `pyjwt` | `2.15.1` | [GHSA-p4g4-x82p-q773](https://github.com/advisories/GHSA-p4g4-x82p-q773) |
| [#613](https://github.com/EmilioEsposito/portfolio/security/dependabot/613) | high | `uv.lock` | `PyJWT` | `2.15.1` | [GHSA-9v7f-9g4p-ffgj](https://github.com/advisories/GHSA-9v7f-9g4p-ffgj) |
| [#614](https://github.com/EmilioEsposito/portfolio/security/dependabot/614) | critical | `uv.lock` | `PyJWT` | `2.15.1` | [GHSA-ffc3-869f-jxw9](https://github.com/advisories/GHSA-ffc3-869f-jxw9) |
| [#427](https://github.com/EmilioEsposito/portfolio/security/dependabot/427) | high | `uv.lock` | `pypdf` | `6.19.0` | [GHSA-5xf7-4p34-54qr](https://github.com/advisories/GHSA-5xf7-4p34-54qr) |
| [#428](https://github.com/EmilioEsposito/portfolio/security/dependabot/428) | high | `uv.lock` | `pypdf` | `6.19.0` | [GHSA-g867-7843-wf8q](https://github.com/advisories/GHSA-g867-7843-wf8q) |
| [#633](https://github.com/EmilioEsposito/portfolio/security/dependabot/633) | high | `uv.lock` | `pypdf` | `6.19.0` | [GHSA-qv6h-rv94-w285](https://github.com/advisories/GHSA-qv6h-rv94-w285) |
| [#634](https://github.com/EmilioEsposito/portfolio/security/dependabot/634) | high | `uv.lock` | `pypdf` | `6.19.0` | [GHSA-5jq2-8x83-x246](https://github.com/advisories/GHSA-5jq2-8x83-x246) |
| [#635](https://github.com/EmilioEsposito/portfolio/security/dependabot/635) | high | `uv.lock` | `pypdf` | `6.19.0` | [GHSA-fp3h-c4fm-7vvf](https://github.com/advisories/GHSA-fp3h-c4fm-7vvf) |
| [#636](https://github.com/EmilioEsposito/portfolio/security/dependabot/636) | high | `uv.lock` | `pypdf` | `6.19.0` | [GHSA-g9cg-prrw-2r8q](https://github.com/advisories/GHSA-g9cg-prrw-2r8q) |
| [#637](https://github.com/EmilioEsposito/portfolio/security/dependabot/637) | high | `uv.lock` | `pypdf` | `6.19.0` | [GHSA-jw7q-gvrg-4vj3](https://github.com/advisories/GHSA-jw7q-gvrg-4vj3) |
| [#638](https://github.com/EmilioEsposito/portfolio/security/dependabot/638) | high | `uv.lock` | `pypdf` | `6.19.0` | [GHSA-w23x-9jrw-r45c](https://github.com/advisories/GHSA-w23x-9jrw-r45c) |
| [#639](https://github.com/EmilioEsposito/portfolio/security/dependabot/639) | high | `uv.lock` | `pypdf` | `6.19.0` | [GHSA-php9-fj8v-98fj](https://github.com/advisories/GHSA-php9-fj8v-98fj) |
| [#640](https://github.com/EmilioEsposito/portfolio/security/dependabot/640) | high | `uv.lock` | `pypdf` | `6.19.0` | [GHSA-v247-6f48-mgcj](https://github.com/advisories/GHSA-v247-6f48-mgcj) |
| [#394](https://github.com/EmilioEsposito/portfolio/security/dependabot/394) | high | `uv.lock` | `soupsieve` | `2.10` | [GHSA-836r-79rf-4m37](https://github.com/advisories/GHSA-836r-79rf-4m37) |
| [#395](https://github.com/EmilioEsposito/portfolio/security/dependabot/395) | high | `uv.lock` | `soupsieve` | `2.10` | [GHSA-2wc2-fm75-p42x](https://github.com/advisories/GHSA-2wc2-fm75-p42x) |
| [#368](https://github.com/EmilioEsposito/portfolio/security/dependabot/368) | high | `uv.lock` | `starlette` | `1.7.0` | [GHSA-82w8-qh3p-5jfq](https://github.com/advisories/GHSA-82w8-qh3p-5jfq) |
| [#364](https://github.com/EmilioEsposito/portfolio/security/dependabot/364) | high | `uv.lock` | `tornado` | `6.5.10` | [GHSA-3x9g-8vmp-wqvf](https://github.com/advisories/GHSA-3x9g-8vmp-wqvf) |
| [#365](https://github.com/EmilioEsposito/portfolio/security/dependabot/365) | high | `uv.lock` | `tornado` | `6.5.10` | [GHSA-mgf9-4vpg-hj56](https://github.com/advisories/GHSA-mgf9-4vpg-hj56) |
| [#531](https://github.com/EmilioEsposito/portfolio/security/dependabot/531) | high | `uv.lock` | `tornado` | `6.5.10` | [GHSA-mpf4-983q-p7j4](https://github.com/advisories/GHSA-mpf4-983q-p7j4) |
| [#621](https://github.com/EmilioEsposito/portfolio/security/dependabot/621) | high | `uv.lock` | `tornado` | `6.5.10` | [GHSA-c2m8-h5v5-343r](https://github.com/advisories/GHSA-c2m8-h5v5-343r) |
| [#622](https://github.com/EmilioEsposito/portfolio/security/dependabot/622) | high | `uv.lock` | `tornado` | `6.5.10` | [GHSA-chx6-46f5-w4vp](https://github.com/advisories/GHSA-chx6-46f5-w4vp) |
| [#264](https://github.com/EmilioEsposito/portfolio/security/dependabot/264) | high | `uv.lock` | `urllib3` | `2.8.0` | [GHSA-mf9v-mfxr-j63j](https://github.com/advisories/GHSA-mf9v-mfxr-j63j) |
| [#267](https://github.com/EmilioEsposito/portfolio/security/dependabot/267) | high | `uv.lock` | `urllib3` | `2.8.0` | [GHSA-qccp-gfcp-xxvc](https://github.com/advisories/GHSA-qccp-gfcp-xxvc) |
| [#617](https://github.com/EmilioEsposito/portfolio/security/dependabot/617) | high | `uv.lock` | `urllib3` | `2.8.0` | [GHSA-8988-9cw3-xx77](https://github.com/advisories/GHSA-8988-9cw3-xx77) |
| [#618](https://github.com/EmilioEsposito/portfolio/security/dependabot/618) | high | `uv.lock` | `urllib3` | `2.8.0` | [GHSA-vxq7-64xx-v4gw](https://github.com/advisories/GHSA-vxq7-64xx-v4gw) |
