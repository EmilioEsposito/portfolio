"""S3-compatible object storage client for the seed-fixture bucket (Railway).

The bucket holds the sanitized seed fixture (too large for git, and may
contain unreviewed PII — must not live in the public repo). Credentials come
from the Railway bucket's Credentials tab and are set as SEED_BUCKET_* env
vars in: local .env, GitHub Actions secrets, and the Claude Code environment
config. All four vars are required; `get_seed_bucket()` returns None when any
are missing so callers can no-op gracefully.

Railway buckets require virtual-hosted-style URLs (per the bucket UI).
"""
import os
from dataclasses import dataclass


@dataclass(frozen=True)
class SeedBucket:
    endpoint_url: str
    bucket_name: str
    access_key_id: str
    secret_access_key: str

    FIXTURE_KEY = "seed_fixtures/agent_conversations.json"

    def client(self):
        import boto3
        from botocore.config import Config

        return boto3.client(
            "s3",
            endpoint_url=self.endpoint_url,
            aws_access_key_id=self.access_key_id,
            aws_secret_access_key=self.secret_access_key,
            region_name="auto",
            config=Config(s3={"addressing_style": "virtual"}),
        )

    def upload_fixture(self, local_path: str) -> None:
        self.client().upload_file(local_path, self.bucket_name, self.FIXTURE_KEY)

    def download_fixture(self, local_path: str) -> None:
        self.client().download_file(self.bucket_name, self.FIXTURE_KEY, local_path)


def get_seed_bucket() -> SeedBucket | None:
    """Build a SeedBucket from SEED_BUCKET_* env vars, or None if unconfigured."""
    endpoint = os.getenv("SEED_BUCKET_ENDPOINT_URL")
    name = os.getenv("SEED_BUCKET_NAME")
    key_id = os.getenv("SEED_BUCKET_ACCESS_KEY_ID")
    secret = os.getenv("SEED_BUCKET_SECRET_ACCESS_KEY")
    if not endpoint or not name or not key_id or not secret:
        return None
    return SeedBucket(
        endpoint_url=endpoint,
        bucket_name=name,
        access_key_id=key_id,
        secret_access_key=secret,
    )
