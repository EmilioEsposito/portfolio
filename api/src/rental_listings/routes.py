"""HTTP endpoints for rental listings, used by the edit screen and other services.

Signed-in Sernia users only, the same as the Sernia admin settings. A
person's own edit here is saved without an approval step: the approval the
general agent's tools ask for exists because an agent may be acting on what a
stranger wrote, and here the person is the one typing. The checks in
``checks.py`` still apply to every save, and every save is recorded in the
history with the person's name.

A write may send ``based_on``: the listing (or the rules) as the caller last read
it. If what is on file has changed since, nothing is saved and the answer is
409, so an edit made from a stale screen cannot undo someone else's change.
"""

from typing import Annotated

from clerk_backend_api import User
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict

from api.src.database.database import DBSession
from api.src.rental_listings import service
from api.src.rental_listings.checks import RentalDataInvalid
from api.src.rental_listings.schema import Change, RentalListing, ShowingRules
from api.src.utils.clerk import verify_serniacapital_user

# Recorded as the source of every change made through these endpoints.
SOURCE = "rental listings screen"

router = APIRouter(
    prefix="/rental-listings",
    tags=["rental-listings"],
    dependencies=[Depends(verify_serniacapital_user)],
)

SerniaUser = Annotated[User, Depends(verify_serniacapital_user)]


class RentalData(BaseModel):
    listings: list[RentalListing]
    showing_rules: ShowingRules


class ListingSave(BaseModel):
    model_config = ConfigDict(extra="forbid")

    listing: RentalListing
    # The listing as the caller last read it. Leave out to save regardless.
    based_on: RentalListing | None = None


class RulesSave(BaseModel):
    model_config = ConfigDict(extra="forbid")

    showing_rules: ShowingRules
    based_on: ShowingRules | None = None


def _person(user: User) -> str:
    """The name recorded as ``changed_by``, with the email so two people cannot blur."""
    name = f"{user.first_name or ''} {user.last_name or ''}".strip()
    email = next(
        (
            address.email_address
            for address in user.email_addresses or []
            if address.email_address.endswith("@serniacapital.com")
        ),
        None,
    )
    if name and email:
        return f"{name} <{email}>"
    return name or email or user.id


def _refused(error: RentalDataInvalid) -> HTTPException:
    return HTTPException(
        status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail={"message": "Not saved.", "problems": error.problems},
    )


def _changed_since() -> HTTPException:
    return HTTPException(
        status.HTTP_409_CONFLICT,
        detail={
            "message": "Not saved.",
            "problems": ["Someone changed this after you loaded it. Reload and try again."],
        },
    )


def _broken_on_file(error: RentalDataInvalid) -> HTTPException:
    """Only a hand edit in the database can store data that fails its checks."""
    return HTTPException(
        status.HTTP_409_CONFLICT,
        detail={"message": "What is on file fails its checks.", "problems": error.problems},
    )


async def _saved_listing(session: DBSession, name: str) -> RentalListing:
    listing = await service.read_listing(session, name)
    assert listing is not None  # just saved
    return listing


@router.get("", response_model=RentalData)
async def read_rental_data(session: DBSession) -> RentalData:
    """Every listing, by unit, and the showing rules."""
    try:
        return RentalData(
            listings=await service.read_listings(session),
            showing_rules=await service.read_showing_rules(session),
        )
    except RentalDataInvalid as error:
        raise _broken_on_file(error) from error


@router.post("/listings", response_model=RentalListing, status_code=status.HTTP_201_CREATED)
async def create_listing(body: ListingSave, user: SerniaUser, session: DBSession) -> RentalListing:
    """Add a unit's listing. Refused with 409 if that unit (in any case) has one."""
    name = body.listing.unit
    try:
        existing = await service.read_listing(session, name, lock=True)
    except RentalDataInvalid as error:
        raise _broken_on_file(error) from error
    if existing is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail={"message": "Not saved.", "problems": [f"Unit {name!r} already has a listing."]},
        )
    try:
        await service.save_listing(session, body.listing, changed_by=_person(user), source=SOURCE)
    except RentalDataInvalid as error:
        raise _refused(error) from error
    return await _saved_listing(session, name)


@router.put("/listings/{name}", response_model=RentalListing)
async def update_listing(
    name: str, body: ListingSave, user: SerniaUser, session: DBSession
) -> RentalListing:
    """Replace a listing. The unit may change case but not spelling: it is not renamed."""
    if body.listing.unit.strip().casefold() != name.strip().casefold():
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "message": "Not saved.",
                "problems": [f"The unit in the body is {body.listing.unit!r}, not {name!r}."],
            },
        )
    try:
        current = await service.read_listing(session, name, lock=True)
    except RentalDataInvalid as error:
        raise _broken_on_file(error) from error
    if current is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"No listing for unit {name!r}.")
    if body.based_on is not None and current != body.based_on:
        raise _changed_since()
    try:
        await service.save_listing(session, body.listing, changed_by=_person(user), source=SOURCE)
    except RentalDataInvalid as error:
        raise _refused(error) from error
    return await _saved_listing(session, body.listing.unit)


@router.put("/showing-rules", response_model=ShowingRules)
async def update_showing_rules(
    body: RulesSave, user: SerniaUser, session: DBSession
) -> ShowingRules:
    """Replace the showing rules that apply to every unit."""
    if body.based_on is not None:
        try:
            current = await service.read_showing_rules(session)
        except RentalDataInvalid as error:
            raise _broken_on_file(error) from error
        if current != body.based_on:
            raise _changed_since()
    try:
        await service.save_showing_rules(
            session, body.showing_rules, changed_by=_person(user), source=SOURCE
        )
    except RentalDataInvalid as error:
        raise _refused(error) from error
    return await service.read_showing_rules(session)


@router.get("/history", response_model=list[Change])
async def read_history(
    session: DBSession,
    subject: Annotated[
        str | None,
        Query(description='A unit\'s name, or "showing_rules". Leave out for everything.'),
    ] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[Change]:
    """Saved changes, newest first, each with the whole listing or rules as they read after it."""
    return await service.read_history(session, subject, limit=limit)
