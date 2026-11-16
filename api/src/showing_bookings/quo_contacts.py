"""The team's Quo (formerly OpenPhone) contacts, read for the resident lookup.

Read-only: it lists contacts through the shared contact store in
``open_phone/service.py``, which fetches the whole list at most once every
five minutes and serves it from memory in between, so a booking does not
fetch the contact list each time. Only ``residents.py`` may use this class,
and only in production; elsewhere the resident lookup is a stand-in.
"""

import os


class LiveQuoInTests(RuntimeError):
    """A test reached the live contact reader instead of installing a stand-in."""


class QuoContacts:
    """A ``ContactSource`` over the team's Quo contacts."""

    async def contacts(self, unit: str) -> list[dict]:
        # Every contact: the shared rules pick the unit's residents.
        if "PYTEST_CURRENT_TEST" in os.environ:
            raise LiveQuoInTests("Tests install a stand-in resident lookup; Quo is never read.")
        # Imported here: open_phone.service reads settings and connects the
        # contact store when imported, which the rest of this module never needs.
        from api.src.open_phone.service import get_all_contacts

        return await get_all_contacts()
