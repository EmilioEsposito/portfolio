"""The external-email approval gate and the Zillow auto-reply's scoped opt-out.

Zillow runs are driven by email from strangers. When the operator turns
`require_approval` off, the opt-out must cover only what the feature needs —
a threaded reply to Zillow's relay — and nothing an injected instruction
could redirect it to.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic_ai import ApprovalRequired

from api.src.sernia_ai.tools import google_tools
from api.src.sernia_ai.tools.google_tools import is_auto_approved_reply
from api.src.sernia_ai.triggers.zillow_email_event_trigger import (
    ZILLOW_EMAIL_DOMAIN,
    is_zillow_email,
)

ZILLOW_SCOPE = frozenset({ZILLOW_EMAIL_DOMAIN})
LEAD = "abc123@convo.zillow.com"


class TestAutoApprovedReplyScope:
    def test_threaded_reply_to_relay_is_in_scope(self):
        assert is_auto_approved_reply([LEAD], "msg-1", ZILLOW_SCOPE)
        assert is_auto_approved_reply(["Lead@Zillow.com"], "msg-1", ZILLOW_SCOPE)

    def test_no_scope_means_no_auto_approval(self):
        assert not is_auto_approved_reply([LEAD], "msg-1", frozenset())

    def test_other_recipients_are_out_of_scope(self):
        assert not is_auto_approved_reply(["attacker@evil.com"], "msg-1", ZILLOW_SCOPE)
        # One out-of-scope recipient taints the whole send.
        assert not is_auto_approved_reply([LEAD, "attacker@evil.com"], "msg-1", ZILLOW_SCOPE)

    def test_lookalike_domains_are_out_of_scope(self):
        for addr in ("x@zillow.com.evil.com", "x@notzillow.com", "x@evilzillow.com"):
            assert not is_auto_approved_reply([addr], "msg-1", ZILLOW_SCOPE), addr

    def test_new_thread_is_out_of_scope(self):
        assert not is_auto_approved_reply([LEAD], "", ZILLOW_SCOPE)
        assert not is_auto_approved_reply([LEAD], "  ", ZILLOW_SCOPE)

    def test_empty_recipient_list_is_out_of_scope(self):
        assert not is_auto_approved_reply([], "msg-1", ZILLOW_SCOPE)


def _ctx(scope: frozenset[str]) -> SimpleNamespace:
    return SimpleNamespace(
        tool_call_approved=False,
        deps=SimpleNamespace(
            user_email="emilio@serniacapital.com", auto_approved_reply_domains=scope
        ),
    )


THREADING = {
    "thread_id": "t1",
    "in_reply_to": "<orig@zillow.com>",
    "references": "<orig@zillow.com>",
}


class TestSendEmailGate:
    @pytest.fixture
    def headers(self, monkeypatch):
        """Gmail's answer for the message being replied to; found by default."""
        lookup = AsyncMock(return_value=dict(THREADING))
        monkeypatch.setattr(google_tools, "_get_threading_headers", lookup)
        return lookup

    @pytest.fixture
    def send(self, monkeypatch, headers):
        send = AsyncMock(return_value={"id": "sent-1"})
        monkeypatch.setattr(google_tools, "_send_email", send)
        monkeypatch.setattr(google_tools, "get_delegated_credentials", lambda **_: object())
        return send

    @pytest.mark.asyncio
    async def test_scoped_run_sends_relay_reply_without_approval(self, send):
        result = await google_tools.send_email(
            _ctx(ZILLOW_SCOPE), [LEAD], "Re: 320 Main St", "Hi", reply_to_message_id="msg-1"
        )
        assert "Email sent" in result
        send.assert_awaited_once()
        # The reply is actually threaded onto the message it named.
        assert send.await_args.kwargs["in_reply_to"] == THREADING["in_reply_to"]

    @pytest.mark.asyncio
    async def test_scoped_run_gates_a_reply_id_that_does_not_resolve(self, send, headers):
        """A fabricated or stale ID (or a failed Gmail lookup) yields no
        headers; without this check the email would go out unreviewed as a
        brand-new thread."""
        headers.return_value = {}
        with pytest.raises(ApprovalRequired):
            await google_tools.send_email(
                _ctx(ZILLOW_SCOPE), [LEAD], "Re: 320 Main St", "Hi", reply_to_message_id="made-up"
            )
        send.assert_not_called()

    @pytest.mark.asyncio
    async def test_reply_found_only_in_the_users_mailbox_still_counts(self, send, headers):
        """The trigger's message IDs come from a different mailbox than the
        one replies are sent from; the fallback lookup must keep working."""

        async def lookup(message_id: str, user_email: str) -> dict:
            return {} if user_email == google_tools.SHARED_EXTERNAL_EMAIL else dict(THREADING)

        headers.side_effect = lookup
        await google_tools.send_email(
            _ctx(ZILLOW_SCOPE), [LEAD], "Re: 320 Main St", "Hi", reply_to_message_id="msg-1"
        )
        sent = send.await_args.kwargs
        assert sent["in_reply_to"] == THREADING["in_reply_to"]
        assert "thread_id" not in sent  # mailbox-specific, dropped on fallback

    @pytest.mark.asyncio
    async def test_approved_send_with_unresolved_reply_id_still_sends(self, send, headers):
        """Unchanged behavior once a human approves: an unresolvable ID just
        means the email goes out unthreaded."""
        headers.return_value = {}
        ctx = _ctx(frozenset())
        ctx.tool_call_approved = True
        result = await google_tools.send_email(
            ctx, ["tenant@example.com"], "Hello", "Hi", reply_to_message_id="gone"
        )
        assert "Email sent" in result

    @pytest.mark.asyncio
    async def test_scoped_run_still_gates_email_to_anyone_else(self, send):
        """The injection case: a lead's message tells the agent to email a third party."""
        with pytest.raises(ApprovalRequired):
            await google_tools.send_email(
                _ctx(ZILLOW_SCOPE),
                ["attacker@evil.com"],
                "Tenant list",
                "…",
                reply_to_message_id="msg-1",
            )
        send.assert_not_called()

    @pytest.mark.asyncio
    async def test_scoped_run_still_gates_unthreaded_email(self, send):
        with pytest.raises(ApprovalRequired):
            await google_tools.send_email(_ctx(ZILLOW_SCOPE), [LEAD], "Hello", "Hi")
        send.assert_not_called()

    @pytest.mark.asyncio
    async def test_unscoped_run_gates_relay_reply(self, send):
        with pytest.raises(ApprovalRequired):
            await google_tools.send_email(
                _ctx(frozenset()), [LEAD], "Re: 320 Main St", "Hi", reply_to_message_id="msg-1"
            )
        send.assert_not_called()


class TestIsZillowEmailHeaderForms:
    def test_accepts_display_name_form(self):
        assert is_zillow_email('"Zillow Rentals" <abc123@convo.zillow.com>')
        assert is_zillow_email("Zillow <noreply@zillow.com>")

    def test_rejects_zillow_address_in_display_name(self):
        assert not is_zillow_email('"noreply@zillow.com" <attacker@evil.com>')

    def test_rejects_zillow_as_a_subdomain_of_another_domain(self):
        assert not is_zillow_email("lead@zillow.com.evil.com")
        assert not is_zillow_email("Zillow <lead@convo.zillow.com.evil.com>")
