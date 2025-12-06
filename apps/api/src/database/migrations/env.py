import asyncio
from logging.config import fileConfig
import re
from datetime import datetime
import logging

from alembic import context
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config


# Import your models - using explicit imports for clarity
from apps.api.src.examples.models import *  # noqa
from apps.api.src.google.gmail.models import *  # noqa
from apps.api.src.oauth.models import *  # noqa
from apps.api.src.open_phone.models import *  # noqa
from apps.api.src.push.models import *  # noqa
from apps.api.src.user.models import *  # noqa
from apps.api.src.contact.models import *  # noqa
from apps.api.src.database.database import DATABASE_URL, engine, Base


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger('alembic.env')

# Log the tables that SQLAlchemy knows about
logging.info("Detected tables in metadata:")
for table in Base.metadata.tables:
    logging.info(f"  - {table}")

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
# This line sets up logging. basically.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# add your model's MetaData object here
# for 'autogenerate' support
target_metadata = Base.metadata

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
        description = re.sub(r'[-\s]+', '_', description).rstrip('_')
        print(f"Description: {description}")
        # Set the rev_id to include the description
        migration_script.rev_id = f"{new_rev_id}_{description}"
        # Clear the message so it won't be used in the filename
        migration_script.message = ''
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
