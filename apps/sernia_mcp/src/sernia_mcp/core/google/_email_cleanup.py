"""Email-body cleanup helpers used by ``read_email_thread_core``.

Vendored from ``api/src/sernia_ai/tools/google_tools.py`` minus the
LLM-summarizer fallback (the MCP server has no LLM dep). Three concerns:

  1. **HTML → Markdown** — strip layout tables and convert remaining HTML
     to readable markdown so Zillow / Gmail HTML emails don't render as
     a wall of HTML tags.
  2. **Zillow boilerplate stripping** — Zillow notification emails wrap
     a short renter message in 80%+ boilerplate (header, button text,
     safety disclaimers, fair-housing notices). Extract the actual
     message via the ``[Name] says:`` anchor; fall back to pattern-based
     cleanup when no ``says:`` section exists.
  3. **Quoted-reply collapsing** — in a thread view, each message is
     already shown in full, so 3+ consecutive ``>`` lines (and the
     ``On ... wrote:`` attribution) are redundant noise.

All three are pure-Python (regex / BeautifulSoup) — no network, no LLM.
"""
from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# HTML → Markdown
# ---------------------------------------------------------------------------


def html_to_markdown(html: str) -> str:
    """Convert HTML email to clean markdown.

    Email HTML uses layout tables extensively, so we strip table tags
    (keeping their text) and remove non-content elements before converting.
    """
    from bs4 import BeautifulSoup
    from markdownify import markdownify as md

    soup = BeautifulSoup(html, "html.parser")
    for tag in soup.find_all(["script", "style", "img"]):
        tag.decompose()
    cleaned = str(soup)

    result = md(
        cleaned, strip=["table", "tr", "td", "th", "tbody", "thead", "tfoot"]
    )
    return re.sub(r"\n{3,}", "\n\n", result).strip()


# ---------------------------------------------------------------------------
# Zillow boilerplate stripping
# ---------------------------------------------------------------------------

_ZILLOW_BOILERPLATE_RE = [
    r"New message from a renter\.?\s*",
    r"A renter sent you a message about your listing at [^.]+\.\s*",
    r"You can reply on Zillow or directly to this (?:email|message)\.?\s*",
    r"(?:Reply|View) on Zillow\.?\s*",
    # Safety disclaimer block (may span multiple lines)
    r"For your safety,?\s*always double[- ]check.*?(?:staying safe|the other party)\.?\s*",
    r"Do not send payment or share personal financial information[^.]*\.?\s*",
    r"Learn about staying safe\.?\s*",
]
_ZILLOW_BOILERPLATE_COMPILED = [
    re.compile(p, re.IGNORECASE | re.DOTALL) for p in _ZILLOW_BOILERPLATE_RE
]


def is_zillow_content(from_addr: str = "", content: str = "") -> bool:
    """True if this email appears to be from Zillow (sender or content)."""
    return "zillow.com" in from_addr.lower() or "zillow.com" in content[:500].lower()


def _strip_zillow_tail(content: str) -> str:
    """Remove Zillow boilerplate via pattern matching."""
    # URLs — use [^\s)] to avoid eating the closing ')' of markdown links
    content = re.sub(r"https?://[^\s)]{80,}", "", content)
    content = re.sub(r"https?://zillow\.com/r/\S+", "", content)

    # Action-button markdown links BEFORE flattening
    content = re.sub(r"\[Reply to \w+(?:\s+\w+)*\]\([^)]*\)", "", content)
    content = re.sub(r"\[Send Application\]\([^)]*\)", "", content)

    # Flatten remaining markdown links — keep text, strip syntax
    content = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", content)
    content = re.sub(r"\[([^\]]+)\]\(\s*", r"\1", content)
    content = re.sub(r"\[\]\([^)]*\)", "", content)
    content = re.sub(r"\[\]\(\s*", "", content)

    # Known boilerplate phrases
    for pat in _ZILLOW_BOILERPLATE_COMPILED:
        content = pat.sub("", content)

    content = re.sub(
        r"You can also reply directly to this (?:email|message)\.?\s*", "", content
    )
    content = re.sub(r"^\s*(?:Yes|No)\s*$", "", content, flags=re.MULTILINE)
    content = re.sub(
        r"^.*(?:utm_|campaign=|headerOnly|MessageTemplate|content-info|"
        r"term=visit|term=rental|AssistanceProgram).*$",
        "",
        content,
        flags=re.MULTILINE,
    )
    content = re.sub(r"Reminder:\s*The\s+federal\s+.*", "", content, flags=re.DOTALL)
    content = re.sub(
        r"(?:The basics of|the basics of)\s+(?:the\s+)?[Ff]air [Hh]ousing.*",
        "",
        content,
        flags=re.DOTALL,
    )
    content = re.sub(r"Other helpful links.*", "", content, flags=re.DOTALL)
    content = re.sub(r"\n{3,}", "\n\n", content)
    return content.strip()


def clean_zillow_email(content: str) -> str:
    """Extract the actual renter message from Zillow notification boilerplate.

    Primary strategy: split on the *last* ``[Name] says:`` anchor (earlier
    occurrences could be quoted content), keep what follows, then run the
    tail-cleaner on the result. Falls back to full-content tail-cleanup
    when no ``says:`` anchor is found (e.g. initial notifications, or our
    own outbound replies that get echoed back via the thread).
    """
    says_pattern = re.compile(r"[A-Z]\w+(?:\s+\w+)*\s+says:\s*")
    matches = list(says_pattern.finditer(content))
    if matches:
        message = content[matches[-1].end():].strip()
        if message:
            message = _strip_zillow_tail(message)
            if message:
                return message

    return _strip_zillow_tail(content)


# ---------------------------------------------------------------------------
# Quoted-reply collapsing
# ---------------------------------------------------------------------------


def strip_quoted_replies(text: str) -> str:
    """Strip quoted reply blocks (3+ consecutive ``>`` lines) from email text.

    In a thread view each message is already shown in full, so re-quoted
    text is redundant noise. Also strips the ``On ... wrote:`` attribution
    that precedes the block. Inline quotes (1-2 ``>`` lines) are kept.
    """
    lines = text.split("\n")
    result: list[str] = []
    i = 0
    while i < len(lines):
        if lines[i].strip().startswith(">"):
            start = i
            while i < len(lines) and lines[i].strip().startswith(">"):
                i += 1
            if i - start >= 3:
                # Drop trailing blank lines from previous content
                while result and result[-1].strip() == "":
                    result.pop()
                # Drop the "On ... wrote:" attribution above the block
                if result and re.search(r"wrote:\s*$", result[-1]):
                    result.pop()
                    # Attribution can wrap across two lines
                    if result and re.search(r"^On .+", result[-1]):
                        result.pop()
                    while result and result[-1].strip() == "":
                        result.pop()
                result.append("[...quoted reply trimmed...]")
            else:
                result.extend(lines[start:i])
        else:
            result.append(lines[i])
            i += 1
    return "\n".join(result)
