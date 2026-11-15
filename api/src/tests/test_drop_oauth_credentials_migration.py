"""Exercise the destructive migration against isolated, rolled-back schemas."""

from importlib import import_module
from uuid import uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import inspect, text
from sqlalchemy.exc import DBAPIError

from api.src.database.database import sync_engine

migration = import_module("api.src.database.migrations.versions.0041_drop_oauth_credentials")


@pytest.fixture
def migration_connection(monkeypatch):
    with sync_engine.connect() as connection, connection.begin():
        schema = f"test_oauth_drop_{uuid4().hex}"
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        connection.execute(text(f'SET LOCAL search_path TO "{schema}"'))
        operations = Operations(MigrationContext.configure(connection))
        monkeypatch.setattr(migration, "op", operations)
        # Rebuild the historical schema, rather than using the new downgrade
        # as its own oracle. 0006 replaces this predecessor table.
        connection.execute(text("CREATE TABLE google_oauth_tokens (id integer)"))
        for name in ("0006_oauth_credentials_", "0007_naive_expiry_oauth_"):
            old = import_module(f"api.src.database.migrations.versions.{name}")
            monkeypatch.setattr(old, "op", operations)
            old.upgrade()
        yield connection
        # Roll back all test DDL and data; never touch the application's schema.
        connection.rollback()


def test_empty_cache_is_dropped_and_downgrade_restores_schema(migration_connection):
    connection = migration_connection
    original = inspect(connection).get_columns("oauth_credentials")
    migration.upgrade()
    assert connection.scalar(text("SELECT to_regclass('oauth_credentials')")) is None
    migration.downgrade()
    restored = inspect(connection).get_columns("oauth_credentials")
    assert [(c["name"], str(c["type"]), c["nullable"]) for c in restored] == [
        (c["name"], str(c["type"]), c["nullable"]) for c in original
    ]
    assert connection.scalar(text("SELECT count(*) FROM oauth_credentials")) == 0
    assert {i["name"] for i in inspect(connection).get_indexes("oauth_credentials")} == {
        "ix_oauth_credentials_provider",
        "ix_oauth_credentials_user_id",
        "uix_user_provider",
    }


def test_nonempty_cache_is_preserved(migration_connection):
    connection = migration_connection
    connection.execute(
        text("""
            INSERT INTO oauth_credentials
                (user_id, provider, provider_user_id, access_token, token_type,
                 expires_at, scopes, raw_response)
            VALUES ('test-user', 'test-provider', 'test-id', 'synthetic-test-value',
                    'Bearer', now(), '[]', '{}')
        """)
    )
    with pytest.raises(DBAPIError, match="Refusing to drop nonempty oauth_credentials"):
        with connection.begin_nested():
            migration.upgrade()
    assert connection.scalar(text("SELECT count(*) FROM oauth_credentials")) == 1
    assert (
        connection.scalar(text("SELECT access_token FROM oauth_credentials"))
        == "synthetic-test-value"
    )


def test_exclusive_lock_is_held_until_transaction_ends(migration_connection):
    connection = migration_connection
    table_oid = connection.scalar(text("SELECT 'oauth_credentials'::regclass::oid"))
    migration.upgrade()
    assert connection.scalar(
        text("""
            SELECT EXISTS (
                SELECT 1 FROM pg_locks
                WHERE pid = pg_backend_pid() AND relation = :oid
                  AND mode = 'AccessExclusiveLock' AND granted
            )
        """),
        {"oid": table_oid},
    )
