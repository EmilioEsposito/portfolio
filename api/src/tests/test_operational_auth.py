"""Offline auth regressions without app startup, dotenv, databases, or provider clients.

Run with --noconftest to avoid the integration conftest importing api.index.
Route declarations are inspected separately; these are not full-app E2E tests.
"""

import ast
import importlib.util
from pathlib import Path
from unittest.mock import Mock

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location(
    "operational_auth", ROOT / "api/src/utils/dependencies.py"
)
auth = importlib.util.module_from_spec(spec)
spec.loader.exec_module(auth)


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("PYTHON_DOTENV_DISABLED", "1")
    # Any accidental external connection must fail, even if a test is later extended.
    monkeypatch.setattr("socket.socket.connect", Mock(side_effect=AssertionError("No network")))
    app = FastAPI()
    operation = Mock(return_value={"success": True})

    @app.post("/private", dependencies=[Depends(auth.verify_cron_secret)])
    def private_operation():
        return operation()

    with TestClient(app) as test_client:
        yield test_client, operation


@pytest.mark.parametrize("secret", [None, "", " ", "\t"])
@pytest.mark.parametrize("token", [None, "Bearer None", "Bearer ", "Bearer test-cron-token"])
def test_unconfigured_cron_rejects_every_request(client, monkeypatch, secret, token):
    if secret is None:
        monkeypatch.delenv("CRON_SECRET", raising=False)
    else:
        monkeypatch.setenv("CRON_SECRET", secret)
    http, operation = client
    headers = {"Authorization": token} if token is not None else {}
    response = http.post("/private", headers=headers, json={"password": "test-password"})
    assert response.status_code == 401
    operation.assert_not_called()


@pytest.mark.parametrize(
    "token", [None, "", "Bearer wrong", "test-cron-token", "Basic test-cron-token"]
)
def test_password_body_cannot_replace_cron_token(client, monkeypatch, token):
    monkeypatch.setenv("CRON_SECRET", "test-cron-token")
    http, operation = client
    headers = {"Authorization": token} if token is not None else {}
    response = http.post("/private", headers=headers, json={"password": "test-password"})
    assert response.status_code == 401
    operation.assert_not_called()


def test_configured_cron_token_authorizes_without_a_body(client, monkeypatch):
    monkeypatch.setenv("CRON_SECRET", "test-cron-token")
    http, operation = client
    response = http.post("/private", headers={"Authorization": "Bearer test-cron-token"})
    assert response.status_code == 200
    operation.assert_called_once_with()


def route_declarations(relative_path):
    tree = ast.parse((ROOT / relative_path).read_text())
    routes = {}
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            if (
                isinstance(decorator, ast.Call)
                and isinstance(decorator.func, ast.Attribute)
                and isinstance(decorator.func.value, ast.Name)
                and decorator.func.value.id == "router"
            ):
                routes[decorator.args[0].value] = (node, decorator)
    return routes


@pytest.mark.parametrize(
    ("file", "paths", "dependency"),
    [
        (
            "api/src/open_phone/routes.py",
            ["/send_message", "/create_contacts_in_openphone"],
            "verify_serniacapital_user",
        ),
        (
            "api/src/google/gmail/routes.py",
            ["/watch/start", "/watch/stop", "/watch/refresh"],
            "verify_cron_secret",
        ),
        ("api/src/cron.py", ["/cron_job_example_private"], "verify_cron_secret"),
    ],
)
def test_operational_routes_declare_required_auth(file, paths, dependency):
    routes = route_declarations(file)
    for path in paths:
        node, decorator = routes[path]
        dependencies = next(kw.value for kw in decorator.keywords if kw.arg == "dependencies")
        assert ast.unparse(dependencies) == f"[Depends({dependency})]"
        if path.startswith("/watch/"):
            assert decorator.func.attr == "post"
            assert not node.args.args  # No obsolete password request schema.


def test_password_only_contact_deletion_is_absent():
    assert "/contact/{id}" not in route_declarations("api/src/open_phone/routes.py")
    assert not (ROOT / "api/src/utils/password.py").exists()
