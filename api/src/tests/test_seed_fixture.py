"""Unit tests for the sanitized seed fixture pipeline (api/src/utils/seed_fixture.py).

No network, no DB — pure functions plus the graceful-skip behavior that CI
and credential-less environments rely on.
"""
import pytest

from api.src.utils.seed_bucket import get_seed_bucket
from api.src.utils.seed_fixture import (
    ANY_STRING_CAP,
    TOOL_RESULT_CAP,
    sanitize_text,
    sanitize_value,
)


class TestSanitizeText:
    def test_phone_numbers_redacted_deterministically(self):
        import re

        a = sanitize_text("call me at (412) 555-0187 ok?")
        b = sanitize_text("number on file: +14125550187")
        assert "555-0187" not in a and "5550187" not in a
        # Same digits (regardless of formatting / country code) -> same fake,
        # so threads stay internally consistent.
        fake_a = re.search(r"\+1555\d{7}", a).group(0)
        fake_b = re.search(r"\+1555\d{7}", b).group(0)
        assert fake_a == fake_b

    def test_external_email_redacted(self):
        out = sanitize_text("lead from john.doe@gmail.com today")
        assert "john.doe@gmail.com" not in out
        assert "@example.com" in out

    def test_internal_email_keeps_domain(self):
        out = sanitize_text("cc emilio@serniacapital.com")
        assert "emilio@serniacapital.com" not in out
        assert "@serniacapital.com" in out  # routing logic depends on the domain

    def test_plain_text_untouched(self):
        assert sanitize_text("fix the faucet in unit 2") == "fix the faucet in unit 2"


class TestSanitizeValue:
    def test_tool_return_string_content_truncated(self):
        part = {"part_kind": "tool-return", "content": "x" * (TOOL_RESULT_CAP + 500)}
        out = sanitize_value(part)
        assert len(out["content"]) < TOOL_RESULT_CAP + 100
        assert "truncated for seed fixture" in out["content"]

    def test_tool_return_structured_content_collapsed_when_large(self):
        part = {"part_kind": "tool-return", "content": {"rows": ["y" * 500] * 20}}
        out = sanitize_value(part)
        assert isinstance(out["content"], str)
        assert "truncated for seed fixture" in out["content"]

    def test_small_tool_return_structured_content_preserved(self):
        part = {"part_kind": "tool-return", "content": {"ok": True, "count": 3}}
        out = sanitize_value(part)
        assert out["content"] == {"ok": True, "count": 3}

    def test_non_tool_strings_get_looser_cap(self):
        text = "z" * (TOOL_RESULT_CAP + 500)  # over tool cap, under global cap
        out = sanitize_value({"part_kind": "text", "content": text})
        assert out["content"] == text
        too_long = "z" * (ANY_STRING_CAP + 500)
        out = sanitize_value({"part_kind": "text", "content": too_long})
        assert "truncated for seed fixture" in out["content"]

    def test_nested_structures_sanitized(self):
        msgs = [{"parts": [{"part_kind": "user-prompt", "content": "text me at 4125550199"}]}]
        out = sanitize_value(msgs)
        assert "4125550199" not in str(out)


class TestGetSeedBucket:
    def test_returns_none_when_unconfigured(self, monkeypatch):
        for var in (
            "SEED_BUCKET_ENDPOINT_URL",
            "SEED_BUCKET_NAME",
            "SEED_BUCKET_ACCESS_KEY_ID",
            "SEED_BUCKET_SECRET_ACCESS_KEY",
        ):
            monkeypatch.delenv(var, raising=False)
        assert get_seed_bucket() is None

    def test_returns_none_when_partially_configured(self, monkeypatch):
        monkeypatch.setenv("SEED_BUCKET_ENDPOINT_URL", "https://t3.storageapi.dev")
        monkeypatch.setenv("SEED_BUCKET_NAME", "bucket")
        monkeypatch.delenv("SEED_BUCKET_ACCESS_KEY_ID", raising=False)
        monkeypatch.delenv("SEED_BUCKET_SECRET_ACCESS_KEY", raising=False)
        assert get_seed_bucket() is None

    def test_returns_bucket_when_complete(self, monkeypatch):
        monkeypatch.setenv("SEED_BUCKET_ENDPOINT_URL", "https://t3.storageapi.dev")
        monkeypatch.setenv("SEED_BUCKET_NAME", "bucket")
        monkeypatch.setenv("SEED_BUCKET_ACCESS_KEY_ID", "key")
        monkeypatch.setenv("SEED_BUCKET_SECRET_ACCESS_KEY", "secret")
        bucket = get_seed_bucket()
        assert bucket.bucket_name == "bucket"
        assert bucket.FIXTURE_KEY == "seed_fixtures/agent_conversations.json"


@pytest.mark.asyncio
async def test_seed_skips_gracefully_without_fixture_or_creds(monkeypatch, tmp_path):
    """The seeding entry point must be a silent no-op in credential-less envs."""
    import api.seed_db as seed_db
    from api.src.utils import seed_fixture as sf

    for var in (
        "SEED_BUCKET_ENDPOINT_URL",
        "SEED_BUCKET_NAME",
        "SEED_BUCKET_ACCESS_KEY_ID",
        "SEED_BUCKET_SECRET_ACCESS_KEY",
    ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(sf, "FIXTURE_LOCAL_PATH", tmp_path / "missing.json")

    # Must not raise and must not touch the DB.
    await seed_db.seed_fixture_conversations(dry_run=True)
