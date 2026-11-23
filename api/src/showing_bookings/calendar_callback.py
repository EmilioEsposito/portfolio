"""Google's OAuth callback: the one showing-bookings route without the Sernia sign-in.

``GET /api/showing-bookings/calendar/callback``. Google sends the browser here
after its consent screen as a plain redirect, so the request carries no
sign-in. Who started the connection comes from the signed, ten-minute
``state`` instead (``google_oauth.read_state``), and a callback whose state is
missing, altered or expired saves nothing.

The answer is a small page that sends the browser back to the Booking settings
tab with the result in the address (``?calendar=connected``), which the tab
shows. It is a page rather than a redirect status because the web server's
``/api`` proxy follows redirects itself instead of passing them on.

The query holds Google's one-time code and the state, so it is kept out of
the logs: request tracing skips this URL (``CALLBACK_URL_PATTERN``, used in
``api/index.py``), ``code`` and ``state`` are read from the raw query rather
than declared as parameters that instrumentation records, and the page asks
the browser to send no referrer onward.
"""

from datetime import UTC, datetime
from html import escape
from urllib.parse import urlencode

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from api.src.showing_bookings import connections
from api.src.utils import team_links

# Mounted at /api in api/index.py beside the team router, which requires sign-in.
callback_router = APIRouter(prefix="/showing-bookings/calendar", tags=["showing-bookings"])

# Request tracing would record the query, Google's one-time code and the state,
# in full; api/index.py leaves URLs matching this out of request spans.
CALLBACK_URL_PATTERN = "/api/showing-bookings/calendar/callback"
TAB_PATH = team_links.LEAD_BOOKINGS_PATH
SECTION_ID = "calendar-connections"
_HEADERS = {
    "Cache-Control": "no-store",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": (
        "default-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
    ),
    "X-Robots-Tag": "noindex",
}


def back_to_tab(result: connections.ConnectResult) -> str:
    """The tab's address with the result, relative so the browser stays on this site."""
    return f"{TAB_PATH}?{urlencode({'calendar': result})}#{SECTION_ID}"


def _page(result: connections.ConnectResult) -> HTMLResponse:
    target = escape(back_to_tab(result))
    page = (
        "<!doctype html>\n"
        '<html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        '<meta name="robots" content="noindex">'
        f'<meta http-equiv="refresh" content="0; url={target}">'
        "<title>Calendar connection</title></head>"
        f'<body><p>Back to <a href="{target}">Booking settings</a>…</p></body></html>\n'
    )
    return HTMLResponse(page, headers=_HEADERS)


@callback_router.get("/callback", include_in_schema=False)
async def calendar_callback(request: Request) -> HTMLResponse:
    query = request.query_params
    result = await connections.complete_connect(
        state=query.get("state"),
        code=query.get("code"),
        error=query.get("error"),
        now=datetime.now(UTC),
    )
    return _page(result)
