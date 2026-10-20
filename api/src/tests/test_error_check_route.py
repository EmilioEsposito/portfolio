"""Tests for the /api/error alert-testing route.

Regression coverage for the "Error-level records (non-local)" alert firing on
fake errors: /api/error is published in the public /api/openapi.json, so
scanners that walk the schema and replay every path hit it in production and
trip the alert (observed 2026-10-09 and 2026-10-12). The route must stay
functional in non-production environments, which is where the alert pipeline
is exercised on purpose.
"""

import os
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from api.index import app


@pytest.fixture
def client():
    return TestClient(app, raise_server_exceptions=False)


class TestErrorCheckRoute:
    def test_production_returns_404_without_logging_an_error(self, client):
        with patch.dict(os.environ, {"RAILWAY_ENVIRONMENT_NAME": "production"}, clear=False):
            with patch("api.index.logfire.error") as mock_error:
                response = client.get("/api/error")

        assert response.status_code == 404
        mock_error.assert_not_called()

    @pytest.mark.parametrize("environment", ["development", "portfolio-pr-328", ""])
    def test_non_production_still_raises_500(self, client, environment):
        with patch.dict(os.environ, {"RAILWAY_ENVIRONMENT_NAME": environment}, clear=False):
            with patch("api.index.logfire.error") as mock_error:
                response = client.get("/api/error")

        assert response.status_code == 500
        mock_error.assert_called_once_with("Raising 500 error for testing")
