"""HTML-to-Soup parsing and Markdown conversion."""

import re

from bs4 import BeautifulSoup
from html_to_markdown import convert

__all__ = [
    "soup_from_html",
    "to_markdown",
]


def soup_from_html(html: str) -> BeautifulSoup:
    """Parse HTML string into BeautifulSoup with lxml."""
    return BeautifulSoup(html, "lxml")


def to_markdown(soup: BeautifulSoup) -> str:
    """Convert preprocessed soup to markdown (body only)."""
    # Use only <body> content to avoid <head> metadata in output
    body = soup.find("body")
    target = body if body else soup
    # Remove elements with class="no-md" (UI-only noise)
    for el in target.find_all(class_="no-md"):
        el.decompose()
    result = convert(str(target))
    md = result.content

    # Strip injected HTML comment block at the top
    if md.lstrip().startswith("<!--"):
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
    return md
