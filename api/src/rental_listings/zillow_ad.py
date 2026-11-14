"""Read a unit's Zillow ad: fetch its public listing page and pull out what the ad says.

Used to check a listing against its ad: by the general agent's
``rental_listing_zillow_ad_read`` tool, and by the Sernia MCP service through
``GET /api/rental-listings/zillow-ad/{unit}``. The lead agent never calls it,
so its answers do not depend on Zillow answering or keeping its page the same.

One plain HTTPS GET, with a user agent that says who we are. Zillow answers a
named crawler with a page it marks as served to a bot, and that page still
carries the listing as JSON in its ``__NEXT_DATA__`` script. A generic client
user agent gets Zillow's bot check instead. Nothing here tries to get past a
bot check: a refusal is reported as one, and the person pastes the ad text
instead.

Zillow can change its page or start refusing at any time. Anything that is not
the expected listing raises ``ZillowAdUnavailable`` with a reason a person can
act on. The ad's description is Zillow page text: callers treat it as data.
"""

import json
import re
from datetime import UTC, date, datetime
from typing import Any
from urllib.parse import urljoin

import httpx
from pydantic import BaseModel, ValidationError

from api.src.rental_listings.checks import zillow_url_problems
from api.src.rental_listings.schema import RentalListing

USER_AGENT = "SerniaListingSync/0.1 (+https://serniacapital.com)"
_TIMEOUT_SECONDS = 20
_MAX_BYTES = 8_000_000
_MAX_REDIRECTS = 3
_MAX_DESCRIPTION = 6_000
_MAX_HISTORY = 8
_NEXT_DATA = re.compile(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)
_ZPID = re.compile(r"/(\d+)_zpid\b")
_BOT_CHECK = ("px-captcha", "Press & Hold", "Access to this page has been denied")
_PASTE_INSTEAD = "Ask the person to paste the ad text instead."


class ZillowAdUnavailable(Exception):
    """The ad could not be read. The message says why, for a person."""


class AdEvent(BaseModel):
    """One line of the ad's price history: listed, price change, removed."""

    date: date | None
    event: str
    price: int | None


class ZillowAd(BaseModel):
    """What a unit's Zillow ad says, as Zillow's page holds it."""

    url: str
    fetched_at: datetime
    # Zillow's status for the page: "FOR_RENT" while the ad is live. Anything
    # else ("OTHER", "SOLD") means there is no live ad on this page.
    status: str
    for_rent: bool
    # The monthly rent, only while the ad is live. On a page with no live ad,
    # Zillow's "price" is its estimate of the sale value, so it is left out.
    rent: int | None
    available_on: date | None
    lease_term: str | None
    days_on_zillow: int | None
    address: str
    bedrooms: int | None
    bathrooms: float | None
    living_area_sqft: int | None
    pets_allowed: bool | None
    furnished: bool | None
    # Zillow's own feature lists: parking, laundry, heating, cooling, appliances.
    features: dict[str, list[str]]
    price_history: list[AdEvent]  # newest first
    description: str


async def read_ad(listing: RentalListing, client: httpx.AsyncClient | None = None) -> ZillowAd:
    """The ad at the listing's public Zillow link. Raises ``ZillowAdUnavailable``."""
    url = listing.zillow_listing_url_public_view
    if not url:
        raise ZillowAdUnavailable(
            f"{listing.unit} has no public Zillow link on file "
            "(zillow_listing_url_public_view). Ask the person for the link, or for the ad text."
        )
    return await fetch_ad(url, client)


async def fetch_ad(url: str, client: httpx.AsyncClient | None = None) -> ZillowAd:
    """Fetch and read the Zillow listing page at ``url``. Raises ``ZillowAdUnavailable``."""
    if problems := zillow_url_problems("link", url):
        raise ZillowAdUnavailable(problems[0])
    if client is None:
        async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as own:
            page = await _get(own, url)
    else:
        page = await _get(client, url)
    return parse_ad(page, url=url, fetched_at=datetime.now(UTC))


async def _get(client: httpx.AsyncClient, url: str) -> str:
    """The page's HTML. Follows a few redirects, each checked to stay on zillow.com."""
    headers = {"User-Agent": USER_AGENT, "Accept": "text/html"}
    for _ in range(_MAX_REDIRECTS + 1):
        try:
            async with client.stream("GET", url, headers=headers, follow_redirects=False) as answer:
                if answer.is_redirect:
                    target = urljoin(url, answer.headers.get("location", ""))
                    if problems := zillow_url_problems("redirect", target):
                        raise ZillowAdUnavailable(
                            f"Zillow sent the request elsewhere: {problems[0]}"
                        )
                    url = target
                    continue
                body = bytearray()
                async for chunk in answer.aiter_bytes():
                    body += chunk
                    if len(body) > _MAX_BYTES:
                        raise ZillowAdUnavailable("Zillow's page was larger than expected.")
                text = body.decode(answer.encoding or "utf-8", errors="replace")
                status = answer.status_code
        except httpx.HTTPError as error:
            raise ZillowAdUnavailable(f"Zillow could not be reached: {error!r}") from error
        if any(marker in text for marker in _BOT_CHECK):
            raise ZillowAdUnavailable(
                f"Zillow refused the request with its bot check (HTTP {status}). {_PASTE_INSTEAD}"
            )
        if status in (404, 410):
            raise ZillowAdUnavailable(f"Zillow has no page at {url} (HTTP {status}).")
        if status != 200:
            raise ZillowAdUnavailable(f"Zillow answered HTTP {status}. {_PASTE_INSTEAD}")
        return text
    raise ZillowAdUnavailable("Zillow redirected the request too many times.")


def _property(page: str) -> dict[str, Any]:
    """The listing record inside the page's ``__NEXT_DATA__`` JSON."""
    found = _NEXT_DATA.search(page)
    try:
        data = json.loads(found.group(1)) if found else None
        cache = data["props"]["pageProps"]["componentProps"]["gdpClientCache"]
        if isinstance(cache, str):
            cache = json.loads(cache)
        if not isinstance(cache, dict):
            raise TypeError(f"gdpClientCache is a {type(cache).__name__}, not an object")
        return next(
            value["property"]
            for value in cache.values()
            if isinstance(value, dict) and isinstance(value.get("property"), dict)
        )
    except (TypeError, KeyError, ValueError, StopIteration) as error:
        raise ZillowAdUnavailable(
            "Zillow's page did not hold the listing where expected; Zillow may have changed "
            f"its page. {_PASTE_INSTEAD}"
        ) from error


def _day(epoch_ms: Any) -> date | None:
    """Zillow gives dates as milliseconds since 1970, at midnight UTC of the day."""
    if isinstance(epoch_ms, int | float) and not isinstance(epoch_ms, bool):
        return datetime.fromtimestamp(epoch_ms / 1000, UTC).date()
    return None


def _strings(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str) and item and item != "None"]


def parse_ad(page: str, *, url: str, fetched_at: datetime) -> ZillowAd:
    """What the ad on this Zillow listing page says. Raises ``ZillowAdUnavailable``.

    A value of a shape not handled below is reported as a changed page, never
    raised as it is: callers catch only ``ZillowAdUnavailable``.
    """
    try:
        return _parse(page, url=url, fetched_at=fetched_at)
    except (TypeError, AttributeError, KeyError, ValueError) as error:
        raise ZillowAdUnavailable(
            f"Zillow's page held a value of an unexpected shape; Zillow may have changed its "
            f"page. {_PASTE_INSTEAD}"
        ) from error


def _parse(page: str, *, url: str, fetched_at: datetime) -> ZillowAd:
    home = _property(page)
    wanted = _ZPID.search(url)
    if wanted and str(home.get("zpid")) != wanted.group(1):
        raise ZillowAdUnavailable(
            f"Zillow's page is for a different listing (zpid {home.get('zpid')}) than the link "
            f"(zpid {wanted.group(1)}). Check the link on file."
        )
    facts = home.get("resoFacts") if isinstance(home.get("resoFacts"), dict) else {}
    status = str(home.get("homeStatus") or "")
    for_rent = status == "FOR_RENT"
    where = home.get("address") if isinstance(home.get("address"), dict) else {}
    address = ", ".join(
        str(part)
        for part in (where.get("streetAddress"), where.get("city"), where.get("state"))
        if part
    )
    if where.get("zipcode"):
        address = f"{address} {where['zipcode']}".strip()
    events = home.get("priceHistory") or []
    if not isinstance(events, list):
        raise TypeError(f"priceHistory is a {type(events).__name__}, not a list")
    history = [
        {"date": event.get("date"), "event": event.get("event") or "", "price": event.get("price")}
        for event in events[:_MAX_HISTORY]
        if isinstance(event, dict)
    ]
    try:
        return ZillowAd(
            url=url,
            fetched_at=fetched_at,
            status=status,
            for_rent=for_rent,
            rent=home.get("price") if for_rent else None,
            available_on=_day(facts.get("availabilityDate")),
            lease_term=facts.get("leaseTerm"),
            days_on_zillow=home.get("daysOnZillow"),
            address=address,
            bedrooms=home.get("bedrooms"),
            bathrooms=home.get("bathrooms"),
            living_area_sqft=home.get("livingArea"),
            pets_allowed=facts.get("hasPetsAllowed"),
            furnished=facts.get("furnished"),
            features={
                name: found
                for name, key in (
                    ("parking", "parkingFeatures"),
                    ("laundry", "laundryFeatures"),
                    ("heating", "heating"),
                    ("cooling", "cooling"),
                    ("appliances", "appliances"),
                )
                if (found := _strings(facts.get(key)))
            },
            price_history=history,
            description=str(home.get("description") or "").strip()[:_MAX_DESCRIPTION],
        )
    except ValidationError as error:
        raise ZillowAdUnavailable(
            f"Zillow's page held a value of an unexpected type ({error.error_count()} fields). "
            f"{_PASTE_INSTEAD}"
        ) from error
