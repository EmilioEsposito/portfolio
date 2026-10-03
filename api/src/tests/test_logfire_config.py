"""Tests for Logfire configuration helpers.

Regression coverage for the "Error-level records (non-local)" alert firing on
local development errors: the worktree launcher sets RAILWAY_ENVIRONMENT_NAME
to an empty string, which used to reach Logfire as environment="" and be
stored as a NULL environment that the alert counts as non-local.
"""

import os
from unittest.mock import patch

from api.src.utils.logfire_config import resolve_environment


class TestResolveEnvironment:
    def test_absent_variable_falls_back_to_local(self):
        with patch.dict(os.environ, {}, clear=True):
            assert resolve_environment() == "local"

    def test_empty_variable_falls_back_to_local(self):
        """The worktree launcher's empty-string sentinel must not become NULL."""
        with patch.dict(os.environ, {"RAILWAY_ENVIRONMENT_NAME": ""}, clear=True):
            assert resolve_environment() == "local"

    def test_hosted_environment_is_preserved(self):
        for name in ("production", "development", "portfolio-pr-321"):
            with patch.dict(os.environ, {"RAILWAY_ENVIRONMENT_NAME": name}, clear=True):
                assert resolve_environment() == name

    def test_explicit_argument_wins_over_environment(self):
        with patch.dict(os.environ, {"RAILWAY_ENVIRONMENT_NAME": "production"}, clear=True):
            assert resolve_environment("verification") == "verification"

    def test_explicit_empty_argument_falls_through_to_environment(self):
        with patch.dict(os.environ, {"RAILWAY_ENVIRONMENT_NAME": "production"}, clear=True):
            assert resolve_environment("") == "production"

    def test_never_returns_empty(self):
        """Logfire stores an empty environment as NULL, so never emit one."""
        for env in ({}, {"RAILWAY_ENVIRONMENT_NAME": ""}):
            with patch.dict(os.environ, env, clear=True):
                assert resolve_environment() != ""
                assert resolve_environment("") != ""
