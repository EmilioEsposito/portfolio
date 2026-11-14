"""Read a unit's Zillow ad: the listing page as text, to compare with the listing on file.

Used by the general agent's ``rental_listing_zillow_ad_read`` tool, and by the
Sernia MCP service through ``GET /api/rental-listings/zillow-ad/{unit}``. The
lead agent never calls it, so its answers do not depend on Zillow or on the
reader below.

Zillow refuses requests from Railway's addresses with its bot check, so the
page is read through Jina Reader (``https://r.jina.ai/<url>``): a reading
service that renders the page in a browser on its own servers and returns it
as Markdown. One request per check, no key needed (``JINA_API_KEY``, when set,
is sent for higher rate limits). Nothing here handles a CAPTCHA or retries a
refusal: if Zillow refuses the reader too, that is reported, and the person
pastes the ad text instead.

The Markdown is cut down to the listing itself: the lines above the address
heading (price per month, availability) and the "What's special", "Facts &
features" and "Price history" sections. Nearby listings, estimates, schools
and site navigation are dropped, and links and images are removed. What is
left is Zillow page text: callers treat it as data, never instructions.
"""

import os
import re
from datetime import UTC, datetime

import httpx
from pydantic import BaseModel

from api.src.rental_listings.checks import zillow_url_problems
from api.src.rental_listings.schema import RentalListing

READER_URL = "https://r.jina.ai/"
READER_KEY_ENV = "JINA_API_KEY"
USER_AGENT = "SerniaListingSync/0.1 (+https://serniacapital.com)"
_TIMEOUT_SECONDS = 60  # the reader renders the page in a browser: 7 to 13 s in testing
_MAX_BYTES = 4_000_000
_MAX_TEXT = 12_000
_LINES_ABOVE_ADDRESS = 10  # the price and "for rent" lines sit just above the address heading
_KEEP_SECTIONS = ("What's special", "Facts & features", "Price history")
_BOT_CHECK = ("Press & Hold", "Access to this page has been denied", "px-captcha")
_PASTE_INSTEAD = "Ask the person to paste the ad text instead."
_ZPID = re.compile(r"/(\d+)_zpid\b")
_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_BARE_URL = re.compile(r"https?://\S+")


class ZillowAdUnavailable(Exception):
    """The ad could not be read. The message says why, for a person."""


class ZillowAd(BaseModel):
    """A unit's Zillow ad, as the page shows it."""

    url: str
    fetched_at: datetime
    title: str
    # The listing part of the page, as plain lines. While the ad is live it
    # starts with the rent ("$1,250/mo") and availability ("Available now");
    # a page with no live ad shows a Zestimate and "Estimated rent" instead.
    page_text: str


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
    """Read the Zillow listing page at ``url``. Raises ``ZillowAdUnavailable``."""
    if problems := zillow_url_problems("link", url):
        raise ZillowAdUnavailable(problems[0])
    if client is None:
        async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as own:
            page = await _get(own, url)
    else:
        page = await _get(client, url)
    return parse_page(page, url=url, fetched_at=datetime.now(UTC))


async def _get(client: httpx.AsyncClient, url: str) -> str:
    """The page as Markdown, from the reader."""
    headers = {
        "User-Agent": USER_AGENT,
        "X-Return-Format": "markdown",
        "X-No-Cache": "true",  # the ad may have changed minutes ago
        "X-Retain-Images": "none",
    }
    if key := os.environ.get(READER_KEY_ENV, "").strip():
        headers["Authorization"] = f"Bearer {key}"
    try:
        async with client.stream(
            "GET", READER_URL + url, headers=headers, follow_redirects=False
        ) as answer:
            body = bytearray()
            async for chunk in answer.aiter_bytes():
                body += chunk
                if len(body) > _MAX_BYTES:
                    raise ZillowAdUnavailable("The page was larger than expected.")
            text = body.decode(answer.encoding or "utf-8", errors="replace")
            status = answer.status_code
    except httpx.HTTPError as error:
        raise ZillowAdUnavailable(f"The page reader could not be reached: {error!r}") from error
    if status == 429:
        raise ZillowAdUnavailable(f"The page reader is busy (HTTP 429). {_PASTE_INSTEAD}")
    if status != 200:
        raise ZillowAdUnavailable(f"The page reader answered HTTP {status}. {_PASTE_INSTEAD}")
    return text


def _plain(line: str) -> str:
    """The line without images, links (their text is kept) or bare addresses."""
    line = _IMAGE.sub("", line)
    line = _LINK.sub(r"\1", line)
    return _BARE_URL.sub("", line).rstrip()


def parse_page(page: str, *, url: str, fetched_at: datetime) -> ZillowAd:
    """The listing part of a page as the reader returns it. Raises ``ZillowAdUnavailable``."""
    if any(marker in page for marker in _BOT_CHECK):
        raise ZillowAdUnavailable(
            f"Zillow refused the page reader with its bot check. {_PASTE_INSTEAD}"
        )
    header, _, body = page.partition("Markdown Content:")
    if not body:
        raise ZillowAdUnavailable(
            f"The page reader returned something unexpected. {_PASTE_INSTEAD}"
        )
    source = re.search(r"^URL Source:\s*(\S+)", header, re.M)
    wanted = _ZPID.search(url)
    if wanted and not (source and f"/{wanted.group(1)}_zpid" in source.group(1)):
        raise ZillowAdUnavailable(
            "Zillow sent the reader to a different page than the link on file "
            f"({source.group(1) if source else 'unknown'}). Check the link."
        )
    title = re.search(r"^Title:\s*(.+)$", header, re.M)
    lines = [plain for line in body.splitlines() if (plain := _plain(line)).strip()]
    address = next((index for index, line in enumerate(lines) if line.startswith("# ")), None)
    if address is None:
        raise ZillowAdUnavailable(
            f"The page had no listing heading; Zillow may have changed its page. {_PASTE_INSTEAD}"
        )
    kept: list[str] = []
    for line in lines[max(0, address - _LINES_ABOVE_ADDRESS) :]:
        if line.startswith("## ") and not line[3:].strip().startswith(_KEEP_SECTIONS):
            break
        kept.append(line)
    return ZillowAd(
        url=url,
        fetched_at=fetched_at,
        title=(title.group(1).strip() if title else ""),
        page_text="\n".join(kept)[:_MAX_TEXT],
    )
