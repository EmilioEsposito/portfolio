"""Renewal regressions using the real Google retry loop with an offline transport."""

import json
from unittest.mock import Mock

import httplib2
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from googleapiclient.http import HttpRequest
from googleapiclient.model import JsonModel

from api.src.google.gmail import routes, service


@pytest.mark.parametrize(
    ("statuses", "succeeds", "attempts"),
    [
        ([200], True, 1),
        ([500, 200], True, 2),
        ([429, 200], True, 2),
        ([503] * 4, False, 4),
        ([403], False, 1),
        ([401], False, 1),
    ],
)
def test_watch_renews_without_stop_and_retries(monkeypatch, statuses, succeeds, attempts):
    success = {"historyId": "123", "expiration": "1893456000000"}
    http = Mock()
    http.request.side_effect = [
        (
            httplib2.Response({"status": str(status)}),
            json.dumps(
                success
                if status == 200
                else {
                    "error": {
                        "message": "failed",
                        "errors": [{"reason": "backendError" if status >= 500 else "forbidden"}],
                    }
                }
            ).encode(),
        )
        for status in statuses
    ]
    request = HttpRequest(http, JsonModel().response, "https://gmail.test/watch", method="POST")
    request._sleep = Mock()
    request._rand = lambda: 0.5
    gmail = Mock()
    gmail.users.return_value.watch.return_value = request
    monkeypatch.setattr(service, "get_gmail_service", Mock(return_value=gmail))
    monkeypatch.setattr(service, "get_delegated_credentials", Mock())
    stop = Mock()
    monkeypatch.setattr(service, "stop_gmail_watch", stop)
    if succeeds:
        assert service.setup_gmail_watch() == success
    else:
        with pytest.raises(service.HTTPException, match="Failed to set up Gmail watch"):
            service.setup_gmail_watch()
    assert http.request.call_count == attempts
    assert request._sleep.call_count == attempts - 1
    stop.assert_not_called()
    gmail.users.return_value.stop.assert_not_called()
    body = gmail.users.return_value.watch.call_args.kwargs["body"]
    assert body["labelIds"] == ["INBOX", "Label_5289438082921996324"]
    assert body["topicName"] == "projects/portfolio-450200/topics/gmail-notifications"


@pytest.fixture
def watch_client(monkeypatch):
    monkeypatch.setenv("CRON_SECRET", "test-watch-secret")
    monkeypatch.setenv("RAILWAY_ENVIRONMENT_NAME", "production")
    app = FastAPI()
    app.include_router(routes.router)
    return TestClient(app)


@pytest.mark.parametrize("operation", ["start", "refresh"])
def test_watch_route_success_and_failure(watch_client, monkeypatch, operation):
    setup = Mock(return_value={"expiration": "1893456000000", "historyId": "123"})
    stop = Mock()
    monkeypatch.setattr(routes, "setup_gmail_watch", setup)
    monkeypatch.setattr(routes, "stop_gmail_watch", stop)
    headers = {"Authorization": "Bearer test-watch-secret"}
    assert watch_client.post(f"/gmail/watch/{operation}").status_code == 401
    setup.assert_not_called()
    result = watch_client.post(f"/gmail/watch/{operation}", headers=headers)
    assert result.status_code == 200
    assert result.json()["expiration"] == "1893456000000"
    setup.side_effect = RuntimeError("exhausted")
    assert watch_client.post(f"/gmail/watch/{operation}", headers=headers).status_code == 500
    stop.assert_not_called()


def test_development_skip(watch_client, monkeypatch):
    monkeypatch.setenv("RAILWAY_ENVIRONMENT_NAME", "development")
    setup = Mock()
    monkeypatch.setattr(routes, "setup_gmail_watch", setup)
    result = watch_client.post(
        "/gmail/watch/refresh", headers={"Authorization": "Bearer test-watch-secret"}
    )
    assert result.status_code == 200
    assert "Skipping" in result.json()["message"]
    setup.assert_not_called()


def test_stop_false_is_http_failure(watch_client, monkeypatch):
    monkeypatch.setattr(routes, "stop_gmail_watch", Mock(return_value=False))
    assert (
        watch_client.post(
            "/gmail/watch/stop", headers={"Authorization": "Bearer test-watch-secret"}
        ).status_code
        == 500
    )
