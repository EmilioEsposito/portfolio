"""
Unit tests for the httplib2 system-CA-bundle shim.

This shim makes google-api-python-client (which builds on httplib2) trust the
system CA bundle so Google APIs work behind the Claude Code sandbox's
TLS-intercepting egress gateway. See api/src/google/common/ca_bundle.py.

Pure unit tests — no network, no live marker.
"""
import os

import pytest

from api.src.google.common.ca_bundle import configure_httplib2_ca_bundle


@pytest.fixture(autouse=True)
def _isolate_env_and_httplib2(monkeypatch):
    """Clear the relevant env vars and restore httplib2.CA_CERTS after each test."""
    for key in ("HTTPLIB2_CA_CERTS", "SSL_CERT_FILE", "REQUESTS_CA_BUNDLE"):
        monkeypatch.delenv(key, raising=False)
    import httplib2

    original = httplib2.CA_CERTS
    yield
    httplib2.CA_CERTS = original


def test_returns_none_when_no_bundle_configured():
    assert configure_httplib2_ca_bundle() is None


def test_uses_ssl_cert_file(monkeypatch, tmp_path):
    ca = tmp_path / "ca.crt"
    ca.write_text("dummy")
    monkeypatch.setenv("SSL_CERT_FILE", str(ca))

    result = configure_httplib2_ca_bundle()

    import httplib2

    assert result == str(ca)
    assert httplib2.CA_CERTS == str(ca)
    assert os.environ["HTTPLIB2_CA_CERTS"] == str(ca)


def test_falls_back_to_requests_ca_bundle(monkeypatch, tmp_path):
    ca = tmp_path / "requests.crt"
    ca.write_text("dummy")
    monkeypatch.setenv("REQUESTS_CA_BUNDLE", str(ca))

    assert configure_httplib2_ca_bundle() == str(ca)


def test_respects_preexisting_httplib2_ca_certs(monkeypatch, tmp_path):
    existing = tmp_path / "existing.crt"
    existing.write_text("dummy")
    other = tmp_path / "other.crt"
    other.write_text("dummy")
    monkeypatch.setenv("HTTPLIB2_CA_CERTS", str(existing))
    monkeypatch.setenv("SSL_CERT_FILE", str(other))

    # Existing valid HTTPLIB2_CA_CERTS wins over SSL_CERT_FILE.
    assert configure_httplib2_ca_bundle() == str(existing)


def test_ignores_nonexistent_paths(monkeypatch, tmp_path):
    monkeypatch.setenv("SSL_CERT_FILE", str(tmp_path / "does-not-exist.crt"))

    assert configure_httplib2_ca_bundle() is None
