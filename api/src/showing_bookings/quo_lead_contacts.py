"""The team's Quo contacts, read and written for the Lead contacts (``lead_contacts.py``).

Written from Quo's API reference:

- a number's contacts: the shared contact store in ``open_phone/service.py``,
  read afresh each time (its five-minute cache is dropped first), so a contact
  made a moment ago is found and never made twice;
- ``GET /v1/contacts/{id}``: one contact as it is now;
- ``POST /v1/contacts``: a new contact, ``defaultFields`` and ``customFields``;
- ``PATCH /v1/contacts/{id}``: replaces the contact's ``emails``,
  ``phoneNumbers`` and ``customFields`` lists whole, so the caller sends every
  entry it keeps. https://www.quo.com/docs/mdx/api-reference/contacts/update-a-contact-by-id.md

Requests go through the shared OpenPhone client, with its rate limit. Only
``lead_contacts.py`` may use this class, and only in production; it sends no text.
"""

import os

import httpx


class LiveQuoInTests(RuntimeError):
    """A test reached the live contact writer instead of installing a stand-in."""


class ContactsUnavailable(Exception):
    """Quo did not do what was asked. ``retryable`` when trying later may work."""

    def __init__(self, detail: str, *, retryable: bool):
        super().__init__(detail)
        self.detail = detail
        self.retryable = retryable


def _client() -> httpx.AsyncClient:
    if "PYTEST_CURRENT_TEST" in os.environ:
        raise LiveQuoInTests("Tests install a stand-in contact store; Quo is never written.")
    # Imported here: open_phone.service reads settings and connects the
    # contact store when imported, which the rest of this module never needs.
    from api.src.open_phone.service import _openphone_client

    return _openphone_client()


def _check(response: httpx.Response) -> dict:
    status = response.status_code
    if status in (200, 201, 202):
        return response.json().get("data") or {}
    detail = f"Quo answered {status}: {response.text[:300]}"
    # Out of reach, rate limited or failing: the next run tries again, and looks
    # the number up first, so a contact that was made before an error is found.
    raise ContactsUnavailable(detail, retryable=status in (408, 429) or status >= 500)


class QuoLeadContacts:
    """A ``LeadContactStore`` over the team's Quo contacts."""

    async def with_phone(self, phone: str) -> list[dict]:
        from api.src.open_phone.service import find_contacts_by_phone, invalidate_contact_cache

        invalidate_contact_cache()
        try:
            async with _client() as client:
                return await find_contacts_by_phone(phone, client)
        except httpx.HTTPStatusError as exc:
            _check(exc.response)
            raise
        except httpx.TransportError as exc:
            raise ContactsUnavailable("Could not reach Quo.", retryable=True) from exc

    async def get(self, contact_id: str) -> dict:
        return await self._call("GET", f"/v1/contacts/{contact_id}")

    async def create(self, payload: dict) -> str:
        created = await self._call("POST", "/v1/contacts", payload)
        if not created.get("id"):
            raise ContactsUnavailable("Quo made the contact but sent no id.", retryable=False)
        return created["id"]

    async def update(self, contact_id: str, payload: dict) -> None:
        await self._call("PATCH", f"/v1/contacts/{contact_id}", payload)

    async def _call(self, method: str, path: str, payload: dict | None = None) -> dict:
        from api.src.open_phone.service import invalidate_contact_cache

        try:
            async with _client() as client:
                response = await client.request(method, path, json=payload)
        except httpx.TransportError as exc:
            # A POST that reached Quo may have made the contact: the retry looks
            # the number up first and finds it.
            raise ContactsUnavailable(f"Quo did not answer the {method}.", retryable=True) from exc
        finally:
            if method != "GET":
                invalidate_contact_cache()
        return _check(response)
