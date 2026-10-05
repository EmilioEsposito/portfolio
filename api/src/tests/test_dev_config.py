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
        "BACKGROUND_AUTOMATION_ENABLED": "true",
    }
    api = config.environment("api", fetched, host, "railway")
    assert api["CLERK_SECRET_KEY"] == "portfolio"
    assert api["DATABASE_URL"] == "local-db"
    assert api["PORT"] == "8123"
    assert api["BACKGROUND_AUTOMATION_ENABLED"] == "false"
    assert api["PYTHON_DOTENV_DISABLED"] == "1"
    assert not any(key.startswith(("RAILWAY_", "GITHUB_", "PROD_")) for key in api)
    web = config.environment("web", fetched, host, "railway")
    assert web["CLERK_SECRET_KEY"] == "portfolio"
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
async def test_dev_only_clerk_webhook_is_verified_without_production_key(monkeypatch):
    from api.src.user import routes

    verifier = Mock()
    verifier.verify.return_value = {"type": "user.created", "data": {"id": "user_test"}}
    monkeypatch.setattr(routes, "webhook_dev", verifier)
    monkeypatch.setattr(routes, "webhook_prod", None)
    upsert = AsyncMock(return_value="updated")
    monkeypatch.setattr(routes, "upsert_user", upsert)
    request = SimpleNamespace(body=AsyncMock(return_value=b"{}"))
    await routes.handle_clerk_webhook(request, None, "id", "timestamp", "signature")
    upsert.assert_awaited_once_with(None, {"id": "user_test"}, "development")


@pytest.mark.asyncio
async def test_missing_clerk_webhook_keys_fail_closed(monkeypatch):
    from fastapi import HTTPException

    from api.src.user import routes

    monkeypatch.setattr(routes, "webhook_dev", None)
    monkeypatch.setattr(routes, "webhook_prod", None)
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
async def test_prod_only_clerk_webhook_remains_supported(monkeypatch):
    from api.src.user import routes

    verifier = Mock()
    verifier.verify.return_value = {"type": "user.created", "data": {"id": "user_test"}}
    monkeypatch.setattr(routes, "webhook_dev", None)
    monkeypatch.setattr(routes, "webhook_prod", verifier)
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
    monkeypatch.setattr(routes, "webhook_dev", verifier)
    monkeypatch.setattr(routes, "webhook_prod", None)
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
            "VITE_GOOGLE_CLIENT_ID": "public-client",
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
