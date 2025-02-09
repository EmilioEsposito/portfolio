from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy import MetaData
import os
from contextlib import asynccontextmanager
from dotenv import load_dotenv, find_dotenv
from typing import AsyncGenerator

load_dotenv(find_dotenv(".env.development.local"), override=True)

# Configure SQLAlchemy to use lowercase, unquoted names by default
convention = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s"
}

# Create metadata with naming convention and quote policy
metadata = MetaData(naming_convention=convention)

# Get the DATABASE_URL from environment variables
DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    raise ValueError("DATABASE_URL environment variable is not set")

# Convert the URL to async format
if DATABASE_URL.startswith("postgres://"):
    # Remove any existing SSL parameters
    DATABASE_URL = DATABASE_URL.replace("?sslmode=require", "").replace("?ssl=true", "")
    # Convert to asyncpg format
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+asyncpg://")

# Create async engine with SSL config in connect_args
engine = create_async_engine(
    DATABASE_URL,
    echo=False,  # Set to True for SQL logging
    pool_size=5,
    max_overflow=10,
    connect_args={
        "ssl": True,  # Enable SSL for asyncpg
        "server_settings": {
            "quote_all_identifiers": "off"  # Ensure identifiers are never quoted unless they contain special characters
        }
    }
)

# Session factory
AsyncSessionFactory = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)

# Base class for declarative models
class Base(DeclarativeBase):
    # Use the configured metadata for all models
    metadata = metadata

# Context manager for database sessions
@asynccontextmanager
async def get_session() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionFactory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise 