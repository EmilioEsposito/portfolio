"""Which of the team's two Quo lines sends each text: decided by the notice kind, here only.

- ``ai``: the Sernia AI line. Every text to a showing agent (the host), who is
  staff, so a reply reaches Sernia AI's SMS conversation with the showing text
  in its thread.
- ``team``: the shared team line. Every text to anyone outside the team
  (prospects, leads, residents), whose replies the team reads in Quo.

A text to a showing agent has a kind starting ``host_``;
``test_showing_text_lines.py`` fails when such a kind is not listed here.
"""

from api.src.showing_bookings.delivery_checks import HOST_MORNING_KIND, HOST_TEXT_KINDS
from api.src.showing_bookings.providers import Line

AI_LINE_KINDS = HOST_TEXT_KINDS | {HOST_MORNING_KIND}


def line_for(kind: str) -> Line:
    """The line a text of ``kind`` is sent from."""
    return "ai" if kind in AI_LINE_KINDS else "team"
