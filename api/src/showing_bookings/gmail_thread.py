"""The live ``EmailSender``: email through Gmail, from the shared external mailbox.

It sends the way the lead agent's replies go (``zillow_leads.handle``). Only
``outbox.py`` uses it, after the guard.

- In a Zillow lead's thread (``send_in_thread``): the thread's headers read for
  the message the route answers, then ``handle.send_thread_email``, the same
  Gmail call as ``send_reply``, with a route the outbox read from the lead
  conversation just before.
- A new email to a prospect (``send_new``): ``handle.send_new_email``, the
  same Gmail call and credentials with no thread, as plain text with an HTML
  alternative.

- Outside production, to an address off the test list
  (``handle.captures_lead_email``): ``NotSent``, nothing read or sent.
- The thread's headers cannot be read: nothing went out, ``NotSent`` and
  tried again later. A reply is never sent outside the lead's thread.
- Gmail refused it (a 4xx other than a timeout, by the lead agent's own
  ``handle.send_result``): ``NotSent``, saved as failed.
- Anything else may have gone out: the error propagates and the outbox saves
  it as unknown, never retried.

It refuses to run under pytest; tests pass ``fakes.RecordingEmailSender``.
"""

import os

from api.src.showing_bookings.providers import NewEmail, NotSent, SentEmail, ThreadRoute


class LiveGmailInTests(RuntimeError):
    """The live Gmail sender was reached from a test."""


class GmailThreadSender:
    async def send_in_thread(self, route: ThreadRoute, body: str) -> SentEmail:
        if "PYTEST_CURRENT_TEST" in os.environ:
            raise LiveGmailInTests(
                "Tests pass a stand-in EmailSender; the live Gmail sender is never used in tests."
            )
        # Imported here: the lead agent's module imports this package.
        from api.src.zillow_leads import handle

        if handle.captures_lead_email(route.to):
            # The guard sends here outside production only to the test list,
            # which is the same rule, so this answers only if the two disagree.
            raise NotSent("not_production", "A lead's email goes out only from production.")
        threading = await handle._get_threading_headers(
            route.reply_to_message_id, handle.SHARED_EXTERNAL_EMAIL
        )
        if not threading.get("thread_id") or not threading.get("in_reply_to"):
            raise NotSent(
                "thread_unreadable",
                "The lead's email thread could not be read, so nothing was sent.",
                retryable=True,
            )
        stored = {
            "to": route.to,
            "subject": route.subject,
            "reply_to_message_id": route.reply_to_message_id,
        }
        try:
            result = await handle.send_thread_email(stored, body, threading=threading)
        except Exception as error:
            if handle.send_result(error) == "failed":
                raise NotSent("rejected", "Gmail refused the email.") from error
            raise
        return SentEmail(provider_id=str(result.get("id") or "unknown"))

    async def send_new(self, email: NewEmail) -> SentEmail:
        if "PYTEST_CURRENT_TEST" in os.environ:
            raise LiveGmailInTests(
                "Tests pass a stand-in EmailSender; the live Gmail sender is never used in tests."
            )
        from api.src.zillow_leads import handle

        try:
            result = await handle.send_new_email(email.to, email.subject, email.text, email.html)
        except Exception as error:
            if handle.send_result(error) == "failed":
                raise NotSent("rejected", "Gmail refused the email.") from error
            raise
        return SentEmail(provider_id=str(result.get("id") or "unknown"))
