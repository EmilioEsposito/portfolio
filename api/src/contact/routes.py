import uuid
from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
import logging

from api.src.database.database import get_session
from api.src.contact import service as contact_service
from api.src.contact.service import ContactCreate, ContactResponse, ContactUpdate
from api.src.utils.dependencies import verify_admin_or_serniacapital

logger = logging.getLogger(__name__)


router = APIRouter(
    prefix="/contacts",
    tags=["Contacts"],
    responses={404: {"description": "Not found"}},
    dependencies=[Depends(verify_admin_or_serniacapital)] 
)

@router.post("/", response_model=ContactResponse, status_code=status.HTTP_201_CREATED)
async def create_new_contact(
    contact: ContactCreate,
    db: Session = Depends(get_session)
):
    """
    Create a new contact.
    - **slug**: Unique, URL-friendly identifier (e.g., 'john-doe', 'internal-support').
    - **name**: Full name or display name of the contact.
    - **email**: Optional email address.
    - **phone_number**: Optional phone number.
    - **notes**: Optional notes about the contact.
    - **user_id**: Optional UUID of an existing user to associate with this contact.
    """
    logger.info(f"Attempting to create new contact with slug: {contact.slug}")
    try:
        created_contact = await contact_service.create_contact(db=db, contact_create=contact)
        logger.info(f"Successfully created contact with ID: {created_contact.id}")
        return created_contact
    except HTTPException as e:
        logger.error(f"Error creating contact: {e.detail}")
        raise
    except Exception as e:
        logger.error(f"Unexpected error creating contact: {str(e)}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Internal server error")

@router.get("/", response_model=List[ContactResponse])
async def read_all_contacts(
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_session)
):
    """
    Retrieve all contacts with pagination.
    """
    logger.info(f"Attempting to read all contacts with skip: {skip}, limit: {limit}")
    try:
        contacts = await contact_service.get_all_contacts(db, skip=skip, limit=limit)
        logger.info(f"Successfully retrieved {len(contacts)} contacts.")
        return contacts
    except Exception as e:
        logger.error(f"Unexpected error reading all contacts: {str(e)}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Internal server error")

@router.get("/id/{contact_id}", response_model=ContactResponse)
async def read_contact_by_id(
    contact_id: uuid.UUID,
    db: Session = Depends(get_session)
):
    """
    Get a specific contact by its UUID.
    """
    logger.info(f"Attempting to read contact by ID: {contact_id}")
    try:
        db_contact = await contact_service.get_contact_by_id(db, contact_id=contact_id)
        if db_contact is None:
            logger.warning(f"Contact with ID: {contact_id} not found.")
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Contact not found")
        logger.info(f"Successfully retrieved contact with ID: {contact_id}")
        return db_contact
    except HTTPException as e:
        # This will catch the 404 from above and re-raise it, logging is already done.
        # Or, if service layer raises an HTTPException for other reasons.
        if e.status_code != status.HTTP_404_NOT_FOUND: # Avoid double logging 404
             logger.error(f"Error reading contact by ID {contact_id}: {e.detail}")
        raise
    except Exception as e:
        logger.error(f"Unexpected error reading contact by ID {contact_id}: {str(e)}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Internal server error")

@router.get("/slug/{slug}", response_model=ContactResponse)
async def read_contact_by_slug(
    slug: str
):
    """
    Get a specific contact by its unique slug.
    """
    logger.info(f"Attempting to read contact by slug: {slug}")
    try:
        db_contact = await contact_service.get_contact_by_slug(slug=slug)
        if db_contact is None:
            logger.warning(f"Contact with slug: '{slug}' not found.")
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Contact with slug '{slug}' not found")
        logger.info(f"Successfully retrieved contact with slug: {slug}")
        return db_contact
    except HTTPException as e:
        if e.status_code != status.HTTP_404_NOT_FOUND:
            logger.error(f"Error reading contact by slug {slug}: {e.detail}")
        raise
    except Exception as e:
        logger.error(f"Unexpected error reading contact by slug {slug}: {str(e)}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Internal server error")

@router.put("/{contact_id}", response_model=ContactResponse)
async def update_existing_contact(
    contact_id: uuid.UUID,
    contact: ContactUpdate,
    db: Session = Depends(get_session)
):
    """
    Update an existing contact by its UUID. Fields not provided will remain unchanged.
    The slug of a contact cannot be changed.
    """
    logger.info(f"Attempting to update contact with ID: {contact_id}")
    try:
        updated_contact = await contact_service.update_contact(db, contact_id=contact_id, contact_update=contact)
        if updated_contact is None:
            logger.warning(f"Contact with ID: {contact_id} not found for update.")
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Contact not found")
        logger.info(f"Successfully updated contact with ID: {contact_id}")
        return updated_contact
    except HTTPException as e:
        if e.status_code != status.HTTP_404_NOT_FOUND:
             logger.error(f"Error updating contact {contact_id}: {e.detail}")
        raise
    except Exception as e:
        logger.error(f"Unexpected error updating contact {contact_id}: {str(e)}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Internal server error")

@router.delete("/{contact_id}", response_model=ContactResponse) # Or just status_code=204 if no body is returned
async def remove_contact(
    contact_id: uuid.UUID,
    db: Session = Depends(get_session)
):
    """
    Delete a contact by its UUID.
    """
    logger.info(f"Attempting to delete contact with ID: {contact_id}")
    try:
        deleted_contact = await contact_service.delete_contact(db, contact_id=contact_id)
        if deleted_contact is None:
            logger.warning(f"Contact with ID: {contact_id} not found for deletion.")
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Contact not found")
        logger.info(f"Successfully deleted contact with ID: {contact_id}")
        return deleted_contact # Or return a message like {"detail": "Contact deleted"}
    except HTTPException as e:
        if e.status_code != status.HTTP_404_NOT_FOUND:
            logger.error(f"Error deleting contact {contact_id}: {e.detail}")
        raise
    except Exception as e:
        logger.error(f"Unexpected error deleting contact {contact_id}: {str(e)}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Internal server error")
