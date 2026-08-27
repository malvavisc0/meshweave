"""Email extraction, deobfuscation, collection, and deduplication."""

import re
from typing import Any
from urllib.parse import unquote

from bs4 import BeautifulSoup
from bs4.element import Tag

__all__ = [
    "collect_emails",
    "deduplicate_sources",
    "extract_emails",
    "is_valid_email",
]

_EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[A-Za-z]{2,}")

BLOCKED_DOMAINS: frozenset[str] = frozenset({"example.com"})


def is_valid_email(email: str) -> bool:
    """Structural validation to filter obvious false positives."""
    if not email or "@" not in email:
        return False
    local, domain = email.split("@", 1)
    if not local or not domain or "." not in domain:
        return False
    if len(local) < 2:
        return False
    if local[0] in ".-" or local[-1] in ".-" or ".." in local:
        return False
    if domain[0] in ".-" or domain[-1] in ".-" or ".." in domain:
        return False
    parts = domain.split(".")
    if any(len(p) < 2 for p in parts[-2:]):
        return False
    # Reject purely numeric local parts (phone numbers, WhatsApp JIDs, etc.)
    if local.isdigit():
        return False
    # Reject IP-address domains (e.g. resource@192.168.1.210.when)
    # A valid domain TLD must not be purely numeric.
    if parts[-1].isdigit():
        return False
    # Reject domains embedding an IP address (4+ consecutive numeric segments)
    for i in range(len(parts) - 3):
        if all(parts[i + j].isdigit() for j in range(4)):
            return False
    # Reject domains with implausibly long TLDs
    if len(parts[-1]) > 8:
        return False
    # Reject blocked domains
    if domain in BLOCKED_DOMAINS or ".".join(parts[-2:]) in BLOCKED_DOMAINS:
        return False
    return True


def _deobfuscate_text(text: str) -> str:
    """Replace common textual obfuscations ([at], (dot), etc.).

    Transforms obfuscation markers like ``[at]`` / ``(dot)`` into
    their real characters, then collapses whitespace around the
    reconstructed ``@`` and ``.`` **only** when flanked by word
    characters (i.e. in email-like contexts).  This avoids corrupting
    unrelated text such as ``e.g. something`` or ``user @ company``.
    """
    s = text
    # 1. Bracket-based replacements
    s = re.sub(r"(?i)[\[\(\{]\s*at\s*[\]\)\}]", "@", s)
    s = re.sub(r"(?i)[\[\(\{]\s*dot\s*[\]\)\}]", ".", s)
    # 2. Bare-word replacements (only between non-space chars). The bare
    # "at" is converted only when the next token contains no "@" — ordinary
    # prose like "contact us at hello@acme.com" must not be rewritten to
    # "us@hello@acme.com" and then absorb following sentence punctuation.
    s = re.sub(r"(?i)(?<=\S)\s+at\s+(?=[^\s@]+(?:\s|$))", " @ ", s)
    s = re.sub(r"(?i)(?<=\S)\s+dot\s+(?=\S)", " . ", s)
    # 3. Collapse whitespace around @ / . in email-like contexts
    prev: str = ""
    while prev != s:
        prev = s
        s = re.sub(r"(\w)\s+@\s+(\w)", r"\1@\2", s)
        s = _join_dotted_fragments(s)
    return s


def _join_dotted_fragments(s: str) -> str:
    """Collapse ``token . token`` when it continues a partial email.

    A dot-fragment is joined only when the left side contains ``@``
    but is *not* already a complete email address.  This keeps the
    reconstruction of obfuscated addresses (``john@example . com``)
    working while never absorbing sentence punctuation that follows a
    complete address (``hello@meshweaveai.com . We will`` — an
    artifact of HTML text extraction inserting a separator between
    an inline link and the next text node).
    """

    def _repl(m: re.Match[str]) -> str:
        left = m.group(1)
        if _EMAIL_RE.fullmatch(left):
            return m.group(0)
        return f"{left}.{m.group(2)}"

    return re.sub(r"(\S+@\S+)\s+\.\s+(\w)", _repl, s)


def _extract_mailto_emails(soup: BeautifulSoup) -> set[str]:
    """Extract emails from mailto: links."""
    emails: set[str] = set()
    for a in soup.find_all("a", href=True):
        if not isinstance(a, Tag):
            continue
        href = str(a["href"])
        if not href.lower().startswith("mailto:"):
            continue
        addr = unquote(href[7:].split("?", 1)[0])
        for p in re.split(r"[;,]", addr):
            p = p.strip()
            if p and _EMAIL_RE.fullmatch(p):
                emails.add(p.lower())
    return emails


def _extract_text_emails(text: str, deobfuscate: bool) -> tuple[set[str], set[str]]:
    """Extract emails from visible text, optionally deobfuscating.

    Returns (all_emails, deobfuscated_only_emails) where
    *deobfuscated_only* contains emails that only appeared after
    deobfuscation (not found in the original text).
    """
    original = {m.group().lower() for m in _EMAIL_RE.finditer(text)}
    deob_only: set[str] = set()
    if deobfuscate:
        deob = _deobfuscate_text(text)
        if deob != text:
            after_deob = {m.group().lower() for m in _EMAIL_RE.finditer(deob)}
            deob_only = after_deob - original
    return original | deob_only, deob_only


def extract_emails(
    html: str, deobfuscate: bool = True
) -> tuple[set[str], list[dict[str, Any]]]:
    """Extract emails from HTML via mailto links and visible text.

    Returns (unique_emails, sources) where each source has
    'email' and 'found_as' keys.
    """
    sources: list[dict[str, Any]] = []
    soup = BeautifulSoup(html, "lxml")

    mailto_emails = _extract_mailto_emails(soup)
    for e in mailto_emails:
        sources.append({"email": e, "found_as": "mailto"})

    # Get visible text (strip scripts/styles)
    for tag in soup.find_all(("script", "style", "noscript")):
        tag.decompose()
    text = soup.get_text(" ")

    text_emails, deob_only = _extract_text_emails(text, deobfuscate)
    for e in text_emails:
        src_type = "obfuscated" if e in deob_only else "text"
        sources.append({"email": e, "found_as": src_type})

    combined = mailto_emails | text_emails
    all_emails = {e for e in combined if is_valid_email(e)}
    sources = [s for s in sources if s["email"] in all_emails]
    return all_emails, sources


def collect_emails(
    page_html: str,
    page_url: str,
    *,
    include_emails: bool,
    deobfuscate_emails: bool,
    all_emails: set[str],
    emails_by_url: dict[str, list[str]],
    email_sources: list[dict[str, Any]],
) -> None:
    """Extract emails from a page and accumulate into collections.

    Modifies *all_emails*, *emails_by_url*, and *email_sources*
    in-place.  When *include_emails* is ``False`` the function is
    a no-op.
    """
    if not include_emails:
        return
    found, srcs = extract_emails(page_html, deobfuscate_emails)
    all_emails.update(found)
    if found:
        emails_by_url[page_url] = sorted(found)
    for s in srcs:
        email_sources.append(
            {
                "email": s["email"],
                "found_as": s["found_as"],
                "url": page_url,
            }
        )


def deduplicate_sources(
    email_sources: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Deduplicate email sources by (email, url) key."""
    dedup: dict[tuple[str, str], set[str]] = {}
    for s in email_sources:
        key = (s["email"].lower(), s["url"])
        dedup.setdefault(key, set()).add(s["found_as"])
    return [
        {
            "email": k[0],
            "url": k[1],
            "found_as": sorted(v),
        }
        for k, v in dedup.items()
    ]
