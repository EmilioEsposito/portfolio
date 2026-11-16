"""The live ``EmailSender``: an email in a Zillow lead's thread, through Gmail.

It sends the way the lead agent's replies go (``zillow_leads.handle``): the
thread's headers read for the message the route answers, then
``handle.send_thread_email``, the same Gmail call as ``send_reply``, from the
shared external mailbox. Only ``outbox.py`` uses it, after the guard, with a
route the outbox read from the lead conversation just before.

- The thread's headers cannot be read: nothing went out, ``NotSent`` and
  tried again later. An email is never sent outside the lead's thread.
- Gmail refused it (a 4xx other than a timeout, by the lead agent's own
  ``handle.send_result``): ``NotSent``, saved as failed.
- Anything else may have gone out: the error propagates and the outbox saves
  it as unknown, never retried.

It refuses to run under pytest; tests pass ``fakes.RecordingEmailSender``.
"""

import os

from api.src.showing_bookings.providers import NotSent, SentEmail, ThreadRoute


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
