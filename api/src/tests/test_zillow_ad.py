"""Reading a unit's Zillow ad (``rental_listings/zillow_ad.py``). No network, no database.

The pages here are invented and much shorter than the reader's Markdown of a
Zillow listing, but laid out the same way: navigation, the price above the
address heading, the listing's sections, then nearby listings and the rest.
"""

from datetime import UTC, datetime

import httpx
import pytest

from api.src.rental_listings import zillow_ad
from api.src.rental_listings.schema import RentalListing

LINK = "https://www.zillow.com/homedetails/12-Example-St-APT-A/123_zpid/?view=public"
FETCHED_AT = datetime(2027, 5, 3, 12, tzinfo=UTC)


def ad_page(*, source: str = LINK, top: str = "", listing: str = "") -> str:
    """A live ad as the reader returns it, with optional replacements."""
    top = top or "Apartment for rent\n\n$1,650/mo Fees may apply\n"
    listing = listing or (
        "1 beds\n\n1 baths\n\nAvailable Jul 5, 2027\n\nCats OK\n\n"
        "## What's special\n\n"
        "Sunny one bedroom. [Learn more](https://www.zillow.com/learn/x/) "
        "Tenant pays gas and electric. Available July 5 or later.\n\n"
        "## Facts & features\n\n### Financial & listing details\n\n*   Lease term: 1 Year\n\n"
        "## Price history\n\n| Date | Event | Price |\n| --- | --- | --- |\n"
        "| 5/1/2027 | Listed for rent | $1,650 |\n"
    )
    return (
        "Title: 12 Example St APT A, Pittsburgh, PA 15224 | Zillow\n\n"
        f"URL Source: {source}\n\n"
        "Markdown Content:\n"  # about 30 lines of site navigation come first, as on Zillow
        "[Skip main navigation](https://www.zillow.com/#skip)\n\n"
        "[![Image 1: Zillow logo](https://www.zillowstatic.com/logo.svg)](https://www.zillow.com/)\n\n"
        + "".join(f"*   [Menu {n}](https://www.zillow.com/m{n}/)\n" for n in range(25))
        + "\n"
        f"{top}\n"
        "# 12 Example St, APT A,Pittsburgh, PA 15224\n\n"
        f"{listing}\n"
        "## Neighborhood: Bloomfield\n\n"
        "## Nearby apartments for rent\n\n"
        "[$1,999 Fees may apply 2 bd Elsewhere Apartments For Rent](https://www.zillow.com/apartments/x/)\n"
    )


def client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


class TestParse:
    def parse(self, page: str) -> zillow_ad.ZillowAd:
        return zillow_ad.parse_page(page, url=LINK, fetched_at=FETCHED_AT)

    def test_a_live_ad_keeps_rent_availability_description_facts_and_history(self):
        ad = self.parse(ad_page())

        assert ad.title == "12 Example St APT A, Pittsburgh, PA 15224 | Zillow"
        text = ad.page_text
        assert text.index("$1,650/mo") < text.index("# 12 Example St")
        for kept in ("Available Jul 5, 2027", "Sunny one bedroom.", "Lease term: 1 Year"):
            assert kept in text
        assert "| 5/1/2027 | Listed for rent | $1,650 |" in text

    def test_nearby_listings_navigation_links_and_images_are_dropped(self):
        text = self.parse(ad_page()).page_text

        assert "$1,999" not in text and "Elsewhere" not in text
        assert "Neighborhood" not in text
        assert "Skip main navigation" not in text
        assert "http" not in text and "![" not in text and "](" not in text
        assert "Learn more" in text  # a link's words stay; its address goes

    def test_a_page_with_no_live_ad_says_so_in_its_text(self):
        page = ad_page(
            top="Off market\n\n**Zestimate®**\n\n$289,500\n",
            listing="1 beds\n\n$1,116 Estimated rent\n\n## Home value\n\n$289,500\n",
        )
        text = self.parse(page).page_text

        assert "Off market" in text and "Estimated rent" in text
        assert "Home value" not in text

    def test_the_text_is_capped(self):
        page = ad_page(listing="## What's special\n\n" + "word " * 10_000)

        assert len(self.parse(page).page_text) == 12_000

    def test_a_page_for_another_listing_is_refused(self):
        other = "https://www.zillow.com/homedetails/Elsewhere/999_zpid/"
        with pytest.raises(zillow_ad.ZillowAdUnavailable, match="different page"):
            self.parse(ad_page(source=other))

    @pytest.mark.parametrize(
        ("page", "reason"),
        [
            ("Title: Access to this page has been denied\n\nPress & Hold", "bot check"),
            ('{"code": 451, "message": "blocked"}', "unexpected"),
            (f"URL Source: {LINK}\n\nMarkdown Content:\nNo heading at all", "changed its page"),
        ],
    )
    def test_anything_else_is_a_reason_to_paste_the_ad(self, page, reason):
        with pytest.raises(zillow_ad.ZillowAdUnavailable, match=reason) as caught:
            self.parse(page)
        assert "paste the ad" in str(caught.value)


class TestFetch:
    @pytest.mark.asyncio
    async def test_it_asks_the_reader_once_for_fresh_markdown(self, monkeypatch):
        monkeypatch.delenv(zillow_ad.READER_KEY_ENV, raising=False)
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(200, text=ad_page())

        async with client(handler) as http:
            ad = await zillow_ad.fetch_ad(LINK, http)

        assert "$1,650/mo" in ad.page_text
        (request,) = seen
        assert str(request.url) == "https://r.jina.ai/" + LINK
        assert request.headers["x-return-format"] == "markdown"
        assert request.headers["x-no-cache"] == "true"
        assert request.headers["user-agent"] == zillow_ad.USER_AGENT
        assert "authorization" not in request.headers

    @pytest.mark.asyncio
    async def test_a_reader_key_is_sent_when_set(self, monkeypatch):
        monkeypatch.setenv(zillow_ad.READER_KEY_ENV, "jina_test")
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(200, text=ad_page())

        async with client(handler) as http:
            await zillow_ad.fetch_ad(LINK, http)
        assert seen[0].headers["authorization"] == "Bearer jina_test"

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("status", "reason"), [(429, "busy"), (401, "HTTP 401"), (500, "HTTP 500")]
    )
    async def test_a_reader_failure_is_reported_and_not_retried(self, status, reason):
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(status, json={"message": "no"})

        async with client(handler) as http:
            with pytest.raises(zillow_ad.ZillowAdUnavailable, match=reason):
                await zillow_ad.fetch_ad(LINK, http)
        assert len(seen) == 1

    @pytest.mark.asyncio
    async def test_a_redirect_is_not_followed(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(302, headers={"location": "https://example.com/"})

        async with client(handler) as http:
            with pytest.raises(zillow_ad.ZillowAdUnavailable, match="HTTP 302"):
                await zillow_ad.fetch_ad(LINK, http)

    @pytest.mark.asyncio
    async def test_an_oversized_answer_is_not_read_to_the_end(self):
        async with client(lambda request: httpx.Response(200, content=b"x" * 4_000_001)) as http:
            with pytest.raises(zillow_ad.ZillowAdUnavailable, match="larger"):
                await zillow_ad.fetch_ad(LINK, http)

    @pytest.mark.asyncio
    async def test_a_link_off_zillow_is_never_requested(self):
        def handler(request: httpx.Request) -> httpx.Response:
            raise AssertionError("requested")

        async with client(handler) as http:
            with pytest.raises(zillow_ad.ZillowAdUnavailable, match="zillow.com"):
                await zillow_ad.fetch_ad("https://example.com/homedetails/123_zpid/", http)

    @pytest.mark.asyncio
    async def test_an_unreachable_reader_is_reported(self):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("down", request=request)

        async with client(handler) as http:
            with pytest.raises(zillow_ad.ZillowAdUnavailable, match="could not be reached"):
                await zillow_ad.fetch_ad(LINK, http)


class TestReadAd:
    @pytest.mark.asyncio
    async def test_a_listing_with_no_link_asks_for_one(self):
        with pytest.raises(zillow_ad.ZillowAdUnavailable, match="no public Zillow link"):
            await zillow_ad.read_ad(RentalListing(unit="12-A"))

    @pytest.mark.asyncio
    async def test_it_reads_the_public_link_not_the_editor_link(self):
        seen: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(str(request.url))
            return httpx.Response(200, text=ad_page())

        listing = RentalListing(
            unit="12-A",
            zillow_listing_url_public_view=LINK,
            zillow_listing_url_internal_editor_view="https://www.zillow.com/rental-manager/x",
        )
        async with client(handler) as http:
            await zillow_ad.read_ad(listing, http)
        assert seen == ["https://r.jina.ai/" + LINK]
