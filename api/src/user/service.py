import logfire
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import NoResultFound, IntegrityError
from datetime import datetime, timezone

from api.src.user.models import User


def _extract_primary_email(data: dict) -> str | None:
    """Extracts the primary email address from the Clerk user data."""
    primary_email_id = data.get('primary_email_address_id')
    if not primary_email_id:
        return None
    for email_data in data.get('email_addresses', []):
        if email_data.get('id') == primary_email_id:
            return email_data.get('email_address')
    return None

def _timestamp_ms_to_datetime(timestamp_ms: int | None) -> datetime | None:
    """Converts a Unix timestamp in milliseconds to a naive UTC datetime object."""
    if timestamp_ms is None:
        return None
    try:
        # Convert milliseconds to seconds
        timestamp_sec = timestamp_ms / 1000.0
        return datetime.fromtimestamp(timestamp_sec, tz=timezone.utc).replace(tzinfo=None)
    except (ValueError, TypeError):
        logfire.warn(f"Could not convert timestamp: {timestamp_ms}")
        return None

async def upsert_user(db: AsyncSession, event_data: dict, environment: str):
    """Creates a new user or updates an existing one based on Clerk webhook data."""
    clerk_user_id = event_data.get('id')
    if not clerk_user_id:
        logfire.error("Missing 'id' (clerk_user_id) in webhook data.")
        raise ValueError("Missing clerk_user_id in webhook data")

    logfire.info(f"Upserting user: clerk_id={clerk_user_id}, env={environment}")

    # Extract data from the payload
    clerk_created_at_ts = event_data.get('created_at')
    clerk_updated_at_ts = event_data.get('updated_at')

    # Convert timestamps
    clerk_created_at_dt = _timestamp_ms_to_datetime(clerk_created_at_ts)
    clerk_updated_at_dt = _timestamp_ms_to_datetime(clerk_updated_at_ts)

    if not clerk_created_at_dt or not clerk_updated_at_dt:
         raise ValueError("Missing or invalid created_at/updated_at timestamp in webhook data")

    primary_email = _extract_primary_email(event_data)

    user_data = {
        "clerk_user_id": clerk_user_id,
        "email": primary_email,
        "first_name": event_data.get('first_name'),
        "last_name": event_data.get('last_name'),
        "image_url": "event_data.get('image_url')",
        "clerk_created_at": clerk_created_at_dt,
        "clerk_updated_at": clerk_updated_at_dt,
        "public_metadata": event_data.get('public_metadata', {}),
        "private_metadata": event_data.get('private_metadata', {}),
        "environment": environment,
        "raw_payload": event_data,
        # created_at and updated_at are handled by the database
    }

    # Try to find existing user
    stmt = select(User).where(
        User.clerk_user_id == clerk_user_id,
        User.environment == environment
    )
    result = await db.execute(stmt)
    existing_user = result.scalars().first()

    if existing_user:
        # Update existing user
        logfire.info(f"Updating existing user: {existing_user.id}")
        for key, value in user_data.items():
            setattr(existing_user, key, value)
        db.add(existing_user)
        await db.flush() # Use flush to ensure the user object is updated
        logfire.info(f"User {existing_user.id} updated successfully.")
        return f"User {existing_user.id} updated successfully."
    else:
        # Create new user
        logfire.info(f"Creating new user for clerk_id={clerk_user_id}")
        new_user = User(**user_data)
        db.add(new_user)
        try:
            await db.flush() # Flush to get the new user ID and check constraints
            await db.refresh(new_user) # Refresh to get db-generated fields like id, created_at
            logfire.info(f"User {new_user.id} created successfully.")
            return f"User {new_user.id} created successfully."
        except IntegrityError as e:
            logfire.error(f"Integrity error creating user (likely duplicate): {e}")
            await db.rollback() # Rollback the specific failed operation
            # Re-fetch just in case there was a race condition
            result = await db.execute(stmt)
            existing_user = result.scalars().first()
            if existing_user:
                 logfire.warn(f"Found user {existing_user.id} after IntegrityError, proceeding as update.")
                 # Update the re-fetched user
                 for key, value in user_data.items():
                     setattr(existing_user, key, value)
                 db.add(existing_user)
                 await db.flush()
                 return f"User {existing_user.id} updated successfully after race condition."
            else:
                logfire.error("Failed to find user even after IntegrityError rollback.")
                raise # Re-raise the original integrity error if user still not found
        except Exception as e:
             logfire.error(f"Error creating user: {e}")
             await db.rollback()
             raise

async def delete_user(db: AsyncSession, event_data: dict, environment: str):
    """Deletes a user based on Clerk webhook data."""
    clerk_user_id = event_data.get('id')
    if not clerk_user_id:
        logfire.error("Missing 'id' (clerk_user_id) in delete event data.")
        raise ValueError("Missing clerk_user_id in delete event data")

    logfire.info(f"Attempting to delete user: clerk_id={clerk_user_id}, env={environment}")

    stmt = delete(User).where(
        User.clerk_user_id == clerk_user_id,
        User.environment == environment
    )
    result = await db.execute(stmt)
    message = ""

    if result.rowcount == 0:
        logfire.warn(f"User not found for deletion: clerk_id={clerk_user_id}, env={environment}")
        # Decide if this should be an error or just a warning
        # raise NoResultFound(f"User not found for deletion: {clerk_user_id}")
        message = "User not found for deletion"
    else:
        logfire.info(f"User deleted successfully: clerk_id={clerk_user_id}, env={environment}")
        message = "User deleted successfully"

    # No need to flush/commit here, handled by the session context/dependency 

    return message