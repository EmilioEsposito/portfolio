"""The thread's own token on the booking links of a reply a person approves.

A booking made through a unit's page with ``?t=<token>`` is tied to the lead's
thread (``lead_links``), so it shows on the lead's page and in the next case.
The agent's draft carries that link when the booking service is in use, but a
person may paste the unit's plain page, or a link copied from another lead,
into an edited reply, and a draft written with the booking service off has the
plain page. Before an approved reply is sent, ``with_thread_token`` gives every
link to a unit's booking page this thread's token: added where it is missing,
put in place of another thread's. The cancel page (``/showings/cancel/...``)
and every other link are left as they are.

The automatic send does not use this: its check refuses a reply with any link
the case did not supply (``auto_reply.reply_problems``).
"""

import re
from functools import cache
from urllib.parse import parse_qsl, urlencode, urlsplit

from api.src.showing_bookings.bookings import public_base_url
from api.src.zillow_leads import lead_links

# What may end a sentence after a link, and so is not part of it.
_TRAILING = ".,;:!?"


@cache
def _pattern(base: str) -> re.Pattern[str]:
    """Links to a unit's booking page on ``base``'s host, with or without the scheme or www."""
    host = (urlsplit(base).netloc or base).removeprefix("www.")
    return re.compile(
        r"(?<![\w.@/-])(?:https?://)?(?:www\.)?"
        + re.escape(host)
        # One path segment, not followed by another: /showings/cancel/<token> is not a unit.
        + r"/showings/(?P<unit>[A-Za-z0-9_%-]+)(?![A-Za-z0-9_%/-])"
        + r"(?:\?(?P<query>[^\s#<>\"'()\[\]]*))?",
        re.IGNORECASE,
    )


def with_token(body: str, token: str, base: str) -> tuple[str, int]:
    """``body`` with every booking-page link carrying ``token``, and how many links changed."""
    changed = 0

    def fix(match: re.Match[str]) -> str:
        nonlocal changed
        query = match["query"] or ""
        kept = query.rstrip(_TRAILING)
        trail = query[len(kept) :]
        pairs = parse_qsl(kept, keep_blank_values=True)
        if [value for key, value in pairs if key == "t"] == [token]:
            return match[0]
        changed += 1
        params = [(key, value) for key, value in pairs if key != "t"] + [("t", token)]
        return f"{base}/showings/{match['unit']}?{urlencode(params)}{trail}"

    return _pattern(base.rstrip("/")).sub(fix, body), changed


async def with_thread_token(body: str, thread_id: str | None) -> tuple[str, int]:
    """``with_token`` with the thread's own token, made if the thread has none yet.

    Unchanged when the reply links no booking page, or the conversation names
    no thread.
    """
    base = public_base_url()
    if not thread_id or not _pattern(base).search(body):
        return body, 0
    token = await lead_links.token_for_thread(thread_id)
    return with_token(body, token, base)
