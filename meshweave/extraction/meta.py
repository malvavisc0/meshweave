"""HTML metadata extraction (title, description, OG tags, canonical)."""

from __future__ import annotations

import json
from typing import Any

from bs4 import BeautifulSoup
from bs4.element import Tag

__all__ = [
    "extract_page_meta",
]


def _meta(soup: BeautifulSoup, attr: str, value: str) -> str:
    """Return the *content* of the first ``<meta>`` matching *attr*=*value*."""
    tag = soup.find("meta", attrs={attr: value})
    if isinstance(tag, Tag) and tag.has_attr("content"):
        return str(tag.get("content", ""))
    return ""


def _meta_all(soup: BeautifulSoup, attr: str, value: str) -> list[str]:
    """Return *content* values of **all** ``<meta>`` tags matching."""
    return [
        str(t.get("content", ""))
        for t in soup.find_all("meta", attrs={attr: value})
        if isinstance(t, Tag) and t.has_attr("content")
    ]


def _extract_jsonld(soup: BeautifulSoup) -> list[dict[str, Any]]:
    """Extract JSON-LD structured data from ``<script>`` tags."""
    results: list[dict[str, Any]] = []
    ld_json_scripts = soup.find_all("script", attrs={"type": "application/ld+json"})
    for script in ld_json_scripts:
        if not isinstance(script, Tag):
            continue
        text = script.string or ""
        text = text.strip()
        if not text:
            continue
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            continue
        except TypeError:
            continue
        if isinstance(data, list):
            results.extend(d for d in data if isinstance(d, dict))
        elif isinstance(data, dict):
            results.append(data)
    return results


def extract_page_meta(soup: BeautifulSoup) -> dict[str, Any]:
    """Extract title, description, OG tags, canonical, and structured data.

    Returns a dict with keys: ``title``, ``description``, ``og``,
    ``twitter``, ``canonical``, and ``jsonld``.
    """
    meta: dict[str, Any] = {
        "title": (soup.title.get_text(strip=True) if soup.title else ""),
        "description": _meta(soup, "name", "description"),
        "og": {
            "title": _meta(soup, "property", "og:title"),
            "description": _meta(soup, "property", "og:description"),
            "image": _meta(soup, "property", "og:image"),
            "url": _meta(soup, "property", "og:url"),
        },
        "twitter": {
            "card": _meta(soup, "name", "twitter:card"),
            "title": _meta(soup, "name", "twitter:title"),
            "description": _meta(soup, "name", "twitter:description"),
            "image": _meta(soup, "name", "twitter:image"),
        },
        "canonical": "",
        "jsonld": _extract_jsonld(soup),
    }
    link = soup.find("link", attrs={"rel": "canonical"})
    if isinstance(link, Tag):
        href = link.get("href")
        if href:
            meta["canonical"] = str(href)
    return meta
