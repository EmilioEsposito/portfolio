import uuid
from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from api.src.database.database import get_session
from api.src.contact import service as contact_service
from api.src.contact.service import ContactCreate, ContactResponse, ContactUpdate
from api.src.utils.dependencies import verify_serniacapital_user

router = APIRouter(
    prefix="/contacts",
    tags=["Contacts"],
    responses={404: {"description": "Not found"}},
    dependencies=[Depends(verify_serniacapital_user)]
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
    return await contact_service.create_contact(db=db, contact_create=contact)

@router.get("/", response_model=List[ContactResponse])
async def read_all_contacts(
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_session)
):
    """
    Retrieve all contacts with pagination.
    """
    contacts = await contact_service.get_all_contacts(db, skip=skip, limit=limit)
    return contacts

@router.get("/id/{contact_id}", response_model=ContactResponse)
async def read_contact_by_id(
    contact_id: uuid.UUID,
    db: Session = Depends(get_session)
):
    """
    Get a specific contact by its UUID.
    """
    db_contact = await contact_service.get_contact_by_id(db, contact_id=contact_id)
    if db_contact is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Contact not found")
    return db_contact

@router.get("/slug/{slug}", response_model=ContactResponse)
async def read_contact_by_slug(
    slug: str
):
    """
    Get a specific contact by its unique slug.
    """
    db_contact = await contact_service.get_contact_by_slug(slug=slug)
    if db_contact is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Contact with slug '{slug}' not found")
    return db_contact

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
    updated_contact = await contact_service.update_contact(db, contact_id=contact_id, contact_update=contact)
    if updated_contact is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Contact not found")
    return updated_contact

@router.delete("/{contact_id}", response_model=ContactResponse) # Or just status_code=204 if no body is returned
async def remove_contact(
    contact_id: uuid.UUID,
    db: Session = Depends(get_session)
):
    """
    Delete a contact by its UUID.
    """
    deleted_contact = await contact_service.delete_contact(db, contact_id=contact_id)
    if deleted_contact is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Contact not found")
    return deleted_contact # Or return a message like {"detail": "Contact deleted"}
