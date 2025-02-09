from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy import MetaData
import os
import logging
from contextlib import asynccontextmanager
from dotenv import load_dotenv, find_dotenv
from typing import AsyncGenerator

# Configure logging with more detailed format for debugging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Enable SQLAlchemy logging for debugging
logging.getLogger('sqlalchemy.engine').setLevel(logging.INFO)

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
DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    raise ValueError("DATABASE_URL environment variable is not set")

# Log the database URL (with credentials removed) for debugging
db_url_for_logging = DATABASE_URL.replace(
    "//" + DATABASE_URL.split("@")[0].split("//")[1],
    "//<credentials>"
)
logger.info(f"Database URL format: {db_url_for_logging}")

# Convert the URL to async format
if DATABASE_URL.startswith("postgres://"):
    base_url = DATABASE_URL.split("?")[0]
    DATABASE_URL = base_url.replace("postgres://", "postgresql+asyncpg://")

logger.info("Creating database engine...")

# Create async engine with minimal settings
engine = create_async_engine(
    DATABASE_URL,
    echo=True,  # Enable SQL logging
    pool_size=5,  # Small pool size for better reliability
    max_overflow=3,  # Allow small overflow
    pool_pre_ping=True,  # Enable connection health checks
    connect_args={
        "ssl": True,
        "server_settings": {
            "quote_all_identifiers": "off",
            "application_name": "fastapi_app",
        }
    }
)

logger.info("Creating session factory...")

# Basic session factory
AsyncSessionFactory = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)

# Base class for models
class Base(DeclarativeBase):
    metadata = metadata

# Session context manager
@asynccontextmanager
async def get_session() -> AsyncGenerator[AsyncSession, None]:
    logger.info("Starting new database session")
    async with AsyncSessionFactory() as session:
        try:
            yield session
            await session.commit()
            logger.info("Session committed successfully")
        except Exception as e:
            await session.rollback()
            logger.error(f"Database session error: {str(e)}", exc_info=True)
            raise 