"""HTML-to-Soup parsing and Markdown conversion."""

import re

from bs4 import BeautifulSoup, NavigableString, Tag
from html_to_markdown import convert

__all__ = [
    "soup_from_html",
    "to_markdown",
]


def soup_from_html(html: str) -> BeautifulSoup:
    """Parse HTML string into BeautifulSoup with lxml."""
    return BeautifulSoup(html, "lxml")


# Tags whose text nodes may contain source-level line wraps that
# should be collapsed into single spaces (per HTML whitespace rules).
_COLLAPSE_WS_TAGS = frozenset(
    {
        "p",
        "li",
        "td",
        "th",
        "dd",
        "dt",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "a",
        "span",
        "strong",
        "em",
        "b",
        "i",
        "u",
        "small",
        "big",
        "code",
        "blockquote",
        "caption",
        "label",
        "legend",
        "figcaption",
        "summary",
        "cite",
        "abbr",
        "time",
        "mark",
    }
)

# Matches a newline (with optional surrounding horizontal whitespace).
_WS_NL = re.compile(r"[ \t]*\n[ \t]*")


def _collapse_ws(soup: BeautifulSoup | Tag) -> None:
    """Collapse source-level newlines inside content tags to single spaces.

    HTML treats newlines inside flow/phrasing content as whitespace.
    Source-formatted templates often wrap paragraphs across multiple
    lines; without normalisation the markdown converter preserves those
    literal newlines, producing broken output.
    """
    for tag in soup.find_all(_COLLAPSE_WS_TAGS):
        for child in tag.children:
            if isinstance(child, NavigableString):
                original = str(child)
                collapsed = _WS_NL.sub(" ", original)
                if collapsed != original:
                    child.replace_with(collapsed)


def to_markdown(soup: BeautifulSoup) -> str:
    """Convert preprocessed soup to markdown (body only)."""
    # Use only <body> content to avoid <head> metadata in output
    body = soup.find("body")
    target = body if body else soup
    # Remove elements with class="no-md" (UI-only noise)
    for el in target.find_all(class_="no-md"):
        el.decompose()
    # Strip non-content elements that produce noise in markdown output
    for tag in target.find_all(["script", "style"]):
        tag.decompose()
    for el in target.find_all(class_="hidden"):
        el.decompose()
    # Collapse source-level newlines inside content elements so the
    # markdown converter receives properly normalised text.
    _collapse_ws(target)
    result = convert(str(target))
    md = result.content or ""

    # Strip injected HTML comment block at the top
    content = md.lstrip()
    if content.startswith("<!--"):
        end = md.find("-->")
        if end != -1:
            md = md[end + 3 :].lstrip("\n\r ")

    # Normalize spacing around markdown links:
    # - Ensure a newline between consecutive inline links ][ → ]\n[
    md = re.sub(r"\)\[", ")\n[", md)
    # - Add space after ')' when followed by a non-space, non-bracket
    #   char (but skip common markdown syntax like )\n and ][)
    md = re.sub(r"\)(?=[^\s\[\]])", ") ", md)
    # - Add space before '[' when preceded by a non-space char
    md = re.sub(r"(?<=\S)\[(?!\()", " [", md)

    # Collapse runs of 3+ blank lines to 2
    md = re.sub(r"\n{3,}", "\n\n", md)
    return md
