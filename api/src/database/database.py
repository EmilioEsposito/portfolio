from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy import MetaData
from sqlalchemy.pool import NullPool
import os
import logging
from contextlib import asynccontextmanager
from dotenv import load_dotenv, find_dotenv
from typing import AsyncGenerator
import asyncio
from sqlalchemy import create_engine as create_sync_engine # Explicit import for clarity

logger = logging.getLogger(__name__)

# Enable SQLAlchemy logging for debugging
# logging.getLogger('sqlalchemy.engine').setLevel(logging.WARNING)  # Removed, now handled in index.py

load_dotenv(find_dotenv(".env.development.local"), override=True)

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
if not DATABASE_URL:
    raise ValueError("DATABASE_URL environment variable is not set")
if not DATABASE_URL_UNPOOLED:
    raise ValueError("DATABASE_URL_UNPOOLED environment variable is not set")

# Remove sslmode from URL if present and log the URL (with credentials removed)
if "sslmode=" in DATABASE_URL:
    DATABASE_URL = DATABASE_URL.split("?")[0]

db_url_for_logging = DATABASE_URL.replace(
    "//" + DATABASE_URL.split("@")[0].split("//")[1],
    "//<credentials>"
)
logger.info(f"Database URL format: {db_url_for_logging}")

# Always convert to asyncpg format
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+asyncpg://")
elif DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://")

# Log the final URL format (with credentials removed)
final_url_for_logging = DATABASE_URL.replace(
    "//" + DATABASE_URL.split("@")[0].split("//")[1],
    "//<credentials>"
)
logger.info(f"Final database URL format: {final_url_for_logging}")

logger.info("Creating database engine...")

# Create async engine for FastAPI app (use Neon pooled connection)
engine = create_async_engine(
    DATABASE_URL,
    echo=False,  # Disable verbose SQL logging by default
    pool_size=5,  # Limit concurrent connections
    max_overflow=10,  # Allow up to 10 additional connections
    pool_timeout=30,  # Wait up to 30 seconds for a connection
    pool_recycle=300,  # Recycle connections every 5 minutes
    pool_pre_ping=True,  # Verify connection is alive before using
    connect_args={
        "ssl": True,  # Enable SSL
        "server_settings": {
            "quote_all_identifiers": "off",
            "application_name": "fastapi_app",
        },
        "command_timeout": 10,  # 10 second timeout on commands
        "statement_cache_size": 0,  # Disable statement cache for serverless
    }
)

logger.info("Creating session factory...")

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

# --- Synchronous Engine for tools like APScheduler or Alembic migrations ---
# Use unpooled connection for sync engine
sync_engine = None

if DATABASE_URL_UNPOOLED:
    sync_db_url = DATABASE_URL_UNPOOLED
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

    logger.info(f"Attempting to create synchronous engine with URL: {sync_url_for_logging}")
    try:
        # For a background service like APScheduler, use NullPool for serverless compatibility.
        # echo=False is common for sync engines unless specific SQL debugging is needed.
        sync_engine = create_sync_engine(
            sync_db_url,
            echo=os.getenv("DEBUG_SYNC_SQL", "False").lower() == "true",
            poolclass=NullPool,  # Use NullPool for serverless
            connect_args={"sslmode": "require"}  # Ensure SSL is used
        )
        logger.info("Synchronous SQLAlchemy engine created successfully with NullPool and SSL.")
    except Exception as e:
        logger.error(f"Failed to create synchronous SQLAlchemy engine: {e}", exc_info=True)
        sync_engine = None # Ensure it's None if creation failed
elif not DATABASE_URL_UNPOOLED:
    logger.warning("DATABASE_URL_UNPOOLED not set, synchronous engine cannot be created.")

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
            logger.error(f"Database session error: {str(e)}", exc_info=True)
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
            logger.error(f"Database session error: {str(e)}", exc_info=True)
            raise 

