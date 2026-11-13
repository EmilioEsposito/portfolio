"""Loading a Zillow lead thread from the shared mailbox (Gmail is faked)."""

import base64
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from googleapiclient.errors import HttpError

from api.src.sernia_ai.config import GOOGLE_DELEGATION_EMAIL, SHARED_EXTERNAL_EMAIL
from api.src.sernia_ai.zillow_leads import thread as lead_thread

LEAD = "Jane Doe <abc123@convo.zillow.com>"
SERNIA = f"Sernia Capital <{SHARED_EXTERNAL_EMAIL}>"


def _http_error(status: int) -> HttpError:
    return HttpError(SimpleNamespace(status=status, reason="x"), b"{}")


def _message(
    message_id: str,
    *,
    rfc_id: str,
    sender: str = LEAD,
    text: str | None = "Hello",
    html: str | None = None,
    at: datetime = datetime(2026, 5, 1, 12, 0, tzinfo=UTC),
    reply_to: str | None = None,
    thread_id: str = "shared-thread",
) -> dict:
    headers = [
        {"name": "Message-ID", "value": rfc_id},
        {"name": "From", "value": sender},
        {"name": "Subject", "value": "Inquiry about 123 Main St"},
    ]
    if reply_to:
        headers.append({"name": "Reply-To", "value": reply_to})
    parts = []
    for mime, content in (("text/plain", text), ("text/html", html)):
        if content is not None:
            data = base64.urlsafe_b64encode(content.encode()).decode()
            parts.append({"mimeType": mime, "body": {"data": data}})
    return {
        "id": message_id,
        "threadId": thread_id,
        "internalDate": str(int(at.timestamp() * 1000)),
        "payload": {"mimeType": "multipart/alternative", "headers": headers, "parts": parts},
    }


class _Call:
    def __init__(self, fn):
        self._fn = fn

    def execute(self):
        return self._fn()


class FakeGmail:
    """One mailbox: messages by Gmail ID, threads by thread ID."""

    def __init__(self, messages: list[dict], *, error: HttpError | None = None):
        self.messages_by_id = {m["id"]: m for m in messages}
        self.error = error
        self.searches: list[str] = []

    def users(self):
        return self

    def messages(self):
        return SimpleNamespace(get=self._get_message, list=self._list_messages)

    def threads(self):
        return SimpleNamespace(get=self._get_thread)

    def _raise_if_broken(self):
        if self.error:
            raise self.error

    def _get_message(self, *, id, **_):
        def run():
            self._raise_if_broken()
            if id not in self.messages_by_id:
                raise _http_error(404)
            return self.messages_by_id[id]

        return _Call(run)

    def _list_messages(self, *, q, **_):
        def run():
            self._raise_if_broken()
            self.searches.append(q)
            wanted = q.removeprefix("rfc822msgid:")
            hits = [
                {"id": m["id"], "threadId": m["threadId"]}
                for m in self.messages_by_id.values()
                if {h["name"]: h["value"] for h in m["payload"]["headers"]}["Message-ID"] == wanted
            ]
            return {"messages": hits} if hits else {}

        return _Call(run)

    def _get_thread(self, *, id, **_):
        def run():
            self._raise_if_broken()
            messages = [m for m in self.messages_by_id.values() if m["threadId"] == id]
            if not messages:
                raise _http_error(404)
            return {"id": id, "messages": messages}

        return _Call(run)


@pytest.fixture
def mailboxes(monkeypatch):
    """Install fake mailboxes; returns the dict so a test can fill them in."""
    boxes: dict[str, FakeGmail] = {
        GOOGLE_DELEGATION_EMAIL: FakeGmail([]),
        SHARED_EXTERNAL_EMAIL: FakeGmail([]),
    }
    monkeypatch.setattr(lead_thread, "_gmail", lambda mailbox: boxes[mailbox])
    return boxes


class TestFindSharedCopy:
    @pytest.mark.asyncio
    async def test_finds_the_shared_copy_by_rfc_message_id(self, mailboxes):
        # Same email, different Gmail IDs in each mailbox — only the RFC ID matches.
        mailboxes[GOOGLE_DELEGATION_EMAIL] = FakeGmail(
            [_message("watched-1", rfc_id="<m1@zillow>", thread_id="watched-thread")]
        )
        mailboxes[SHARED_EXTERNAL_EMAIL] = FakeGmail(
            [
                _message("shared-0", rfc_id="<m0@zillow>"),
                _message("shared-1", rfc_id="<m1@zillow>"),
            ]
        )

        copy = await lead_thread.find_shared_copy("watched-1")

        assert copy == lead_thread.SharedCopy(
            message_id="shared-1", thread_id="shared-thread", rfc_message_id="<m1@zillow>"
        )
        assert mailboxes[SHARED_EXTERNAL_EMAIL].searches == ["rfc822msgid:<m1@zillow>"]

    @pytest.mark.asyncio
    async def test_none_when_the_email_is_gone_from_the_watched_mailbox(self, mailboxes):
        assert await lead_thread.find_shared_copy("deleted") is None
        assert mailboxes[SHARED_EXTERNAL_EMAIL].searches == []

    @pytest.mark.asyncio
    async def test_none_when_the_shared_mailbox_has_no_copy(self, mailboxes):
        mailboxes[GOOGLE_DELEGATION_EMAIL] = FakeGmail([_message("watched-1", rfc_id="<m1@z>")])
        assert await lead_thread.find_shared_copy("watched-1") is None

    @pytest.mark.asyncio
    async def test_gmail_outages_are_raised_not_read_as_missing(self, mailboxes):
        # A 500 must not look like "no shared copy": the queue retries errors,
        # but would hand the agent the wrong mailbox's IDs for a None.
        mailboxes[GOOGLE_DELEGATION_EMAIL] = FakeGmail([], error=_http_error(500))
        with pytest.raises(HttpError):
            await lead_thread.find_shared_copy("watched-1")


class TestLoadLeadThread:
    @pytest.mark.asyncio
    async def test_returns_messages_oldest_first_with_who_sent_each(self, mailboxes):
        mailboxes[SHARED_EXTERNAL_EMAIL] = FakeGmail(
            [
                _message(
                    "s2",
                    rfc_id="<r2@sernia>",
                    sender=SERNIA,
                    text="We have tours Tuesday.",
                    at=datetime(2026, 5, 1, 15, 0, tzinfo=UTC),
                ),
                _message(
                    "s1",
                    rfc_id="<r1@zillow>",
                    text="Is it available?",
                    reply_to="Jane Doe <reply-xyz@convo.zillow.com>",
                    at=datetime(2026, 5, 1, 12, 0, tzinfo=UTC),
                ),
            ]
        )

        thread = await lead_thread.load_lead_thread("shared-thread")

        assert thread is not None
        assert thread.mailbox == SHARED_EXTERNAL_EMAIL
        assert [m.message_id for m in thread.messages] == ["s1", "s2"]
        first, second = thread.messages
        assert (first.from_sernia, second.from_sernia) == (False, True)
        assert first.from_address == "abc123@convo.zillow.com"
        assert first.reply_to == "reply-xyz@convo.zillow.com"
        assert second.reply_to is None
        assert first.rfc_message_id == "<r1@zillow>"
        assert first.sent_at == datetime(2026, 5, 1, 12, 0, tzinfo=UTC)
        assert first.body == "Is it available?"

    @pytest.mark.asyncio
    async def test_quoted_history_is_dropped_from_each_message(self, mailboxes):
        quoted = (
            "Tuesday at 2 works.\n\nOn Fri, Sernia wrote:\n> line one\n> line two\n> line three"
        )
        mailboxes[SHARED_EXTERNAL_EMAIL] = FakeGmail([_message("s1", rfc_id="<r1@z>", text=quoted)])

        thread = await lead_thread.load_lead_thread("shared-thread")

        assert thread is not None
        body = thread.messages[0].body
        assert "Tuesday at 2 works." in body
        assert "line two" not in body

    @pytest.mark.asyncio
    async def test_our_reply_survives_the_zillow_message_quoted_under_it(self, mailboxes):
        # Real replies quote the lead's Zillow email, "[Name] says:" line and
        # all. The Zillow cleaner anchors on that line, so it must not run on
        # mail we sent — it would keep the quote and drop the reply.
        reply = (
            "Hello Jane,\n\nWe have tours on Tuesday at 2:00 PM.\n\n"
            "On Fri, May 1, Jane Doe <abc123@convo.zillow.com> wrote:\n"
            "> New message from a renter on zillow.com\n"
            "> Jane says:\n"
            "> Is it available?\n"
            "> Reply to Jane"
        )
        mailboxes[SHARED_EXTERNAL_EMAIL] = FakeGmail(
            [_message("s2", rfc_id="<r2@sernia>", sender=SERNIA, text=reply)]
        )

        thread = await lead_thread.load_lead_thread("shared-thread")

        assert thread is not None
        body = thread.messages[0].body
        assert "tours on Tuesday at 2:00 PM" in body
        assert "Is it available?" not in body

    @pytest.mark.asyncio
    async def test_html_only_email_is_converted_to_text(self, mailboxes):
        mailboxes[SHARED_EXTERNAL_EMAIL] = FakeGmail(
            [_message("s1", rfc_id="<r1@z>", text=None, html="<p>Do you allow <b>cats</b>?</p>")]
        )

        thread = await lead_thread.load_lead_thread("shared-thread")

        assert thread is not None
        body = thread.messages[0].body
        assert "cats" in body and "<p>" not in body

    @pytest.mark.asyncio
    @pytest.mark.usefixtures("mailboxes")
    async def test_none_when_the_thread_is_not_in_the_shared_mailbox(self):
        assert await lead_thread.load_lead_thread("watched-thread") is None


class TestTidyZillowForm:
    def test_a_relayed_message_keeps_the_lead_words_once(self):
        from api.src.sernia_ai.zillow_leads.thread import tidy_zillow_form

        body = (
            "Could we do 11 tomorrow?  Regarding your listing at:  12 Example St #A, Town, PA 15224 "
            "Some rental inquiries may be scams. If a message asks you to scan a QR code, select a "
            "link, verify your identity, or send payment, don't respond. Report it to us instead. "
            "RENTER'S NAME  Pat Doe  RENTER'S MESSAGE  Could we do 11 tomorrow?"
        )

        assert tidy_zillow_form(body) == (
            "Could we do 11 tomorrow?\n\n"
            "Zillow form details (not written by the lead):\n"
            "- Listing: 12 Example St #A, Town, PA 15224\n"
            "- Name: Pat Doe"
        )

    def test_a_first_inquiry_keeps_the_profile_and_drops_the_button(self):
        from api.src.sernia_ai.zillow_leads.thread import tidy_zillow_form

        body = (
            "I would like to schedule a tour. Send application About Pat Doe  Move in  Aug 01, 2026 "
            "\xa0 Credit score  660 to 719 \xa0 Income  $39996 \xa0 Pets  No \xa0 Lease Length  "
            "12 months \xa0 Number of Bedrooms  1 \xa0 Number of Occupants  1"
        )

        tidy = tidy_zillow_form(body)

        assert tidy.splitlines() == [
            "I would like to schedule a tour.",
            "",
            "Zillow form details (not written by the lead):",
            "- Name: Pat Doe",
            "- Move in: Aug 01, 2026",
            "- Credit score: 660 to 719",
            "- Income: $39996",
            "- Pets: No",
            "- Lease Length: 12 months",
            "- Number of Bedrooms: 1",
            "- Number of Occupants: 1",
        ]
        assert "Send application" not in tidy
        assert tidy_zillow_form(tidy) == tidy  # applying it again changes nothing

    def test_a_profile_with_fields_missing_is_still_recognised(self):
        from api.src.sernia_ai.zillow_leads.thread import tidy_zillow_form

        # No move-in date and no button: the profile starts at another field.
        body = (
            "I would like to schedule a tour. I am available Saturday  About Pat Doe \xa0 "
            "Credit score  740 to 799 \xa0 Income  $49992 \xa0 Pets  Not answered \xa0 "
            "Lease Length  12 months \xa0 Number of Bedrooms  2 \xa0 Number of Occupants  2"
        )

        tidy = tidy_zillow_form(body)

        assert tidy.splitlines() == [
            "I would like to schedule a tour. I am available Saturday",
            "",
            "Zillow form details (not written by the lead):",
            "- Name: Pat Doe",
            "- Credit score: 740 to 799",
            "- Income: $49992",
            "- Pets: Not answered",
            "- Lease Length: 12 months",
            "- Number of Bedrooms: 2",
            "- Number of Occupants: 2",
        ]
        assert tidy_zillow_form(tidy) == tidy
        assert tidy_zillow_form("Hi  About Pat Doe  Pets  Yes").endswith("- Pets: Yes")

    def test_a_sentence_that_mentions_a_field_is_not_a_profile(self):
        from api.src.sernia_ai.zillow_leads.thread import tidy_zillow_form

        for text in (
            "A question about pets: Pets are allowed, right?",
            "About the lease. Income is not a problem for us.",
            "I asked About Pets  yesterday and heard nothing.",
        ):
            assert tidy_zillow_form(text) == text

    def test_a_bare_button_is_dropped_and_plain_text_is_left_alone(self):
        from api.src.sernia_ai.zillow_leads.thread import tidy_zillow_form

        assert tidy_zillow_form("Texting is preferred. Send application") == "Texting is preferred."
        assert tidy_zillow_form("See you then!") == "See you then!"
        # Someone writing about themselves is not a Zillow profile.
        about = "A bit about me: I work nights. Move in whenever."
        assert tidy_zillow_form(about) == about

    def test_suggested_replies_after_the_button_go_with_it(self):
        from api.src.sernia_ai.zillow_leads.thread import tidy_zillow_form

        body = (
            "Just confirming you don't allow any dogs? Send applicationYes that's fineSorry I "
            "don't allow that Pat will receive your message.  Use our online application with "
            "tenant screening. Questions? Learn more about our online applications. "
            "About Pat Doe  Move in  Aug 01, 2026 \xa0 Pets  Yes"
        )

        tidy = tidy_zillow_form(body)

        assert tidy.splitlines()[0] == "Just confirming you don't allow any dogs?"
        assert "- Pets: Yes" in tidy and "online application" not in tidy
        assert tidy_zillow_form(tidy) == tidy

    def test_a_relayed_message_with_its_tail_cut_off(self):
        from api.src.sernia_ai.zillow_leads.thread import tidy_zillow_form

        warning = (
            "Some rental inquiries may be scams. If a message asks you to scan a QR code, select "
            "a link, verify your identity, or send payment, don't respond. Report it to us instead."
        )
        listing = "Regarding your listing at:  12 Example St #A, Town, PA 15224"
        wanted = (
            "A friend will come.\n\nZillow form details (not written by the lead):\n"
            "- Listing: 12 Example St #A, Town, PA 15224"
        )

        # Nothing after the warning but a stray fragment.
        assert tidy_zillow_form(f"A friend will come. {listing} {warning} 555-0100") == wanted
        # The second copy of the message is missing.
        cut = f"A friend will come. {listing} {warning} RENTER'S NAME  Pat Doe  RENTER'S MESSAGE"
        assert tidy_zillow_form(cut) == wanted + "\n- Name: Pat Doe"
        # Zillow's suggested replies sit between the address and the warning.
        buttons = f"Running late. {listing} Okay see you soon!Sorry, almost there! {warning}"
        assert tidy_zillow_form(
            buttons + " RENTER'S NAME  Pat Doe  RENTER'S MESSAGE  Running late."
        ) == (
            "Running late.\n\nZillow form details (not written by the lead):\n"
            "- Listing: 12 Example St #A, Town, PA 15224\n- Name: Pat Doe"
        )
        # No message in either place.
        bare = tidy_zillow_form(f"{listing} {warning} RENTER'S NAME  Pat Doe  RENTER'S MESSAGE")
        assert bare.splitlines()[0] == "(no message text)"
        assert tidy_zillow_form(bare) == bare
