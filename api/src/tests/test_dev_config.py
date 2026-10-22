"""Offline regressions for configuration boundaries and startup automation."""

import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location("dev_config", ROOT / "scripts/dev_config.py")
config = importlib.util.module_from_spec(spec)
spec.loader.exec_module(config)


def test_railway_configuration_does_not_import_infrastructure_or_other_service_credentials():
    host = {
        "PATH": "/bin",
        "DATABASE_URL": "local-db",
        "DATABASE_URL_UNPOOLED": "local-db",
        "PORT": "8123",
        "RAILWAY_MCP_TOKEN": "management",
        "CLERK_SECRET_KEY": "other-app",
    }
    fetched = {
        "CLERK_SECRET_KEY": "portfolio",
        "DATABASE_URL": "hosted-db",
        "PORT": "99",
        "GITHUB_EMILIO_PERSONAL_WRITE_PAT": "write-pat",
        "RAILWAY_API_TOKEN": "provider-token",
        "PROD_CLERK_WEBHOOK_SECRET": "prod-secret",
        "DEV_CLERK_WEBHOOK_SECRET": "dev-secret",
        "CLERK_WEBHOOK_SECRET": "endpoint-secret",
        "BACKGROUND_AUTOMATION_ENABLED": "true",
    }
    api = config.environment("api", fetched, host, "railway")
    assert api["CLERK_SECRET_KEY"] == "portfolio"
    assert api["CLERK_WEBHOOK_SECRET"] == "endpoint-secret"
    assert "DEV_CLERK_WEBHOOK_SECRET" not in api
    assert api["DATABASE_URL"] == "local-db"
    assert api["PORT"] == "8123"
    assert api["BACKGROUND_AUTOMATION_ENABLED"] == "false"
    assert api["PYTHON_DOTENV_DISABLED"] == "1"
    assert not any(key.startswith(("RAILWAY_", "GITHUB_", "PROD_")) for key in api)
    web = config.environment("web", fetched, host, "railway")
    assert web["CLERK_SECRET_KEY"] == "portfolio"
    assert "CLERK_WEBHOOK_SECRET" not in web
    assert "DATABASE_URL" not in web
    assert "RAILWAY_MCP_TOKEN" not in web


def test_missing_database_never_falls_back_to_hosted_database():
    env = config.environment("api", {"DATABASE_URL": "hosted-db"}, {}, "railway")
    assert "DATABASE_URL" in config.validate("api", env)
    assert "DATABASE_URL_UNPOOLED" in config.validate("api", env)


def test_local_values_preserve_database_and_ports_with_shell_override():
    env = config.environment(
        "api", {"DATABASE_URL": "local-file-db", "PORT": "9000"}, {"PORT": "9001"}, "local"
    )
    assert env["DATABASE_URL"] == "local-file-db"
    assert env["PORT"] == "9001"


def test_placeholder_values_fail_validation():
    assert config.validate(
        "web", {"CLERK_SECRET_KEY": "****", "VITE_CLERK_PUBLISHABLE_KEY": "ok"}
    ) == ["CLERK_SECRET_KEY"]


def test_redaction_includes_short_and_multiline_secrets():
    assert (
        config.redact("ab long-first\nsecond-part", {"one": "ab", "two": "long-first\nsecond-part"})
        == "[redacted] [redacted]"
    )


def test_provider_errors_never_expose_response_or_token(monkeypatch):
    monkeypatch.setattr(
        config.subprocess,
        "run",
        lambda *a, **kw: SimpleNamespace(
            returncode=0, stdout='{"errors":[{"message":"secret-value"}]}'
        ),
    )
    with pytest.raises(config.ConfigError) as error:
        config.query("query", {}, "management-token")
    assert "secret-value" not in str(error.value)
    assert "management-token" not in str(error.value)


def test_fetch_resolves_only_exact_development_service(monkeypatch):
    calls = []

    def query(document, variables, token):
        calls.append(variables)
        if len(calls) == 1:
            return {
                "project": {
                    "environments": {
                        "edges": [
                            {"node": {"id": "prod", "name": "production"}},
                            {"node": {"id": "dev", "name": "development"}},
                        ]
                    },
                    "services": {"edges": [{"node": {"id": "api-id", "name": "fastapi"}}]},
                }
            }
        return {"variables": {"CLERK_SECRET_KEY": "private"}}

    monkeypatch.setattr(config, "query", query)
    config.fetch("api", {"RAILWAY_MCP_TOKEN": "token"})
    assert calls[1] == {"project": config.PROJECT, "environment": "dev", "service": "api-id"}


def test_disabled_dotenv_cannot_override_injected_database(monkeypatch, tmp_path):
    from dotenv import load_dotenv

    path = tmp_path / ".env"
    path.write_text("DATABASE_URL=wrong-database\nGITHUB_EMILIO_PERSONAL_WRITE_PAT=forbidden\n")
    monkeypatch.setenv("PYTHON_DOTENV_DISABLED", "1")
    monkeypatch.setenv("DATABASE_URL", "local-database")
    monkeypatch.delenv("GITHUB_EMILIO_PERSONAL_WRITE_PAT", raising=False)
    load_dotenv(path, override=True)
    import os

    assert os.environ["DATABASE_URL"] == "local-database"
    assert "GITHUB_EMILIO_PERSONAL_WRITE_PAT" not in os.environ


@pytest.mark.parametrize(
    ("hosted", "flag", "expected"),
    [
        (None, None, False),
        ("production", None, False),
        ("production", "true", True),
        ("development", None, False),
        ("portfolio-pr-123", None, False),
        ("development", "false", False),
        (None, "true", True),
    ],
)
def test_automation_defaults(monkeypatch, hosted, flag, expected):
    from api.src.utils.automation import background_automation_enabled

    for key, value in (
        ("RAILWAY_ENVIRONMENT_NAME", hosted),
        ("BACKGROUND_AUTOMATION_ENABLED", flag),
    ):
        if value is None:
            monkeypatch.delenv(key, raising=False)
        else:
            monkeypatch.setenv(key, value)
    assert background_automation_enabled() is expected


@pytest.mark.asyncio
async def test_lifespan_runs_workspace_setup_without_creating_scheduler(monkeypatch):
    import api.index as index
    import api.src.sernia_ai.tools.duckdb_tools as duckdb_tools

    monkeypatch.setenv("BACKGROUND_AUTOMATION_ENABLED", "false")
    monkeypatch.setattr(index, "is_hosted", False)
    initialize = AsyncMock()
    scheduler = Mock(side_effect=AssertionError("scheduler must not be constructed"))
    monkeypatch.setattr(index, "initialize_workspace", initialize)
    monkeypatch.setattr(index, "get_scheduler", scheduler)
    monkeypatch.setattr(duckdb_tools, "cleanup_stale_data", Mock())
    app = SimpleNamespace(state=SimpleNamespace())
    async with index.lifespan(app):
        initialize.assert_awaited_once()
        assert not hasattr(app.state, "apscheduler_startup_task")
    scheduler.assert_not_called()


@pytest.mark.asyncio
async def test_development_clerk_webhook_uses_single_secret(monkeypatch):
    from api.src.user import routes

    verifier = Mock()
    verifier.verify.return_value = {"type": "user.created", "data": {"id": "user_test"}}
    monkeypatch.setattr(routes, "webhook", verifier)
    monkeypatch.setenv("CLERK_SECRET_KEY", "sk_test_dummy")
    upsert = AsyncMock(return_value="updated")
    monkeypatch.setattr(routes, "upsert_user", upsert)
    request = SimpleNamespace(body=AsyncMock(return_value=b"{}"))
    await routes.handle_clerk_webhook(request, None, "id", "timestamp", "signature")
    upsert.assert_awaited_once_with(None, {"id": "user_test"}, "development")


@pytest.mark.asyncio
async def test_missing_clerk_webhook_keys_fail_closed(monkeypatch):
    from fastapi import HTTPException

    from api.src.user import routes

    monkeypatch.setattr(routes, "webhook", None)
    with pytest.raises(HTTPException) as error:
        await routes.handle_clerk_webhook(None, None, "id", "time", "signature")
    assert error.value.status_code == 500


@pytest.mark.parametrize(
    "url",
    [
        "postgresql://user@hosted.example/db",
        "postgresql://localhost/db?host=remote",
        "postgresql://localhost/db?service=remote",
        "postgresql://localhost/db?options=x",
        "postgresql://localhost:bad/db",
        "postgresql://localhost/db#secret",
    ],
)
def test_railway_mode_rejects_remote_or_redirected_database_urls(url):
    env = config.environment("api", {}, {"DATABASE_URL": url}, "railway")
    with pytest.raises(config.ConfigError, match="DATABASE_URL: supply a local") as error:
        config.validate_runtime("api", env)
    assert url not in str(error.value)


def test_token_on_laptop_does_not_select_railway(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "ROOT", tmp_path)
    monkeypatch.setattr(config, "fetch", Mock(side_effect=AssertionError("must not fetch")))
    env, _ = config.resolve("api", "auto", {"RAILWAY_MCP_TOKEN": "token"})
    assert env["PORTFOLIO_CONFIG_SOURCE"] == "local"


def test_cloud_automatically_uses_railway(monkeypatch):
    fetch = Mock(return_value={"CLERK_SECRET_KEY": "portfolio"})
    monkeypatch.setattr(config, "fetch", fetch)
    env, _ = config.resolve(
        "web", "auto", {"CODEX_ENVIRONMENT_ID": "cloud", "RAILWAY_MCP_TOKEN": "token"}
    )
    assert env["PORTFOLIO_CONFIG_SOURCE"] == "railway"
    fetch.assert_called_once()


@pytest.mark.parametrize("port", ["0", "70000", "bad", "12\n34"])
def test_invalid_port_diagnostics_are_value_free(port):
    env = config.environment("api", {}, {"PORT": port}, "local")
    with pytest.raises(config.ConfigError, match="PORT: invalid"):
        config.validate_runtime("api", env)


@pytest.mark.asyncio
async def test_production_clerk_webhook_uses_single_secret(monkeypatch):
    from api.src.user import routes

    verifier = Mock()
    verifier.verify.return_value = {"type": "user.created", "data": {"id": "user_test"}}
    monkeypatch.setattr(routes, "webhook", verifier)
    monkeypatch.setenv("CLERK_SECRET_KEY", "sk_live_dummy")
    upsert = AsyncMock(return_value="updated")
    monkeypatch.setattr(routes, "upsert_user", upsert)
    request = SimpleNamespace(body=AsyncMock(return_value=b"{}"))
    await routes.handle_clerk_webhook(request, None, "id", "timestamp", "signature")
    upsert.assert_awaited_once_with(None, {"id": "user_test"}, "production")


@pytest.mark.asyncio
async def test_invalid_clerk_signature_is_rejected(monkeypatch):
    from fastapi import HTTPException
    from svix.webhooks import WebhookVerificationError

    from api.src.user import routes

    verifier = Mock()
    verifier.verify.side_effect = WebhookVerificationError("private-signature")
    monkeypatch.setattr(routes, "webhook", verifier)
    monkeypatch.setenv("CLERK_SECRET_KEY", "sk_test_dummy")
    upsert = AsyncMock()
    monkeypatch.setattr(routes, "upsert_user", upsert)
    request = SimpleNamespace(body=AsyncMock(return_value=b"{}"))
    with pytest.raises(HTTPException) as error:
        await routes.handle_clerk_webhook(request, None, "id", "timestamp", "signature")
    assert error.value.status_code == 400
    assert "private-signature" not in error.value.detail
    upsert.assert_not_awaited()


def test_redaction_selects_secrets_without_mangling_public_settings():
    private = config.private_values(
        {
            "CLERK_SECRET_KEY": "short",
            "RAILWAY_MCP_TOKEN": "manage",
            "ENVIRONMENT": "development",
            "BACKGROUND_AUTOMATION_ENABLED": "1",
            "VITE_CLERK_PUBLISHABLE_KEY": "public-client",
            "GITHUB_WRITE_PAT": "write-key",
        }
    )
    assert (
        config.redact("development 1 public-client short manage write-key", private)
        == "development 1 public-client [redacted] [redacted] [redacted]"
    )


def test_bare_config_check_reports_both_services(monkeypatch, capsys):
    monkeypatch.setattr(
        config,
        "resolve",
        lambda service, source, host: (config.environment(service, {}, {}, "local"), {}),
    )
    assert config.main([]) == 1
    output = capsys.readouterr().out
    assert "api CLERK_SECRET_KEY: missing" in output
    assert "web CLERK_SECRET_KEY: missing" in output


@pytest.mark.parametrize("source", ["local", "railway"])
def test_quo_credential_is_scoped_to_backend_and_redacted(source):
    values = {
        "QUO_API_KEY_PORTFOLIO": "test-portfolio-quo",
        "OPEN_PHONE_API_KEY": "test-legacy-openphone",
        "QUO_API_KEY": "test-legacy-quo",
    }
    api = config.environment("api", values, {}, source)
    assert api["QUO_API_KEY_PORTFOLIO"] == "test-portfolio-quo"
    assert "OPEN_PHONE_API_KEY" not in api
    assert "QUO_API_KEY" not in api
    web = config.environment("web", values, {}, source)
    assert not any(key in web for key in values)
    assert config.redact("test-portfolio-quo", config.private_values(api)) == "[redacted]"


@pytest.mark.parametrize("source", ["local", "railway"])
@pytest.mark.parametrize("service", ["api", "web"])
def test_retired_credentials_are_not_forwarded(source, service):
    retired = dict.fromkeys(
        [
            "ADMIN_PASSWORD_HASH",
            "ADMIN_PASSWORD_SALT",
            "VITE_GOOGLE_DRIVE_PICKER_API_KEY",
            "NEXT_PUBLIC_GOOGLE_DRIVE_PICKER_API_KEY",
            "VITE_GOOGLE_CLIENT_ID",
        ],
        "test-retired-value",
    )
    env = config.environment(service, retired, retired, source)
    assert not retired.keys() & env.keys()


def test_google_backend_credentials_and_clerk_remain_supported():
    values = {
        "GOOGLE_OAUTH_CLIENT_ID": "test-client-id",
        "GOOGLE_OAUTH_CLIENT_SECRET": "test-client-secret",
        "GOOGLE_SERVICE_ACCOUNT_CREDENTIALS": "test-service-account",
        "CLERK_SECRET_KEY": "test-clerk-secret",
        "VITE_CLERK_PUBLISHABLE_KEY": "test-clerk-public",
    }
    api = config.environment("api", values, {}, "local")
    for name in values.keys() - {"VITE_CLERK_PUBLISHABLE_KEY"}:
        assert api[name] == values[name]
    web = config.environment("web", values, {}, "local")
    assert web["CLERK_SECRET_KEY"] == values["CLERK_SECRET_KEY"]
    assert web["VITE_CLERK_PUBLISHABLE_KEY"] == values["VITE_CLERK_PUBLISHABLE_KEY"]
    assert not any(name.startswith("GOOGLE_") for name in web)
