"""Service-scoped development configuration. Never write provider values to disk."""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
PROJECT = "73eb837a-ba86-4899-992c-cefd0c22b91f"
SERVICES = {"api": "fastapi", "web": "react-router"}
BASELINE = set(
    """PATH HOME LANG LC_ALL TERM SSL_CERT_FILE SSL_CERT_DIR REQUESTS_CA_BUNDLE
CURL_CA_BUNDLE NODE_EXTRA_CA_CERTS HTTPS_PROXY HTTP_PROXY ALL_PROXY NO_PROXY
https_proxy http_proxy all_proxy no_proxy XDG_DATA_HOME XDG_CACHE_HOME UV_CACHE_DIR
UV_PYTHON_INSTALL_DIR TMPDIR""".split()
)
API_KEYS = set(
    """ADMIN_PASSWORD_HASH ADMIN_PASSWORD_SALT CLERK_SECRET_KEY CLICKUP_API_KEY
CRON_SECRET DEV_CLERK_WEBHOOK_SECRET GOOGLE_OAUTH_CLIENT_ID GOOGLE_OAUTH_CLIENT_SECRET
GOOGLE_SERVICE_ACCOUNT_CREDENTIALS OPENAI_API_KEY OPEN_PHONE_API_KEY OPEN_PHONE_WEBHOOK_SECRET
PORTFOLIO_OPENROUTER_API_KEY PUBLIC_PORTFOLIO_OPENROUTER_API_KEY SERNIA_ANTHROPIC_API_KEY
SESSION_SECRET_KEY TWILIO_ACCOUNT_SID TWILIO_AUTH_TOKEN VAPID_CLAIM_EMAIL VAPID_PRIVATE_KEY
VAPID_PUBLIC_KEY""".split()
)
WEB_KEYS = {
    "CLERK_SECRET_KEY",
    "VITE_CLERK_PUBLISHABLE_KEY",
    "CLERK_PUBLISHABLE_KEY",
    "VITE_GOOGLE_CLIENT_ID",
    "VITE_GOOGLE_DRIVE_PICKER_API_KEY",
}
LOCAL_KEYS = set(
    """DATABASE_URL DATABASE_URL_UNPOOLED DATABASE_REQUIRE_SSL PORT VITE_PORT
BACKEND_PORT GOOGLE_OAUTH_REDIRECT_URI WORKSPACE_PATH BACKGROUND_AUTOMATION_ENABLED""".split()
)
REQUIRED = {
    "api": set(
        """DATABASE_URL DATABASE_URL_UNPOOLED CLERK_SECRET_KEY SESSION_SECRET_KEY
GOOGLE_OAUTH_CLIENT_ID GOOGLE_OAUTH_CLIENT_SECRET GOOGLE_OAUTH_REDIRECT_URI
OPEN_PHONE_WEBHOOK_SECRET CLICKUP_API_KEY TWILIO_ACCOUNT_SID TWILIO_AUTH_TOKEN
PORTFOLIO_OPENROUTER_API_KEY PUBLIC_PORTFOLIO_OPENROUTER_API_KEY""".split()
    ),
    "web": {"CLERK_SECRET_KEY", "VITE_CLERK_PUBLISHABLE_KEY"},
}


class ConfigError(Exception):
    """Messages contain only fixed diagnostics or known configuration names."""


def query(document: str, variables: dict[str, str], token: str) -> dict:
    # Send authorization through stdin, never argv, shell tracing, or a temp file.
    config = "\n".join(
        [
            'url = "https://backboard.railway.com/graphql/v2"',
            'request = "POST"',
            'header = "Content-Type: application/json"',
            "header = " + json.dumps("Authorization: Bearer " + token),
            "data = " + json.dumps(json.dumps({"query": document, "variables": variables})),
        ]
    )
    try:
        result = subprocess.run(
            ["curl", "--fail", "--silent", "--max-time", "30", "--config", "-"],
            input=config,
            text=True,
            capture_output=True,
            check=False,
        )
        payload = json.loads(result.stdout) if result.returncode == 0 else {}
        if payload.get("errors") or not isinstance(payload.get("data"), dict):
            raise ValueError
        return payload["data"]
    except (OSError, ValueError, TypeError, AttributeError):
        raise ConfigError(
            "Railway request failed; check token and network access (response suppressed)"
        ) from None


def fetch(service: str, host: dict[str, str]) -> dict[str, str]:
    token = host.get("RAILWAY_MCP_TOKEN")
    if not token:
        raise ConfigError("RAILWAY_MCP_TOKEN: missing")
    try:
        data = query(
            "query($id:String!) { project(id:$id) { environments { edges { node { id name } } } services { edges { node { id name } } } } }",
            {"id": PROJECT},
            token,
        )["project"]

        def exact(kind: str, name: str) -> str:
            ids = [
                edge["node"]["id"] for edge in data[kind]["edges"] if edge["node"]["name"] == name
            ]
            if len(ids) != 1:
                raise ConfigError("Exact Railway development service/environment not found")
            return ids[0]

        values = query(
            "query($project:String!,$environment:String!,$service:String!) { variables(projectId:$project,environmentId:$environment,serviceId:$service) }",
            {
                "project": PROJECT,
                "environment": exact("environments", "development"),
                "service": exact("services", SERVICES[service]),
            },
            token,
        )["variables"]
        if not isinstance(values, dict) or any(
            not isinstance(k, str) or not isinstance(v, str) for k, v in values.items()
        ):
            raise ValueError
        return values
    except (KeyError, TypeError, ValueError):
        raise ConfigError("Invalid Railway configuration response (response suppressed)") from None


def environment(
    service: str, values: dict[str, str], host: dict[str, str], source: str
) -> dict[str, str]:
    allowed = API_KEYS if service == "api" else WEB_KEYS
    env = {k: v for k, v in host.items() if k in BASELINE}
    env.update({k: v for k, v in values.items() if k in allowed})
    # Only local input can select databases, ports, or enable automation. Hosted
    # Railway variables (including PATs and production webhook keys) never win.
    local = host if source == "railway" else {**values, **host}
    env.update({k: v for k, v in local.items() if k in LOCAL_KEYS})
    if service == "api":
        env.setdefault("PORT", "8000")
        env.setdefault(
            "GOOGLE_OAUTH_REDIRECT_URI", f"http://localhost:{env['PORT']}/api/oauth/callback"
        )
        env.setdefault("BACKGROUND_AUTOMATION_ENABLED", "false")
        env.setdefault("WORKSPACE_PATH", str(ROOT / ".local/workspace"))
    else:
        env.setdefault("VITE_PORT", "5173")
        env.setdefault("BACKEND_PORT", local.get("PORT", "8000"))
        # A frontend has no reason to receive local database settings.
        for key in LOCAL_KEYS - {"VITE_PORT", "BACKEND_PORT"}:
            env.pop(key, None)
    env.update(
        PORTFOLIO_CONFIG_SOURCE=source,
        PYTHON_DOTENV_DISABLED="1",
        LOGFIRE_SEND_TO_LOGFIRE="false",
        NODE_ENV="development",
    )
    return env


def resolve(
    service: str, source: str, host: dict[str, str]
) -> tuple[dict[str, str], dict[str, str]]:
    if source == "auto":
        source = (
            "railway"
            if (host.get("CODEX_ENVIRONMENT_ID") or host.get("CLAUDE_CODE_REMOTE") == "true")
            else "local"
        )
    if source == "railway":
        values = fetch(service, host)
    else:
        from dotenv import dotenv_values

        path = ROOT / ".env" if service == "api" else ROOT / "apps/web-react-router/.env"
        # No shell evaluation or cross-service variable interpolation.
        values = {k: v for k, v in dotenv_values(path, interpolate=False).items() if v is not None}
        allowed = API_KEYS if service == "api" else WEB_KEYS
        values.update({k: v for k, v in host.items() if k in allowed})
    return environment(service, values, host, source), values


def validate(service: str, env: dict[str, str]) -> list[str]:
    return sorted(
        key for key in REQUIRED[service] if not env.get(key) or env[key].strip("* =") == ""
    )


def validate_runtime(service: str, env: dict[str, str]) -> None:
    for key in ("PORT",) if service == "api" else ("VITE_PORT", "BACKEND_PORT"):
        value = env.get(key, "")
        if not value.isascii() or not value.isdigit() or not 1 <= int(value) <= 65535:
            raise ConfigError(f"{key}: invalid; supply an integer port from 1 to 65535")
    if service == "api" and env.get("BACKGROUND_AUTOMATION_ENABLED", "").lower() not in {
        "true",
        "false",
        "0",
        "1",
    }:
        raise ConfigError("BACKGROUND_AUTOMATION_ENABLED: invalid; use true, false, 1, or 0")
    if service == "api" and env["PORTFOLIO_CONFIG_SOURCE"] == "railway":
        for key in ("DATABASE_URL", "DATABASE_URL_UNPOOLED"):
            if not env.get(key):
                continue  # names-only required-field report below
            try:
                url = urlsplit(env[key])
                safe = (
                    url.scheme in {"postgres", "postgresql"}
                    and url.hostname in {"localhost", "127.0.0.1", "::1"}
                    and not url.query
                    and not url.fragment
                    and bool(url.path.strip("/"))
                    and (url.port is None or 1 <= url.port <= 65535)
                )
            except ValueError:
                safe = False
            if not safe:
                raise ConfigError(f"{key}: supply a local PostgreSQL URL without query parameters")


def private_values(values: dict[str, str]) -> dict[str, str]:
    markers = (
        "SECRET",
        "TOKEN",
        "PASSWORD",
        "CREDENTIAL",
        "API_KEY",
        "PRIVATE_KEY",
        "DATABASE_URL",
        "AUTH",
        "SALT",
        "_PAT",
    )
    return {
        k: v
        for k, v in values.items()
        if not k.startswith("VITE_") and any(marker in k for marker in markers)
    }


def redact(line: str, values: dict[str, str]) -> str:
    # Include multiline credential parts; short nonempty values are redacted too.
    parts = {part for value in values.values() for part in (value, *value.splitlines()) if part}
    for part in sorted(parts, key=len, reverse=True):
        line = line.replace(part, "[redacted]")
    return line


def run(command: list[str], cwd: Path, env: dict[str, str], secrets: dict[str, str]) -> int:
    child = subprocess.Popen(
        command,
        cwd=cwd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        start_new_session=True,
    )

    def stop(signum: int, _frame: object) -> None:
        try:
            os.killpg(child.pid, signum)
        except ProcessLookupError:
            pass

    previous = {sig: signal.signal(sig, stop) for sig in (signal.SIGINT, signal.SIGTERM)}
    try:
        assert child.stdout is not None
        for line in child.stdout:
            print(redact(line, secrets), end="", flush=True)
        return child.wait()
    finally:
        if child.poll() is None:
            stop(signal.SIGTERM, None)
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                stop(signal.SIGKILL, None)
                child.wait()
        for sig, handler in previous.items():
            signal.signal(sig, handler)


def main(arguments: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("service", nargs="?", choices=[*SERVICES, "all"], default="all")
    parser.add_argument("action", nargs="?", choices=["start", "check", "migrate"], default="check")
    parser.add_argument(
        "--source",
        choices=["auto", "local", "railway"],
        default=os.getenv("DEV_CONFIG_SOURCE", "auto"),
    )
    args = parser.parse_args(arguments)
    if args.service == "all":
        if args.action != "check":
            parser.error("select api or web to start/migrate")
        return max(main([service, "check", "--source", args.source]) for service in SERVICES)
    try:
        env, values = resolve(args.service, args.source, dict(os.environ))
        validate_runtime(args.service, env)
        missing = validate(args.service, env)
        for key in sorted(REQUIRED[args.service]):
            print(
                f"{args.service} {key}: {'missing' if key in missing else 'configured'}", flush=True
            )
        if missing:
            return 1
        if args.action == "check":
            return 0
        if args.action == "migrate":
            if args.service != "api":
                raise ConfigError("migrate requires api")
            command = [str(ROOT / ".venv/bin/python"), "-m", "alembic", "upgrade", "head"]
            cwd = ROOT
        elif args.service == "api":
            command = [
                str(ROOT / ".venv/bin/python"),
                "-m",
                "uvicorn",
                "api.index:app",
                "--host",
                "127.0.0.1",
                "--port",
                env["PORT"],
                "--reload",
            ]
            cwd = ROOT
        else:
            command = ["pnpm", "dev", "--host", "127.0.0.1"]
            cwd = ROOT / "apps/web-react-router"
        private = private_values(
            {**values, **env, "RAILWAY_MCP_TOKEN": os.getenv("RAILWAY_MCP_TOKEN", "")}
        )
        return run(command, cwd, env, private)
    except ConfigError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except OSError:
        print("Startup failed; verify installed Python, pnpm, and curl tools", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
