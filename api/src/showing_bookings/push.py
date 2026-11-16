"""The team's alerts, through the Sernia web push the approval flow already uses.

``notify_all_sernia_users`` (``agent_conversations/push/service.py``) pushes to
every phone and browser a team member has turned Sernia notifications on for,
with the VAPID key the app already has: no new provider and no new secret.
Tapping the alert opens the page it names. Only ``outbox.py`` may use this
class, and only in production; elsewhere an alert is logged and kept on its
notice, never pushed.
"""


class PushTeamAlerter:
    """A ``TeamAlerter`` that pushes to every Sernia subscription."""

    async def send_alert(self, title: str, body: str, url: str) -> None:
        # Imported here: the push service reads its key when first used, which
        # nothing else in this module needs.
        from api.src.agent_conversations.push.service import notify_all_sernia_users

        await notify_all_sernia_users(
            title=title, body=body, data={"url": url, "type": "showing_alert"}
        )
