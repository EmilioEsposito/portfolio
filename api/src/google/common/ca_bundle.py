"""
Make httplib2 (used by google-api-python-client) trust the system CA bundle.

Why this exists
---------------
``google-api-python-client`` builds its HTTP transport on ``httplib2``, which —
unlike ``requests`` / ``httpx`` / ``curl`` — ships and uses its *own* CA bundle
(``httplib2/cacerts.txt`` or ``certifi``) and ignores the system trust store.

In the Claude Code cloud sandbox, all outbound HTTPS is MITM-intercepted by
Anthropic's egress gateway, whose CA is installed only in the **system** bundle
(``SSL_CERT_FILE`` → ``/etc/ssl/certs/ca-certificates.crt``). So httplib2
rejects the gateway's certificate with::

    [SSL: CERTIFICATE_VERIFY_FAILED] self-signed certificate in certificate chain

and every Google API call (Gmail / Sheets / Calendar / Drive) fails — even
though the very same hosts are reachable via ``curl`` / ``httpx``, which read
the system bundle. (This is distinct from the OpenPhone/Quo failure, which is a
genuine upstream-TLS rejection at that vendor's edge, not a trust problem.)

Pointing httplib2 at the system bundle aligns it with the rest of the app. It
is also correct in production: there the system bundle is the standard Mozilla
set, which already trusts Google — so this is safe in every environment and a
no-op when no system bundle is configured.

httplib2 resolves its CA path once at import (``httplib2.CA_CERTS``) and also
honors the ``HTTPLIB2_CA_CERTS`` env var (see ``httplib2/certs.py``). We set
both so the result is independent of import order.
"""
import os
from typing import Optional


def configure_httplib2_ca_bundle() -> Optional[str]:
    """Point httplib2 at the system CA bundle when one is configured.

    Order of preference: an already-set ``HTTPLIB2_CA_CERTS`` (respected), then
    ``SSL_CERT_FILE``, then ``REQUESTS_CA_BUNDLE``. Returns the bundle path that
    was applied, or ``None`` when no usable system bundle is configured — in
    which case httplib2's own default is left in place (correct for production
    when ``SSL_CERT_FILE`` is unset).
    """
    existing = os.environ.get("HTTPLIB2_CA_CERTS")
    if existing and os.path.isfile(existing):
        ca = existing
    else:
        ca = os.environ.get("SSL_CERT_FILE") or os.environ.get("REQUESTS_CA_BUNDLE")
        if not ca or not os.path.isfile(ca):
            return None
        os.environ["HTTPLIB2_CA_CERTS"] = ca

    try:
        import httplib2

        httplib2.CA_CERTS = ca
    except Exception:
        # httplib2 isn't importable — nothing to configure. The Google client
        # would fail to import too; don't mask that with an error here.
        return None
    return ca
