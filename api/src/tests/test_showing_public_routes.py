"""The public routes' checks: the switch, the fields, Turnstile, the limits, the number
route, the mobile correction, what a booking saves, and what no answer may show.

Uses the test database and the stand-ins in ``showing_public.py``. Nothing is sent.
"""

from datetime import timedelta

import pytest
import pytest_asyncio

from api.src.showing_bookings import open_times, turnstile
from api.src.tests.showing_helpers import clear_showing_data, store_settings
from api.src.tests.showing_public import (
    ADDRESS,
    ALEX_EMAIL,
    ALEX_MOBILE,
    CASEY_EMAIL,
    CASEY_MOBILE,
    FAILING_TOKEN,
    JANE,
    NOW,
    ROBIN_EMAIL,
    ROBIN_MOBILE,
    UNIT,
    PublicWorld,
    add_world_rows,
    at,
    booking,
    install,
    rows,
    statuses,
)

PUBLIC = "/api/showing-bookings/public"
BOOK = f"{PUBLIC}/units/{UNIT}/bookings"
TEAM_PHONE = "+14125550100"  # the fictional SERNIA_TEAM_PHONE in conftest.py


@pytest_asyncio.fixture
async def world(monkeypatch):
    for name in (
        "RAILWAY_ENVIRONMENT_NAME",
        "TURNSTILE_SECRET_KEY",
        "TURNSTILE_SITE_KEY",
        "SHOWING_PUBLIC_BASE_URL",
        "SERNIA_TEAM_PHONE_EXTENSION",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("SERNIA_TEAM_PHONE", TEAM_PHONE)
    monkeypatch.setenv("SHOWING_TEST_PHONES", f"{ALEX_MOBILE},{ROBIN_MOBILE},{CASEY_MOBILE}")
    monkeypatch.setenv("SHOWING_TEST_EMAILS", f"{ALEX_EMAIL},{ROBIN_EMAIL},{JANE}")
    open_times.forget_free_busy()
    await clear_showing_data()
    state = PublicWorld()
    install(state, monkeypatch)
    await add_world_rows(state)
    yield state
    open_times.forget_free_busy()
    await clear_showing_data()


async def post(world: PublicWorld, path: str, body: dict, **headers):
    async with world.client() as client:
        return await client.post(path, json=body, headers=headers)


async def book(world: PublicWorld, body: dict | None = None, **headers):
    return await post(world, BOOK, body or booking(), **headers)


def refusal(answer) -> tuple[int, str, list[str]]:
    detail = answer.json()["detail"]
    return answer.status_code, detail["code"], sorted(detail["fields"])


# The switch


async def every_route(world: PublicWorld) -> list[int]:
    async with world.client() as client:
        return [
            (await client.get(f"{PUBLIC}/units/{UNIT}")).status_code,
            (await client.post(BOOK, json=booking())).status_code,
            (
                await client.post(f"{PUBLIC}/bookings/lookup", json={"cancel_token": "x"})
            ).status_code,
            (
                await client.post(
                    f"{PUBLIC}/bookings/mobile", json={"cancel_token": "x", "mobile": ROBIN_MOBILE}
                )
            ).status_code,
            (await client.post(f"{PUBLIC}/team-phone", json={"turnstile_token": "t"})).status_code,
        ]


@pytest.mark.asyncio
async def test_switched_off_every_public_route_answers_404_and_saves_nothing(world):
    await store_settings(public_booking_enabled=False)

    assert await every_route(world) == [404] * 5
    assert await rows() == ([], [], [])
    assert world.turnstile_checks == []
    assert world.texts.sent == []


@pytest.mark.asyncio
async def test_unset_means_off_in_production_and_on_elsewhere(world, monkeypatch):
    async with world.client() as client:
        assert (await client.get(f"{PUBLIC}/units/{UNIT}")).status_code == 200

    monkeypatch.setenv("RAILWAY_ENVIRONMENT_NAME", "production")
    assert await every_route(world) == [404] * 5

    await store_settings(public_booking_enabled=True)
    async with world.client() as client:
        assert (await client.get(f"{PUBLIC}/units/{UNIT}")).status_code == 200


@pytest.mark.asyncio
async def test_the_tab_switches_public_booking_and_keeps_it_through_a_send_mode_save(world):
    async with world.client(world.team_app()) as client:
        view = (await client.get("/api/showing-bookings/settings")).json()
        assert (view["public_booking_on"], view["public_booking_default"]) == (True, True)
        assert view["settings"]["public_booking_enabled"] is None

        closed = await client.put(
            "/api/showing-bookings/settings/public-booking", json={"public_booking_enabled": False}
        )
        assert closed.status_code == 200
        assert closed.json()["public_booking_on"] is False

        # A send-mode screen loaded before the switch moved neither undoes it nor is refused.
        stale = view["settings"]
        saved = await client.patch(
            "/api/showing-bookings/settings",
            json={"settings": {**stale, "send_mode": "off"}, "based_on": stale},
        )
        assert saved.status_code == 200, saved.text
        assert saved.json()["public_booking_enabled"] is False

        back = await client.put(
            "/api/showing-bookings/settings/public-booking", json={"public_booking_enabled": None}
        )
        assert back.json()["public_booking_on"] is True
    assert await every_route(world) != [404] * 5


# What the page reads


@pytest.mark.asyncio
async def test_the_unit_page_shows_what_the_form_needs_and_nothing_more(world):
    async with world.client() as client:
        answer = await client.get(f"{PUBLIC}/units/{UNIT.lower()}")

    assert answer.status_code == 200
    page = answer.json()
    assert (page["unit"], page["rent"], page["showing_minutes"]) == (UNIT, 1450, 30)
    assert [(c["key"], c["wording"]) for c in page["confirmations"]] == [
        ("lease_start", "My lease can start between Jun 1 and Jul 1, 2027."),
        ("pets", "No pet dogs will live in the apartment."),
        ("texts", "Text me about this showing and my rental inquiry."),
    ]
    assert "Reply STOP to opt out" in page["consent_small_print"]
    assert page["turnstile_site_key"] == turnstile.TEST_SITE_KEY
    first = page["offers"][0]
    assert set(first) == {"start", "join", "preferred", "places_left"}
    assert first["places_left"] == 4
    raw = answer.text
    for private in ("Jane", JANE, ADDRESS, "320 Example", TEAM_PHONE, "412-555-0100"):
        assert private not in raw


@pytest.mark.asyncio
async def test_the_lease_start_box_follows_the_listing(world):
    world.listing = world.listing.model_copy(update={"next_lease_start_latest": None})
    async with world.client() as client:
        one = (await client.get(f"{PUBLIC}/units/{UNIT}")).json()
    assert one["confirmations"][0]["wording"] == "My lease can start on Jun 1, 2027."

    world.listing = world.listing.model_copy(update={"next_lease_start_earliest": None})
    async with world.client() as client:
        none = (await client.get(f"{PUBLIC}/units/{UNIT}")).json()
    assert [c["key"] for c in none["confirmations"]] == ["pets", "texts"]
    # Without lease dates the lease box is not asked for.
    answer = await book(world, booking(confirmations={"pets": True, "texts": True}))
    assert answer.status_code == 201, answer.text


@pytest.mark.asyncio
async def test_an_unknown_or_off_market_unit_is_404(world):
    async with world.client() as client:
        assert (await client.get(f"{PUBLIC}/units/999-99")).status_code == 404
    world.listing = world.listing.model_copy(update={"off_market_on": NOW.date()})
    async with world.client() as client:
        answer = await client.get(f"{PUBLIC}/units/{UNIT}")
    assert answer.status_code == 404
    assert answer.json()["detail"]["code"] == "unknown_unit"


# Refusals, each with its field, and nothing saved


@pytest.mark.parametrize(
    ("changes", "field"),
    [
        ({"confirmations": {"lease_start": True, "texts": True}}, "confirmations.pets"),
        ({"confirmations": {"pets": True, "texts": True}}, "confirmations.lease_start"),
        ({"confirmations": {"lease_start": True, "pets": True}}, "confirmations.texts"),
        ({"confirmations": {"lease_start": True, "pets": "yes", "texts": True}}, None),
        ({"mobile": "555-0117"}, "mobile"),
        ({"mobile": "+44 20 7946 0958"}, "mobile"),
        ({"email": "alex at example.com"}, "email"),
        ({"name": "   "}, "name"),
        ({"start": "next Wednesday"}, "start"),
        ({"start": "2027-04-14T16:00:00"}, "start"),  # no time zone
    ],
)
@pytest.mark.asyncio
async def test_a_bad_field_is_refused_with_the_field_named(world, changes, field):
    answer = await book(world, booking(**changes))

    assert answer.status_code == 422
    detail = answer.json()["detail"]
    if field is not None:
        assert detail["code"] == "invalid"
        assert field in detail["fields"]
    assert await rows() == ([], [], [])
    assert world.turnstile_checks == []


@pytest.mark.asyncio
async def test_every_bad_field_is_named_at_once(world):
    answer = await book(world, booking(mobile="12", email="", name="", confirmations={}))
    assert refusal(answer) == (
        422,
        "invalid",
        [
            "confirmations.lease_start",
            "confirmations.pets",
            "confirmations.texts",
            "email",
            "mobile",
            "name",
        ],
    )


@pytest.mark.asyncio
async def test_a_failed_turnstile_check_is_refused(world, monkeypatch):
    # A secret other than Cloudflare's test secret, so Cloudflare (the stand-in) is asked.
    monkeypatch.setenv("TURNSTILE_SECRET_KEY", "made-up-secret")
    answer = await book(world, booking(token=FAILING_TOKEN))
    assert refusal(answer) == (403, "turnstile_failed", ["turnstile"])

    missing = booking()
    missing.pop("turnstile_token")
    assert refusal(await book(world, missing)) == (403, "turnstile_failed", ["turnstile"])
    assert await rows() == ([], [], [])


@pytest.mark.asyncio
async def test_outside_production_the_test_secret_passes_without_asking_cloudflare(world):
    answer = await book(world, booking(token=FAILING_TOKEN))
    assert answer.status_code == 201, answer.text
    assert world.turnstile_checks == []


@pytest.mark.asyncio
async def test_a_real_secret_asks_cloudflare_with_the_ip(world, monkeypatch):
    monkeypatch.setenv("TURNSTILE_SECRET_KEY", "made-up-secret")
    await book(world, **{"cf-connecting-ip": "203.0.113.7"})
    assert world.turnstile_checks == [("made-up-secret", "turnstile-passes", "203.0.113.7")]


@pytest.mark.asyncio
async def test_production_without_a_secret_refuses_to_book(world, monkeypatch):
    monkeypatch.setenv("RAILWAY_ENVIRONMENT_NAME", "production")
    await store_settings(public_booking_enabled=True)

    answer = await book(world)

    assert refusal(answer) == (503, "unavailable", [])
    assert await rows() == ([], [], [])
    assert world.turnstile_checks == []
    async with world.client() as client:
        page = (await client.get(f"{PUBLIC}/units/{UNIT}")).json()
    assert page["turnstile_site_key"] is None

    monkeypatch.setenv("TURNSTILE_SECRET_KEY", "made-up-secret")
    monkeypatch.setenv("TURNSTILE_SITE_KEY", "made-up-site-key")
    assert (await book(world)).status_code == 201
    assert world.turnstile_checks[0][0] == "made-up-secret"


@pytest.mark.asyncio
async def test_changed_wording_since_the_page_loaded_is_refused(world):
    async with world.client() as client:
        page = (await client.get(f"{PUBLIC}/units/{UNIT}")).json()
    world.listing = world.listing.model_copy(update={"next_lease_start_latest": None})

    answer = await book(world, booking(confirmations_version=page["confirmations_version"]))

    assert refusal(answer) == (409, "confirmations_changed", ["confirmations"])
    assert await rows() == ([], [], [])


# Limits


@pytest.mark.asyncio
async def test_two_upcoming_showings_per_mobile(world):
    assert (await book(world, booking(at(16)))).status_code == 201
    assert (await book(world, booking(at(17), email=ROBIN_EMAIL))).status_code == 201

    answer = await book(world, booking(at(18), email=CASEY_EMAIL))

    assert refusal(answer) == (429, "limit_mobile", ["mobile"])
    _, attendees, _ = await rows()
    assert len(attendees) == 2


@pytest.mark.asyncio
async def test_two_upcoming_showings_per_email(world):
    assert (await book(world, booking(at(16)))).status_code == 201
    assert (await book(world, booking(at(17), mobile=ROBIN_MOBILE))).status_code == 201

    answer = await book(world, booking(at(18), mobile=CASEY_MOBILE))

    assert refusal(answer) == (429, "limit_email", ["email"])


@pytest.mark.asyncio
async def test_bookings_per_ip_address_come_from_the_settings_row(world):
    await store_settings(public_limits={"bookings_per_ip_per_day": 2})
    ip = {"x-forwarded-for": "198.51.100.4, 10.0.0.1"}
    assert (await book(world, booking(at(16)), **ip)).status_code == 201
    second = booking(at(17), mobile=ROBIN_MOBILE, email=ROBIN_EMAIL)
    assert (await book(world, second, **ip)).status_code == 201

    third = booking(at(18), mobile=CASEY_MOBILE, email=CASEY_EMAIL)
    assert refusal(await book(world, third, **ip)) == (429, "limit_ip", [])
    # Another address, or a day later, may book.
    assert (await book(world, third, **{"x-forwarded-for": "198.51.100.5"})).status_code == 201
    _, attendees, _ = await rows()
    assert "198.51.100" not in str([a.client_ip_hash for a in attendees])


@pytest.mark.asyncio
async def test_a_cap_on_all_page_bookings_a_day_comes_from_the_settings_row(world):
    await store_settings(public_limits={"bookings_per_day": 2})
    assert (await book(world, booking(at(16)))).status_code == 201
    await store_settings(
        send_mode="live", test_sends_enabled=True, public_limits={"bookings_per_day": 2}
    )
    world.texts.refuse(ROBIN_MOBILE, "invalid_number")
    withdrawn = await book(world, booking(at(17), mobile=ROBIN_MOBILE, email=ROBIN_EMAIL))
    assert withdrawn.status_code == 422  # withdrawn bookings count too

    third = await book(world, booking(at(18), mobile=CASEY_MOBILE, email=CASEY_EMAIL))

    assert refusal(third) == (429, "limit_site", [])
    assert (
        third.json()["detail"]["message"] == "Online booking is paused right now. Call us to book."
    )
    _, attendees, _ = await rows()
    assert len(attendees) == 2
    # A day later the cap has room again.
    world.now = NOW + timedelta(days=1, minutes=1)
    later = booking(
        at(18, day=at(0).date() + timedelta(days=2)), mobile=CASEY_MOBILE, email=CASEY_EMAIL
    )
    assert (await book(world, later)).status_code == 201


@pytest.mark.asyncio
async def test_a_past_showing_does_not_count_against_the_limit(world):
    await book(world, booking(at(16)))
    await book(world, booking(at(17), email=ROBIN_EMAIL))
    world.now = at(17, 30)  # both have started
    later = booking(at(18, day=at(0).date() + timedelta(days=1)), email=CASEY_EMAIL)
    assert (await book(world, later)).status_code == 201


# What a booking saves


@pytest.mark.asyncio
async def test_the_lead_token_user_agent_and_signature_header_are_saved(world):
    answer = await book(
        world,
        booking(lead_token="thread-token_123"),
        **{
            "user-agent": "ExampleAgent/1.0",
            "signature-agent": '"https://agent.example.com"',
        },
    )

    assert answer.status_code == 201
    _, (alex,), _ = await rows()
    assert alex.lead_token == "thread-token_123"
    assert alex.user_agent == "ExampleAgent/1.0"
    assert alex.signature_agent == '"https://agent.example.com"'


@pytest.mark.asyncio
async def test_a_malformed_lead_token_is_not_saved(world):
    await book(world, booking(lead_token="<script>"))
    _, (alex,), _ = await rows()
    assert alex.lead_token is None and alex.signature_agent is None


# Reading a booking back, and nothing leaks


@pytest.mark.asyncio
async def test_a_booking_is_read_back_by_its_cancel_token_only(world):
    alex = (await book(world)).json()
    robin_body = booking(mobile=ROBIN_MOBILE, email=ROBIN_EMAIL, name="Robin Lee")
    robin = (await book(world, robin_body)).json()

    seen = await post(world, f"{PUBLIC}/bookings/lookup", {"cancel_token": robin["cancel_token"]})

    assert seen.status_code == 200
    view = seen.json()
    assert view["status"] == "booked"
    assert (view["unit"], view["address"]) == (UNIT, ADDRESS)
    assert view["mobile"] == "•••-•••-••18"
    assert view["corrections_left"] == 2
    for answer in (seen.text, str(robin)):
        for private in (
            "Alex",
            ALEX_MOBILE,
            ALEX_EMAIL,
            "Jane",
            JANE,
            TEAM_PHONE,
            alex["cancel_token"],
        ):
            assert private not in answer
    unknown = await post(world, f"{PUBLIC}/bookings/lookup", {"cancel_token": "nope"})
    assert refusal(unknown) == (404, "unknown_booking", [])


# The team's number


@pytest.mark.asyncio
async def test_the_number_route_refuses_without_turnstile(world, monkeypatch):
    monkeypatch.setenv("TURNSTILE_SECRET_KEY", "made-up-secret")
    assert refusal(await post(world, f"{PUBLIC}/team-phone", {})) == (
        403,
        "turnstile_failed",
        ["turnstile"],
    )
    failed = await post(world, f"{PUBLIC}/team-phone", {"turnstile_token": FAILING_TOKEN})
    assert refusal(failed) == (403, "turnstile_failed", ["turnstile"])
    assert TEAM_PHONE not in failed.text


@pytest.mark.asyncio
async def test_the_number_route_answers_after_turnstile(world, monkeypatch):
    monkeypatch.setenv("SERNIA_TEAM_PHONE_EXTENSION", "3")
    answer = await post(world, f"{PUBLIC}/team-phone", {"turnstile_token": "turnstile-passes"})

    assert answer.status_code == 200
    assert answer.json() == {
        "phone": TEAM_PHONE,
        "display": "412-555-0100",
        "extension": "3",
        "tel_href": f"tel:{TEAM_PHONE},3",
    }
    assert answer.headers["cache-control"] == "no-store"


@pytest.mark.asyncio
async def test_the_number_route_refuses_in_production_without_a_secret(world, monkeypatch):
    monkeypatch.setenv("RAILWAY_ENVIRONMENT_NAME", "production")
    await store_settings(public_booking_enabled=True)
    answer = await post(world, f"{PUBLIC}/team-phone", {"turnstile_token": "turnstile-passes"})
    assert refusal(answer) == (503, "unavailable", [])


# Correcting the mobile


@pytest.mark.asyncio
async def test_a_corrected_mobile_gets_the_confirmation_and_the_held_notices(world):
    await store_settings(send_mode="live", test_sends_enabled=True)
    world.texts.report(ALEX_MOBILE, "undelivered")
    token = (await book(world)).json()["cancel_token"]
    await world.send_pending(NOW + timedelta(minutes=1))
    _, (alex,), notices = await rows()
    assert alex.attention_reason == "confirmation_undelivered"
    assert [n.status for n in notices[1:]] == ["cancelled", "cancelled"]

    fixed = await post(
        world, f"{PUBLIC}/bookings/mobile", {"cancel_token": token, "mobile": "(412) 555-0118"}
    )

    assert fixed.status_code == 200, fixed.text
    assert fixed.json()["mobile"] == "•••-•••-••18"
    assert fixed.json()["corrections_left"] == 1
    assert len(world.texts.to(ROBIN_MOBILE)) == 1
    _, (alex,), notices = await rows()
    assert alex.mobile == ROBIN_MOBILE and alex.mobile_corrections == 1
    assert alex.attention_reason is None
    new_confirmation = notices[-1]
    assert (new_confirmation.kind, new_confirmation.recipient) == (
        "prospect_confirmation",
        ROBIN_MOBILE,
    )
    held = [n for n in notices if n.channel != "sms"]
    assert [(n.status, n.waits_for_notice_id) for n in held] == [
        ("held", new_confirmation.id),
        ("held", new_confirmation.id),
    ]

    await world.send_pending(NOW + timedelta(minutes=2))
    _, _, notices = await rows()
    assert [n.status for n in notices if n.channel != "sms"] == ["sent", "sent"]


@pytest.mark.asyncio
async def test_the_mobile_can_be_corrected_twice(world):
    token = (await book(world)).json()["cancel_token"]
    path = f"{PUBLIC}/bookings/mobile"
    assert (
        await post(world, path, {"cancel_token": token, "mobile": ROBIN_MOBILE})
    ).status_code == 200
    assert (
        await post(world, path, {"cancel_token": token, "mobile": CASEY_MOBILE})
    ).status_code == 200

    third = await post(world, path, {"cancel_token": token, "mobile": "+14125550120"})

    assert refusal(third) == (429, "corrections_used", ["mobile"])
    _, (alex,), notices = await rows()
    assert alex.mobile == CASEY_MOBILE
    assert [n.recipient for n in notices if n.kind == "prospect_confirmation"] == [
        ALEX_MOBILE,
        ROBIN_MOBILE,
        CASEY_MOBILE,
    ]


@pytest.mark.asyncio
async def test_a_refused_correction_keeps_the_old_number(world):
    await store_settings(send_mode="live", test_sends_enabled=True)
    token = (await book(world)).json()["cancel_token"]
    world.texts.refuse(ROBIN_MOBILE, "unreachable_number")

    answer = await post(
        world, f"{PUBLIC}/bookings/mobile", {"cancel_token": token, "mobile": ROBIN_MOBILE}
    )

    assert refusal(answer) == (422, "number_refused", ["mobile"])
    _, (alex,), notices = await rows()
    assert (alex.mobile, alex.status) == (ALEX_MOBILE, "booked")
    assert [n.waits_for_notice_id for n in notices if n.channel != "sms"] == [notices[0].id] * 2


@pytest.mark.parametrize(
    ("mobile", "code"),
    [("12", "invalid"), ("412-555-0117", "same_number")],
)
@pytest.mark.asyncio
async def test_a_correction_needs_a_new_valid_number(world, mobile, code):
    token = (await book(world)).json()["cancel_token"]
    answer = await post(
        world, f"{PUBLIC}/bookings/mobile", {"cancel_token": token, "mobile": mobile}
    )
    assert refusal(answer) == (422, code, ["mobile"])
    _, (alex,), _ = await rows()
    assert alex.mobile_corrections == 0


@pytest.mark.asyncio
async def test_a_withdrawn_booking_cannot_be_corrected(world):
    await store_settings(send_mode="live", test_sends_enabled=True)
    world.texts.refuse(ALEX_MOBILE, "invalid_number")
    await book(world)
    _, (alex,), _ = await rows()

    answer = await post(
        world,
        f"{PUBLIC}/bookings/mobile",
        {"cancel_token": alex.cancel_token, "mobile": ROBIN_MOBILE},
    )

    assert refusal(answer) == (409, "not_booked", [])
    lookup = await post(world, f"{PUBLIC}/bookings/lookup", {"cancel_token": alex.cancel_token})
    assert lookup.json()["status"] == "cancelled"
    _, _, notices = await rows()
    assert statuses(notices)[0] == ("prospect_confirmation", "failed")
