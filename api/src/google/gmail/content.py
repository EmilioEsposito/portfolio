"""Deterministic email cleanup shared by the general and Zillow agents."""

import re
from email.utils import parseaddr

from api.src.google.common.config import INTERNAL_EMAIL_DOMAIN


def _html_to_markdown(html: str) -> str:
    """Convert HTML email to clean markdown for LLM consumption.

    Email HTML uses layout tables extensively, so we strip table tags
    (keeping their text) and remove non-content elements before converting.
    """
    import re

    from bs4 import BeautifulSoup
    from markdownify import markdownify as md

    # Remove tags whose *content* should be discarded (not just the tag)
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup.find_all(["script", "style", "img"]):
        tag.decompose()
    cleaned = str(soup)

    # Convert remaining HTML → markdown, stripping layout table tags
    result = md(cleaned, strip=["table", "tr", "td", "th", "tbody", "thead", "tfoot"])
    # Collapse excessive blank lines
    return re.sub(r"\n{3,}", "\n\n", result).strip()


# ---------------------------------------------------------------------------
# Zillow email cleanup
# ---------------------------------------------------------------------------

# Boilerplate phrases that appear in Zillow notification emails.
# Each pattern is removed (with surrounding whitespace) from the email body.
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


def _is_zillow_content(from_addr: str = "", content: str = "") -> bool:
    """Return True if this email appears to be from Zillow.

    Never true for email we sent. Our replies quote the lead's Zillow message
    underneath, and the Zillow cleaner keeps only what follows the last
    "[Name] says:" line — in a reply that is the quoted history, so the reply
    itself would be thrown away.
    """
    if parseaddr(from_addr)[1].lower().endswith(f"@{INTERNAL_EMAIL_DOMAIN}"):
        return False
    return "zillow.com" in from_addr.lower() or "zillow.com" in content[:500].lower()


def _clean_zillow_email(content: str) -> str:
    """Extract the actual renter message from Zillow notification boilerplate.

    Zillow wraps a short renter message in boilerplate (header, suggested
    reply buttons, safety disclaimers, Fair Housing notices).  The real
    content is in the "[Name] says:" section near the end.  Extract that
    and discard everything above it (including confusing suggested-reply
    button text).  Falls back to pattern-based cleanup when no "says:"
    section is found (initial notifications, our own replies, etc.).
    """
    # -- Primary strategy: extract from "[Name] says:" -----------------------
    # Find the *last* occurrence — earlier ones could be quoted content.
    says_pattern = re.compile(r"[A-Z]\w+(?:\s+\w+)*\s+says:\s*")
    matches = list(says_pattern.finditer(content))
    if matches:
        message = content[matches[-1].end() :].strip()
        if message:
            # Strip trailing boilerplate that may follow the message
            message = _strip_zillow_tail(message)
            if message:
                return message

    # -- Fallback: no usable "says:" section ---------------------------------
    return _strip_zillow_tail(content)


def _strip_zillow_tail(content: str) -> str:
    """Remove Zillow boilerplate from *content* using pattern matching.

    Used both as the tail-cleaner after a "says:" extraction and as the
    full-content fallback when no "says:" anchor is found.
    """
    # URLs — use [^\s)] to avoid eating the closing ")" of markdown links
    content = re.sub(r"https?://[^\s)]{80,}", "", content)
    content = re.sub(r"https?://zillow\.com/r/\S+", "", content)

    # Remove action-button markdown links BEFORE flattening so they don't
    # merge with adjacent text (no space between adjacent links in Zillow HTML).
    content = re.sub(r"\[Reply to \w+(?:\s+\w+)*\]\([^)]*\)", "", content)
    content = re.sub(r"\[Send Application\]\([^)]*\)", "", content)

    # Flatten remaining markdown links — keep text, strip syntax
    content = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", content)  # [text](url) → text
    content = re.sub(r"\[([^\]]+)\]\(\s*", r"\1", content)  # [text]( → text
    content = re.sub(r"\[\]\([^)]*\)", "", content)  # [](url) → remove
    content = re.sub(r"\[\]\(\s*", "", content)  # []( → remove

    # Known boilerplate phrases
    for pat in _ZILLOW_BOILERPLATE_COMPILED:
        content = pat.sub("", content)

    # Action button text (plain text variants, for cases without link syntax)
    content = re.sub(r"You can also reply directly to this (?:email|message)\.?\s*", "", content)

    # Standalone button text
    content = re.sub(r"^\s*(?:Yes|No)\s*$", "", content, flags=re.MULTILINE)

    # UTM / tracking-param lines
    content = re.sub(
        r"^.*(?:utm_|campaign=|headerOnly|MessageTemplate|content-info|"
        r"term=visit|term=rental|AssistanceProgram).*$",
        "",
        content,
        flags=re.MULTILINE,
    )

    # "Reminder: The federal Fair Housing Act..." and everything after
    content = re.sub(r"Reminder:\s*The\s+federal\s+.*", "", content, flags=re.DOTALL)

    # "the basics of fair housing..." and everything after
    content = re.sub(
        r"(?:The basics of|the basics of)\s+(?:the\s+)?[Ff]air [Hh]ousing.*",
        "",
        content,
        flags=re.DOTALL,
    )

    # "Other helpful links" section and everything after
    content = re.sub(r"Other helpful links.*", "", content, flags=re.DOTALL)

    # Collapse blank lines
    content = re.sub(r"\n{3,}", "\n\n", content)
    return content.strip()


def _strip_quoted_replies(text: str) -> str:
    """Strip quoted reply blocks from email text.

    In a thread view each message is already shown in full, so re-quoted
    text (3+ consecutive lines starting with '>') is redundant.
    Also strips the 'On ... wrote:' attribution line that precedes the block.
    """
    import re

    lines = text.split("\n")
    result: list[str] = []
    i = 0
    while i < len(lines):
        stripped = lines[i].strip()
        if stripped.startswith(">"):
            # Count consecutive '>' lines
            start = i
            while i < len(lines) and lines[i].strip().startswith(">"):
                i += 1
            if i - start >= 3:
                # Also remove the "On ... wrote:" attribution above
                while result and result[-1].strip() == "":
                    result.pop()
                if result and re.search(r"wrote:\s*$", result[-1]):
                    result.pop()
                    # Attribution can wrap across two lines (name on one, "wrote:" on next)
                    if result and re.search(r"^On .+", result[-1]):
                        result.pop()
                    # Clean trailing blank lines again
                    while result and result[-1].strip() == "":
                        result.pop()
                result.append("[...quoted reply trimmed...]")
            else:
                # Short quote — keep it (could be an inline quote)
                result.extend(lines[start:i])
        else:
            result.append(lines[i])
            i += 1
    return "\n".join(result)
