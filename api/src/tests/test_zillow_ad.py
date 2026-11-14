"""Reading a unit's Zillow ad (``rental_listings/zillow_ad.py``). No network, no database.

The pages here are invented and much smaller than Zillow's, but hold the
listing where Zillow's do: a JSON string inside the ``__NEXT_DATA__`` script.
"""

import json
from datetime import UTC, date, datetime

import httpx
import pytest

from api.src.rental_listings import zillow_ad
from api.src.rental_listings.schema import RentalListing

LINK = "https://www.zillow.com/homedetails/12-Example-St-APT-A/123_zpid/?view=public"
FETCHED_AT = datetime(2027, 5, 3, 12, tzinfo=UTC)
AVAILABLE_MS = 1814745600000  # 2027-07-05 00:00 UTC, as Zillow gives dates


def ad_page(**changes) -> str:
    """A listing page as Zillow serves a named crawler, with ``changes`` to the listing."""
    home = {
        "zpid": 123,
        "homeStatus": "FOR_RENT",
        "price": 1650,
        "daysOnZillow": 3,
        "bedrooms": 1,
        "bathrooms": 1.5,
        "livingArea": 620,
        "address": {
            "streetAddress": "12 Example St APT A",
            "city": "Pittsburgh",
            "state": "PA",
            "zipcode": "15224",
        },
        "resoFacts": {
            "availabilityDate": AVAILABLE_MS,
            "leaseTerm": "1 Year",
            "hasPetsAllowed": False,
            "furnished": False,
            "laundryFeatures": ["None"],
            "parkingFeatures": ["Off Street"],
            "appliances": ["Dishwasher", "Refrigerator"],
        },
        "priceHistory": [
            {"date": "2027-05-01", "event": "Listed for rent", "price": 1650, "source": "x"},
        ],
        "description": "  Sunny one bedroom. Tenant pays gas and electric.  ",
        **changes,
    }
    cache = json.dumps({"ForRentShopperPlatformFullRenderQuery{}": {"property": home}})
    data = {"props": {"isBot": True, "pageProps": {"componentProps": {"gdpClientCache": cache}}}}
    return (
        "<html><head></head><body>"
        f'<script id="__NEXT_DATA__" type="application/json">{json.dumps(data)}</script>'
        "</body></html>"
    )


BOT_CHECK = "<html><body><div id='px-captcha'></div>Press &amp; Hold</body></html>"


def client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


class TestParse:
    def test_a_live_ad_gives_rent_date_and_facts(self):
        ad = zillow_ad.parse_ad(ad_page(), url=LINK, fetched_at=FETCHED_AT)

        assert ad.for_rent and ad.status == "FOR_RENT"
        assert ad.rent == 1650
        assert ad.available_on == date(2027, 7, 5)
        assert ad.lease_term == "1 Year"
        assert (ad.bedrooms, ad.bathrooms, ad.living_area_sqft) == (1, 1.5, 620)
        assert ad.address == "12 Example St APT A, Pittsburgh, PA 15224"
        assert ad.features == {
            "parking": ["Off Street"],
            "appliances": ["Dishwasher", "Refrigerator"],
        }
        assert ad.price_history[0].event == "Listed for rent"
        assert ad.description == "Sunny one bedroom. Tenant pays gas and electric."

    def test_a_page_with_no_live_ad_gives_no_rent(self):
        """Off the market, Zillow's price is its estimate of the sale value."""
        ad = zillow_ad.parse_ad(
            ad_page(homeStatus="OTHER", price=289500), url=LINK, fetched_at=FETCHED_AT
        )

        assert not ad.for_rent and ad.rent is None

    def test_a_page_for_another_listing_is_refused(self):
        with pytest.raises(zillow_ad.ZillowAdUnavailable, match="different listing"):
            zillow_ad.parse_ad(ad_page(zpid=999), url=LINK, fetched_at=FETCHED_AT)

    @pytest.mark.parametrize(
        "page",
        [
            "<html>nothing here</html>",
            '<script id="__NEXT_DATA__">not json</script>',
            '<script id="__NEXT_DATA__">{"props": {}}</script>',
            '<script id="__NEXT_DATA__">[]</script>',
            *(
                '<script id="__NEXT_DATA__">'
                + json.dumps(
                    {"props": {"pageProps": {"componentProps": {"gdpClientCache": cache}}}}
                )
                + "</script>"
                for cache in (None, [], "null", "[]", "{}")
            ),
        ],
    )
    def test_a_page_that_has_changed_says_so(self, page):
        with pytest.raises(zillow_ad.ZillowAdUnavailable, match="changed its page"):
            zillow_ad.parse_ad(page, url=LINK, fetched_at=FETCHED_AT)

    def test_a_value_of_the_wrong_type_is_reported_not_raised_raw(self):
        with pytest.raises(zillow_ad.ZillowAdUnavailable, match="unexpected type"):
            zillow_ad.parse_ad(ad_page(bedrooms="one"), url=LINK, fetched_at=FETCHED_AT)

    def test_a_long_description_is_cut(self):
        ad = zillow_ad.parse_ad(ad_page(description="x" * 20_000), url=LINK, fetched_at=FETCHED_AT)

        assert len(ad.description) == 6_000


class TestFetch:
    @pytest.mark.asyncio
    async def test_it_asks_once_with_a_user_agent_that_names_us(self):
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(200, text=ad_page())

        async with client(handler) as http:
            ad = await zillow_ad.fetch_ad(LINK, http)

        assert ad.rent == 1650
        assert len(seen) == 1
        assert seen[0].headers["user-agent"] == zillow_ad.USER_AGENT
        assert str(seen[0].url) == LINK

    @pytest.mark.asyncio
    async def test_a_bot_check_is_reported_and_not_retried(self):
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(403, text=BOT_CHECK)

        async with client(handler) as http:
            with pytest.raises(zillow_ad.ZillowAdUnavailable, match="bot check.*paste the ad"):
                await zillow_ad.fetch_ad(LINK, http)
        assert len(seen) == 1

    @pytest.mark.asyncio
    @pytest.mark.parametrize(("status", "reason"), [(404, "no page"), (500, "HTTP 500")])
    async def test_other_failures_say_what_zillow_answered(self, status, reason):
        async with client(lambda request: httpx.Response(status, text="")) as http:
            with pytest.raises(zillow_ad.ZillowAdUnavailable, match=reason):
                await zillow_ad.fetch_ad(LINK, http)

    @pytest.mark.asyncio
    async def test_a_redirect_within_zillow_is_followed(self):
        moved = "https://www.zillow.com/homedetails/12-Example-St/123_zpid/"

        def handler(request: httpx.Request) -> httpx.Response:
            if str(request.url) == LINK:
                return httpx.Response(
                    301, headers={"location": "/homedetails/12-Example-St/123_zpid/"}
                )
            assert str(request.url) == moved
            return httpx.Response(200, text=ad_page())

        async with client(handler) as http:
            assert (await zillow_ad.fetch_ad(LINK, http)).rent == 1650

    @pytest.mark.asyncio
    async def test_a_redirect_off_zillow_is_not_followed(self):
        seen: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(str(request.url))
            return httpx.Response(302, headers={"location": "https://example.com/elsewhere"})

        async with client(handler) as http:
            with pytest.raises(zillow_ad.ZillowAdUnavailable, match="elsewhere"):
                await zillow_ad.fetch_ad(LINK, http)
        assert seen == [LINK]

    @pytest.mark.asyncio
    async def test_endless_redirects_stop(self):
        async with client(lambda request: httpx.Response(302, headers={"location": LINK})) as http:
            with pytest.raises(zillow_ad.ZillowAdUnavailable, match="too many"):
                await zillow_ad.fetch_ad(LINK, http)

    @pytest.mark.asyncio
    async def test_an_oversized_page_is_not_read_to_the_end(self):
        async with client(lambda request: httpx.Response(200, content=b"x" * 8_000_001)) as http:
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
    async def test_an_unreachable_zillow_is_reported(self):
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
        assert seen == [LINK]
