"""Offline trust inventory; exits nonzero on vulnerable tooling or active roots.

Kept inside the standalone MCP build context; also used by the API, CI and setup.
Does not import application modules, load dotenv, or print environment values.
"""

import argparse
import importlib
import importlib.util
import re
import shutil
import ssl
import subprocess
import sys
from importlib.metadata import version
from pathlib import Path

BAD_ROOT = "emSign ECC Root CA - G3"


def require_version(name: str, installed: str, minimum: str) -> None:
    print(f"{name}: {installed} (minimum {minimum})")
    if tuple(map(int, installed.split("."))) < tuple(map(int, minimum.split("."))):
        raise ValueError(f"Upgrade {name} before any package download")


def check_context(context: ssl.SSLContext, label: str) -> None:
    certificates = context.get_ca_certs()
    if not certificates:
        raise ValueError(f"No CA certificates loaded: {label}")
    for certificate in certificates:
        names = [value for rdn in certificate["subject"] for key, value in rdn]
        if BAD_ROOT in names:
            raise ValueError(f"Compromised root trusted: {label}")
    print(f"{label}: {len(certificates)} roots; compromised G3 absent")


def check_bundle(path: str, label: str, *, private_path: bool = False) -> None:
    # Use an empty context so a passing custom bundle cannot mask a bad system bundle.
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    try:
        context.load_verify_locations(cafile=path)
    except OSError:
        if private_path:
            raise ValueError(f"Cannot load {label}") from None
        raise
    check_context(context, label if private_path else f"{label} ({path})")


def check_default_trust() -> None:
    defaults = ssl.get_default_verify_paths()
    default_context = ssl.create_default_context()
    # OpenSSL lazily loads capath entries; inspect them separately as well.
    if defaults.capath:
        directory_context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        loaded = False
        for path in sorted(Path(defaults.capath).iterdir()):
            if path.is_file() and re.fullmatch(r"[0-9a-f]{8}\.\d+", path.name):
                directory_context.load_verify_locations(cafile=str(path))
                default_context.load_verify_locations(cafile=str(path))
                loaded = True
        if loaded:
            check_context(directory_context, f"OpenSSL capath ({defaults.capath})")
    check_context(default_context, "Python default SSL context (including lazy capath)")
    if defaults.cafile:
        check_bundle(defaults.cafile, "OpenSSL cafile")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--require-uv", action="store_true")
    parser.add_argument("--require-certifi", action="store_true")
    args = parser.parse_args()
    print(f"Python: {sys.version.split()[0]} ({sys.executable})")
    check_default_trust()
    for name in ("certifi", "pip", "httplib2"):
        if importlib.util.find_spec(name) is None:
            print(f"{name}: absent")
            if name == "certifi" and args.require_certifi:
                raise ValueError("Application certifi is missing")
            continue
        if name == "pip":
            require_version(name, version(name), "26.2.2")
            bundled = importlib.import_module("pip._vendor.certifi")
            require_version("pip vendored certifi", bundled.__version__, "2026.10.19")
            check_bundle(bundled.where(), "pip vendored certifi")
        elif name == "certifi":
            require_version(name, version(name), "2026.10.19")
            check_bundle(importlib.import_module(name).where(), name)
        else:
            check_bundle(importlib.import_module(name).CA_CERTS, "httplib2 default")
    # Explicit custom overrides are additional trust surfaces, never logged as values.
    import os

    for variable in (
        "REQUESTS_CA_BUNDLE",
        "CURL_CA_BUNDLE",
        "HTTPLIB2_CA_CERTS",
        "AWS_CA_BUNDLE",
        "PIP_CERT",
    ):
        if os.environ.get(variable):
            check_bundle(os.environ[variable], f"{variable} override", private_path=True)
    if shutil.which("uv"):
        installed = subprocess.check_output(["uv", "--version"], text=True).split()[1]
        require_version("uv", installed, "0.13.4")
    elif args.require_uv:
        raise ValueError("uv is missing")
    else:
        print("uv: absent (not needed in the MCP runtime)")


if __name__ == "__main__":
    main()
