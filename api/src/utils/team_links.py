"""Addresses of the team's signed-in pages, for every link the backend builds or mentions.

The web app serves the team pages under ``/team``. In production they are on
serniacapital.com. The development environment has no Sernia hostname, so its
links use dev.eesposito.com, where the web app serves ``/team`` too. Anywhere
else (a local run, tests, a PR environment) links point at the local web server.

Use a ``*_PATH`` constant or a ``*_path`` function for a link the browser opens
on the site it is already on (web push, the calendar callback's page), and
``team_url`` around either for a link read outside the browser (SMS, a calendar
event, the agent's own context).

This module imports nothing from the app, so any package may use it.
"""

import os
from urllib.parse import quote, urlencode

PRODUCTION_BASE_URL = "https://serniacapital.com"
DEVELOPMENT_BASE_URL = "https://dev.eesposito.com"
LOCAL_BASE_URL = "http://localhost:5173"

CHAT_PATH = "/team/chat"
SETTINGS_PATH = "/team/settings"
ADMIN_PATH = "/team/admin"
TENANTS_PATH = "/team/tenants"
SCHEDULER_PATH = "/team/scheduler"
LEADS_PATH = "/team/leads"
LEAD_LISTINGS_PATH = "/team/leads/listings"
LEAD_SETTINGS_PATH = "/team/leads/settings"
LEAD_BOOKINGS_PATH = "/team/leads/bookings"
LEAD_THREADS_PATH = "/team/leads/threads"


def base_url() -> str:
    """The team pages' site for this environment (``RAILWAY_ENVIRONMENT_NAME``)."""
    environment = os.getenv("RAILWAY_ENVIRONMENT_NAME", "")
    if environment == "production":
        return PRODUCTION_BASE_URL
    if environment == "development":
        return DEVELOPMENT_BASE_URL
    return LOCAL_BASE_URL


def chat_path(conversation_id: str) -> str:
    """One Sernia AI conversation, as a path: ``/team/chat?id=<conversation id>``."""
    return f"{CHAT_PATH}?{urlencode({'id': conversation_id})}"


def lead_thread_path(thread_id: str) -> str:
    """One Zillow lead, by the Gmail thread id of their inquiry:
    ``/team/leads/threads/<thread id>``."""
    return f"{LEAD_THREADS_PATH}/{quote(thread_id, safe='')}"


def showing_path(showing_id: int) -> str:
    """One booked showing, in the Showings section of the Booking settings tab."""
    return f"{LEAD_BOOKINGS_PATH}#showing-{int(showing_id)}"


def team_url(path: str) -> str:
    """``path`` (one of the paths above) as an absolute address for this environment."""
    return f"{base_url()}{path}"
