# Python installer and CA verification

These dependency-free helpers live inside the standalone MCP Docker context and
are also copied into the API and Cursor images and called from CI/worktree setup.
No service credentials are needed. They never load application modules or dotenv.

- `bootstrap-pip.sh [interpreter]` installs pip 26.2.2 from the exact official
  wheel URL and SHA-256 published in the [Python incident guidance](https://discuss.python.org/t/information-for-python-users-regarding-emudhra-emsign-ca-certificate-security-incident/109576).
  Use it **before** that interpreter's first package download. It does not fix
  other interpreters, existing venvs, or the old wheel embedded in `ensurepip`.
- `distrust-emsign.sh` is for Debian/Ubuntu images or explicitly managed CI
  runners. Run as root after refreshing `ca-certificates`. It disables only
  `emSign_ECC_Root_CA_-_G3.crt`, retains unaffected roots, and rebuilds the bundle
  and hashed certificate directory. Do not run this against the macOS host.
- `check_python_trust.py [--require-uv] [--require-certifi]` checks installed pip
  and its own vendored certifi, application certifi, httplib2's effective default,
  OpenSSL's active file/directory, custom Requests/httplib2/AWS/pip bundles, and uv's version.
  A missing application package is allowed before installation; `--require-certifi`
  makes it mandatory afterward. The MCP runtime intentionally has neither venv pip
  nor uv; its base interpreter's pip is checked separately.

The checker verifies G3 distrust, not every possible certificate-chain/revocation
attack. uv's internal roots are addressed by its fixed version, not by certifi.
Native OS trust providers such as macOS Keychain need OS-owner verification.
Also audit OS-added enterprise roots,
and any application-supplied SSL context. Do not print credential variables.

Run `python3 -m unittest discover -s api/src/tests -p test_python_trust.py`
from the repository root to test refusal of old tools, empty stores, missing
bundles and a synthetic distrusted CA. The full inventory, evidence, and authorized
redeployment checklist are in [docs/PYTHON_TRUST.md](../../../docs/PYTHON_TRUST.md).
