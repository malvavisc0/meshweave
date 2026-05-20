"""HTML soup preprocessing: noise removal and anchor normalization."""

from __future__ import annotations

import copy
import re
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup
from bs4.element import Comment, Tag

__all__ = [
    "preprocess_soup",
]

_NOISE_TAGS = ("nav", "footer", "header", "aside")
_NOISE_ROLES = re.compile(
    r"^(navigation|banner|contentinfo|complementary|" r"dialog|alert)$",
    re.I,
)
_NOISE_CLASSES = re.compile(
    r"(cookie|consent|gdpr|nav(bar|-links)?|menu(-bar)?|"
    r"site-header|page-header|main-header|"
    r"site-footer|page-footer|main-footer|"
    r"social(-links)?|ad(s|vertisement|wrapper)?|"
    r"popup|modal|banner|overlay|tooltip)",
    re.I,
)
_NOISE_IDS = re.compile(
    r"(cookie|consent|gdpr|ad(s|vertisement|wrapper)?|" r"popup|modal|banner|overlay)",
    re.I,
)


def preprocess_soup(
    soup: BeautifulSoup,
    base_url: str,
    final_url: str,
    *,
    copy_first: bool = True,
) -> BeautifulSoup:
    """Remove noise, strip images, normalize anchors.

    By default the function operates on a *copy* of *soup* so the
    caller's original tree is preserved.  Pass ``copy_first=False``
    to mutate in-place (slightly faster, but destructive).
    """
    if copy_first:
        soup = copy.deepcopy(soup)

    # Remove noise tags
    for tag_name in _NOISE_TAGS:
        for node in soup.find_all(tag_name):
            node.decompose()

    # Remove by role
    for node in soup.find_all(attrs={"role": _NOISE_ROLES}):
        node.decompose()

    # Remove by class / id patterns
    for node in soup.find_all(True, {"class": _NOISE_CLASSES}):
        node.decompose()
    for node in soup.find_all(True, id=_NOISE_IDS):
        node.decompose()

    # Strip HTML comments
    for c in soup.find_all(string=lambda t: isinstance(t, Comment)):
        c.extract()

    # Resolve base for URL operations
    base = final_url or base_url or ""

    # Process anchors: normalize text, preserve href for downstream
    for a in soup.find_all("a"):
        if not isinstance(a, Tag):
            continue

        href = str(a.get("href", ""))

        # Remove image-like children
        for img in a.find_all(("img", "picture", "svg")):
            if isinstance(img, Tag):
                img.decompose()

        # Compute visible text after stripping images
        text = " ".join(a.get_text(strip=True).split())

        # Compute fallback label from domain
        label = text
        if not label:
            try:
                abs_url = urljoin(base, href) if base else href
                domain = urlparse(abs_url).netloc.lower()
                if domain.startswith("www."):
                    domain = domain[4:]
                label = domain or "link"
            except Exception:
                label = "link"

        a.clear()
        a.append(soup.new_string(label))

    # Remove remaining standalone images
    for tag_name in ("img", "picture", "svg"):
        for node in soup.find_all(tag_name):
            if isinstance(node, Tag):
                node.decompose()

    return soup
