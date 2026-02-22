from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy import MetaData
from sqlalchemy.pool import QueuePool
import os
import logfire
from contextlib import asynccontextmanager
from dotenv import load_dotenv, find_dotenv
from typing import AsyncGenerator, Annotated
from fastapi import Depends
from sqlalchemy import create_engine as create_sync_engine # Explicit import for clarity
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from sqlalchemy import text
import pytest
from api.src.utils.logfire_config import ensure_logfire_configured

# Load .env file if present (for local dev, alembic migrations, worktrees)
# This is idempotent - won't override existing environment variables
load_dotenv(find_dotenv(), override=False)

ensure_logfire_configured(mode="prod", service_name="fastapi")

# Configure SQLAlchemy to use lowercase, unquoted names by default
convention = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s"
}

# Create metadata with naming convention
metadata = MetaData(naming_convention=convention)

# Get the DATABASE_URL (pooled, for async app) and DATABASE_URL_UNPOOLED (unpooled, for sync app) from env variables
DATABASE_URL = os.environ.get("DATABASE_URL")
DATABASE_URL_UNPOOLED = os.environ.get("DATABASE_URL_UNPOOLED")
DATABASE_REQUIRE_SSL = bool(os.environ.get("DATABASE_REQUIRE_SSL", "true").lower() == "true")

if not DATABASE_URL:
    raise ValueError("DATABASE_URL environment variable is not set")
if not DATABASE_URL_UNPOOLED:
    raise ValueError("DATABASE_URL_UNPOOLED environment variable is not set")

_UNSUPPORTED_QUERY_PARAMS = {"sslmode", "channel_binding"}


def _remove_unsupported_query_params(url: str) -> str:
    """Remove query parameters that asyncpg does not support (e.g., sslmode, channel_binding)."""

    if not any(f"{param}=" in url for param in _UNSUPPORTED_QUERY_PARAMS):
        return url

    url_parts = urlsplit(url)
    filtered_params = [
        (k, v)
        for k, v in parse_qsl(url_parts.query, keep_blank_values=True)
        if k not in _UNSUPPORTED_QUERY_PARAMS
    ]
    cleaned_query = urlencode(filtered_params, doseq=True)
    return urlunsplit(url_parts._replace(query=cleaned_query))


# Remove parameters unsupported by asyncpg (e.g., sslmode, channel_binding)
# SSL is configured via connect_args["ssl"] instead of sslmode URL params
DATABASE_URL = _remove_unsupported_query_params(DATABASE_URL)


def _mask_credentials(url: str) -> str:
    if "@" not in url or "//" not in url:
        return url
    prefix, rest = url.split("//", 1)
    if "@" not in rest:
        return url
    credentials, remainder = rest.split("@", 1)
    return f"{prefix}//<credentials>@{remainder}"


db_url_for_logging = _mask_credentials(DATABASE_URL)
logfire.info(f"Database URL format: {db_url_for_logging}")

# Always convert to asyncpg format
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+asyncpg://")
elif DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://")

# Log the final URL format (with credentials removed)
final_url_for_logging = _mask_credentials(DATABASE_URL)
logfire.info(f"Final database URL format: {final_url_for_logging}")

logfire.info("Creating database engine...")

# Create async engine for FastAPI app (use Neon pooled connection)
async_connect_args = {
    "server_settings": {
        "quote_all_identifiers": "off",
        "application_name": "fastapi_app",
    },
    "command_timeout": 10,  # 10 second timeout on commands
    "statement_cache_size": 0,  # Disable statement cache for serverless
}

# SSL defaults to True (safe for production); set to False only for local development
async_connect_args["ssl"] = DATABASE_REQUIRE_SSL

engine = create_async_engine(
    DATABASE_URL,
    echo=False,  # Disable verbose SQL logging by default
    pool_size=5,  # Limit concurrent connections
    max_overflow=10,  # Allow up to 10 additional connections
    pool_timeout=30,  # Wait up to 30 seconds for a connection
    pool_recycle=300,  # Recycle connections every 5 minutes
    pool_pre_ping=True,  # Verify connection is alive before using
    connect_args=async_connect_args,
)

logfire.info("Creating session factory...")

# Session factory for FastAPI app
AsyncSessionFactory = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False
)

# Base class for models
class Base(DeclarativeBase):
    metadata = metadata

# --- Synchronous Engine for APScheduler and Alembic migrations ---
# Uses QueuePool (pool_size=2) instead of NullPool so APScheduler reuses
# connections. With NullPool every job-store operation opened a new TCP+TLS
# connection to Neon (~0.5s each), and because APScheduler v3's
# SQLAlchemyJobStore is synchronous it blocked the asyncio event loop for
# the entire duration. QueuePool keeps a small number of idle connections
# so subsequent operations are just pool checkouts (~0.07s).
sync_engine = None

if DATABASE_URL_UNPOOLED:
    sync_db_url = _remove_unsupported_query_params(DATABASE_URL_UNPOOLED)
    if sync_db_url.startswith("postgresql+asyncpg://"):
        sync_db_url = sync_db_url.replace("postgresql+asyncpg://", "postgresql://")
    elif sync_db_url.startswith("postgres://"): # Normalize postgres:// to postgresql://
        sync_db_url = sync_db_url.replace("postgres://", "postgresql://")
    # else, assume it's already a suitable synchronous URL like "postgresql://..."

    # Mask credentials for logging
    sync_url_for_logging = sync_db_url
    if "@" in sync_url_for_logging and "//" in sync_url_for_logging:
        parts = sync_url_for_logging.split("//")
        if len(parts) > 1 and "@" in parts[1]:
            credentials_part = parts[1].split("@")[0]
            sync_url_for_logging = sync_url_for_logging.replace(credentials_part, "<credentials>")

    logfire.info(f"Attempting to create synchronous engine with URL: {sync_url_for_logging}")
    logfire.info(f"DATABASE_REQUIRE_SSL value: {os.environ.get('DATABASE_REQUIRE_SSL', 'NOT SET')}, parsed as: {DATABASE_REQUIRE_SSL}")
    try:
        # See module-level comment above for why QueuePool over NullPool.
        sync_connect_args = {"sslmode": "require"} if DATABASE_REQUIRE_SSL else {"sslmode": "disable"}
        logfire.info(f"Using sync_connect_args: {sync_connect_args}")
        sync_engine = create_sync_engine(
            sync_db_url,
            echo=os.getenv("DEBUG_SYNC_SQL", "False").lower() == "true",
            poolclass=QueuePool,
            pool_size=2,
            max_overflow=0,
            pool_recycle=300,  # Recycle every 5 min to avoid stale Neon connections
            pool_pre_ping=True,
            connect_args=sync_connect_args,
        )
        ssl_status = "with SSL" if DATABASE_REQUIRE_SSL else "without SSL"
        logfire.info(f"Synchronous SQLAlchemy engine created successfully with QueuePool {ssl_status}.")
    except Exception as e:
        logfire.exception(f"Failed to create synchronous SQLAlchemy engine: {e}")
        sync_engine = None # Ensure it's None if creation failed
elif not DATABASE_URL_UNPOOLED:
    logfire.warn("DATABASE_URL_UNPOOLED not set, synchronous engine cannot be created.")

@asynccontextmanager
async def session_context() -> AsyncGenerator[AsyncSession, None]:
    """Async context manager for database sessions.
    Use with 'async with' statements.
    """
    async with AsyncSessionFactory() as session:
        try:
            yield session
            await session.commit()
        except Exception as e:
            await session.rollback()
            logfire.exception(f"Database session error: {str(e)}")
            raise

# Session dependency for FastAPI
async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """Database session as a FastAPI dependency.
    Use with FastAPI Depends().
    """
    async with AsyncSessionFactory() as session:
        try:
            yield session
            await session.commit()
        except Exception as e:
            await session.rollback()
            logfire.exception(f"Database session error: {str(e)}")
            raise


# Type alias for dependency injection - use this in route handlers:
#   async def my_route(session: DBSession):
# Instead of:
#   async def my_route(session: AsyncSession = Depends(get_session)):
DBSession = Annotated[AsyncSession, Depends(get_session)]

@asynccontextmanager
async def provide_session(session: AsyncSession | None = None) -> AsyncGenerator[AsyncSession, None]:
    """
    Context manager that yields the provided session if it exists,
    or creates a new one using AsyncSessionFactory.
    
    Useful for functions that can be called either with an existing session (e.g. from a route)
    or standalone (e.g. from a script).
    """
    if session:
        yield session
    else:
        async with AsyncSessionFactory() as new_session:
            yield new_session

@logfire.instrument("test-sync-engine-select-one")
def test_sync_engine_select_one():
    """Run a SELECT 1 from the synchronous engine to verify it's working."""
    try:
        with sync_engine.connect() as conn:
            result = conn.execute(text("SELECT 1"))
            result = result.fetchone()[0]
            assert result==1, f"Synchronous engine SELECT 1 test failed. Result: {result}"
            logfire.info("SUCCESS: Synchronous engine SELECT 1 test passed successfully.")
    except Exception as e:
        logfire.exception(f"FAILURE: Synchronous engine SELECT 1 test failed! Exception: {e}")
        raise Exception(f"Synchronous engine SELECT 1 test failed: {e}")


def wait_for_db(max_retries=10, delay=1):
    """Wait for database to be ready with simple retry logic."""
    import time
    for i in range(max_retries):
        try:
            test_sync_engine_select_one()
            return
        except:
            if i == max_retries - 1:
                raise
            time.sleep(delay)

@pytest.mark.asyncio
@logfire.instrument("test-async-engine-select-one")
async def test_async_engine_select_one():
    """Run a SELECT 1 from the async engine to verify it's working."""
    try:
        async with engine.connect() as conn:
            result = await conn.execute(text("SELECT 1"))
            result = result.scalar_one()
            assert result == 1, f"Async engine SELECT 1 test failed. Result: {result}"
            logfire.info("SUCCESS: Async engine SELECT 1 test passed successfully.")
    except Exception as e:
        logfire.exception(f"FAILURE: Async engine SELECT 1 test failed! Exception: {e}")
        raise Exception(f"Async engine SELECT 1 test failed: {e}")

@pytest.mark.asyncio
@logfire.instrument("test-database-connections")
async def test_database_connections():
    try:
        test_sync_engine_select_one()
        await test_async_engine_select_one()
    except Exception as e:
        logfire.exception(f"Error during database connection test: {e}")
        raise Exception(f"Error during database connection test: {e}")
