# Reusable credential custody

`recovery.py` provides offline private storage, literal environment-file imports,
app-owned secret generation, manifest routing, delivery preparation, and lifecycle
assertions. It does not import dated incident adapters or contact providers.
`manifest.json` is Portfolio's current source-derived consumer contract, not proof
of hosted state or a universal required-variable list.

Start with [the playbook](../../docs/credentials/README.md). From the repository root:

```sh
python3 scripts/credentials/recovery.py --help
python3 scripts/credentials/recovery.py --cycle <cycle> status
```

The primary checkout's ignored `.local/credential-recovery/` path, cycle schema,
permissions, private journals and evidence remain unchanged. Use a fresh cycle for
new replacements; reconcile uncertain operations before another issuer mutation.
Never print private inputs or use a CLI flag to bypass ownership/capture checks.

[Incident adapters](../incidents/README.md) own fixed account/resource identities
and provider mutations. They are intentionally not imported by this package.
The old `scripts/credential_*.py` entrypoints remain thin import/CLI aliases during
active cutover; the old manifest path is a symlink to this single manifest, not a
second copy. New callers should use the canonical paths.

Synthetic tests remain in `api/src/tests/test_credential_*.py` per repo convention.
The layout regression exercises custody with the entire incident tree absent.
