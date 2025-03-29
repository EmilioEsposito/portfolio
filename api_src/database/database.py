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


# Configure logging with more detailed format for debugging
logging.basicConfig(
    level=logging.WARNING,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Enable SQLAlchemy logging for debugging
logging.getLogger('sqlalchemy.engine').setLevel(logging.WARNING)

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

# Get the DATABASE_URL from environment variables
DATABASE_URL = os.environ.get("DATABASE_URL_UNPOOLED")
if not DATABASE_URL:
    raise ValueError("DATABASE_URL_UNPOOLED environment variable is not set")

# Remove sslmode from URL if present and log the URL (with credentials removed)
if "sslmode=" in DATABASE_URL:
    DATABASE_URL = DATABASE_URL.split("?")[0]

db_url_for_logging = DATABASE_URL.replace(
    "//" + DATABASE_URL.split("@")[0].split("//")[1],
    "//<credentials>"
)
logging.info(f"Database URL format: {db_url_for_logging}")

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
logging.info(f"Final database URL format: {final_url_for_logging}")

logging.info("Creating database engine...")

# Create engine with NullPool - no connection pooling for serverless
engine = create_async_engine(
    DATABASE_URL,
    echo=True,  # Enable SQL logging
    poolclass=NullPool,  # Disable connection pooling
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

logging.info("Creating session factory...")

# Session factory with appropriate settings for serverless
AsyncSessionFactory = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False
)

# Base class for models
class Base(DeclarativeBase):
    metadata = metadata

# Session dependency for FastAPI
async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """Get a database session with proper error handling and cleanup.
    This is designed to work with FastAPI's dependency injection system.
    """
    async with AsyncSessionFactory() as session:
        try:
            yield session
            await session.commit()
        except Exception as e:
            await session.rollback()
            logging.error(f"Database session error: {str(e)}", exc_info=True)
            raise 