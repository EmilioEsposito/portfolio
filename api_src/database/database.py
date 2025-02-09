from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase
import os
from contextlib import asynccontextmanager
from dotenv import load_dotenv, find_dotenv
from typing import AsyncGenerator

load_dotenv(find_dotenv(".env.development.local"), override=True)


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
    connect_args={"ssl": True}  # This is the correct way to enable SSL for asyncpg
)

# Session factory
AsyncSessionFactory = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)

# Base class for declarative models
class Base(DeclarativeBase):
    pass

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