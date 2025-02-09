from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy import MetaData
import os
import logging
from contextlib import asynccontextmanager
from dotenv import load_dotenv, find_dotenv
from typing import AsyncGenerator
import asyncio

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

def create_engine():
    """Create a new engine instance with appropriate settings"""
    return create_async_engine(
        DATABASE_URL,
        echo=True,  # Enable SQL logging
        pool_size=1,  # Minimal pool for serverless
        max_overflow=0,  # No overflow in serverless
        pool_timeout=30,  # Shorter timeout
        pool_pre_ping=True,  # Enable connection health checks
        pool_use_lifo=True,  # Last In First Out - better for serverless
        connect_args={
            "ssl": True,
            "server_settings": {
                "quote_all_identifiers": "off",
                "application_name": "fastapi_app",
            },
            "command_timeout": 10  # 10 second timeout on commands
        }
    )

# Create engine
engine = create_engine()

logger.info("Creating session factory...")

# Session factory with appropriate settings for serverless
AsyncSessionFactory = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False  # Disable autoflush for better performance
)

# Base class for models
class Base(DeclarativeBase):
    metadata = metadata

# Session context manager
@asynccontextmanager
async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """Get a database session with proper error handling and cleanup"""
    session = AsyncSessionFactory()
    try:
        logger.info("Starting new database session")
        yield session
        await session.commit()
        logger.info("Session committed successfully")
    except Exception as e:
        await session.rollback()
        logger.error(f"Database session error: {str(e)}", exc_info=True)
        raise
    finally:
        await session.close()
        logger.info("Session closed") 