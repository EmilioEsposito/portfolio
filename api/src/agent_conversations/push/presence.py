"""Who has a conversation open in front of them right now.

A team page showing a conversation reports it every few seconds while it is
the page in use (visible, and focused on a computer), and reports leaving when
it is hidden or closed. A push about a conversation is not sent to someone who
has it open: they are already reading it.

The push has to be held back here, on the server. A service worker that
receives a push and shows nothing is not an option on Apple's push service
(iPhone and iPad Home Screen apps, Safari on a Mac): WebKit counts every push
that shows no notification within 30 seconds, never resets the count, and
deletes all of the site's push subscriptions on the third
(``maxSilentPushCount`` in WebKit's ``WebPushDaemonConstants.h``).

Kept in this process's memory: the API runs as one process, and a report that
is lost (a restart, another replica) only means a push is sent as before.
"""

import time
from dataclasses import dataclass

# The page reports every 15 seconds; one missed report is tolerated. A page
# that goes away without saying so (the phone is switched off, the tab is
# killed) stops holding back pushes after this long.
PRESENCE_TTL_SECONDS = 40.0


@dataclass(frozen=True)
class _PageReport:
    conversation_id: str | None
    expires: float
    # The page numbers its reports. Two can be handled out of order (each
    # authenticates in a thread), and an older "open" landing after "left"
    # would hold back pushes for a page nobody is looking at.
    seq: int


# Keyed by (Clerk user ID, page's client ID), so that one page leaving does not
# cancel another page, on the same or another device, that still has the
# conversation open.
_pages: dict[tuple[str, str], _PageReport] = {}


def report(clerk_user_id: str, client_id: str, conversation_id: str | None, seq: int) -> None:
    """Record what one page shows now; ``None`` means it shows no conversation."""
    now = time.monotonic()
    for key, page in list(_pages.items()):
        if page.expires <= now:
            del _pages[key]
    key = (clerk_user_id, client_id)
    current = _pages.get(key)
    if current is not None and seq <= current.seq:
        return
    _pages[key] = _PageReport(conversation_id or None, now + PRESENCE_TTL_SECONDS, seq)


def readers(conversation_id: str | None) -> set[str]:
    """Clerk user IDs with ``conversation_id`` open in front of them."""
    if not conversation_id:
        return set()
    now = time.monotonic()
    return {
        user_id
        for (user_id, _), page in _pages.items()
        if page.conversation_id == conversation_id and page.expires > now
    }


def clear() -> None:
    """Forget every page (tests)."""
    _pages.clear()
