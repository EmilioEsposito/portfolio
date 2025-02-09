import asyncio
from logging.config import fileConfig
import re
from datetime import datetime

from alembic import context
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

# Import your models
from api_src.examples.models import Example
from api_src.database.database import DATABASE_URL, engine

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
# This line sets up loggers basically.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# add your model's MetaData object here
# for 'autogenerate' support
target_metadata = Example.metadata

def get_next_revision_id():
    """Get the next sequential revision ID."""
    import os
    
    versions_dir = os.path.join(os.path.dirname(__file__), 'versions')
    if not os.path.exists(versions_dir):
        return "0001"
        
    # Get all migration files
    migration_files = [f for f in os.listdir(versions_dir) if f.endswith('.py')]
    if not migration_files:
        return "0001"
    
    # Extract numbers from filenames
    numbers = []
    for filename in migration_files:
        match = re.match(r'(\d{4})_.*\.py', filename)
        if match:
            numbers.append(int(match.group(1)))
    
    # Get the next number
    next_num = max(numbers) + 1 if numbers else 1
    return f"{next_num:04d}"

def process_revision_directives(context, revision, directives):
    """Customize how revisions are generated."""
    if not directives:
        return

    # Get the migration script
    migration_script = directives[0]
    
    # Create new sequential revision ID with description
    new_rev_id = get_next_revision_id()
    description = migration_script.message
    if description:
        # Convert description to snake case and clean it
        description = re.sub(r'[^\w\s-]', '', description.lower())
        description = re.sub(r'[-\s]+', '_', description)
        migration_script.rev_id = f"{new_rev_id}_{description}"
    else:
        migration_script.rev_id = new_rev_id

def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.

    """
    url = DATABASE_URL
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        process_revision_directives=process_revision_directives,
    )

    with context.begin_transaction():
        context.run_migrations()

def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        process_revision_directives=process_revision_directives,
    )

    with context.begin_transaction():
        context.run_migrations()

async def run_async_migrations() -> None:
    """Run migrations in 'online' mode.

    In this scenario we need to create an Engine
    and associate a connection with the context.

    """
    async with engine.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await engine.dispose()

def run_migrations_online() -> None:
    """Run migrations in 'online' mode.

    In this scenario we need to create an Engine
    and associate a connection with the context.

    """
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
