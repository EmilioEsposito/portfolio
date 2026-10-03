# Temporary braces depth mitigation

The dependency graph refresh after PR #320 indexed high-severity
[Dependabot #642](https://github.com/EmilioEsposito/portfolio/security/dependabot/642),
[GHSA-vfj7-8cjw-p6xm / CVE-2026-93687](https://github.com/advisories/GHSA-vfj7-8cjw-p6xm).
All published `braces` versions through 3.0.3 are affected; no patched release was
available on 2026-10-03. It reaches the workspace through micromatch in build and
mobile tooling. No production exploit testing was performed.

`patches/braces@3.0.3.patch` follows the mitigation recommended in
[upstream issue #70](https://github.com/micromatch/braces/issues/70): bound nesting
in the parser before the recursive compile, expand, and stringify walkers run.
The installed package rejects more than 100 nested brace/parenthesis groups with
a deliberate `SyntaxError`, analogous to its existing character-length rejection.
The limit cannot be disabled by increasing `maxLength`. Quoted and escaped braces
are literal text, and sequential shallow groups do not accumulate depth.

This protects string-input paths and ASTs produced by the patched parser. It does
not claim to validate arbitrary caller-constructed AST objects passed directly to
low-level walkers; no such untrusted-AST use was found in this repository. Callers
must still handle invalid-pattern errors, as they must for the existing length guard.

Validation: four depth regression cases fail before the patch and pass afterward
across parse/compile/expand/stringify, testing depth 101 and 4,000 for braces and
parentheses. Boundary depth 100, normal expansion, escaped/quoted input and 200 flat
groups pass. The full security suite has nine passing tests; web regression tests,
typecheck and production build are also checked in CI.

Keep the GitHub alert visible. Version-based scanners cannot infer this local
patch; replace it with a verified upstream fixed release when one becomes available.
The separate node-forge alert #641 is likewise version-flagged but was mitigated
by PR #320's tested temporary patch.
