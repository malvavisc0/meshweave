"""Fetch and analyse robots.txt and llms.txt for AI bot accessibility."""

from __future__ import annotations

import logging
import re
from typing import Any

from ..crawling.fetcher import BrowserSession, fetch_text

_HTML_RE = re.compile(
    r"^\s*(<!doctype\s+html|<html[\s>]|<head[\s>]" r"|<body[\s>]|<p[\s>])",
    re.IGNORECASE | re.DOTALL,
)


def _is_valid_llms_content(text: str) -> bool:
    """Return *True* if *text* looks like a real llms.txt.

    Servers that don't host an llms.txt file often return a generic
    HTML error page with HTTP 200.  This heuristic detects those
    responses so they are not mistakenly reported as valid llms.txt
    files.
    """
    return not _HTML_RE.match(text)


__all__ = [
    "fetch_robots_info",
    "check_llms_txt",
]

logger = logging.getLogger(__name__)

_AI_BOTS = (
    "GPTBot",
    "ChatGPT-User",
    "ClaudeBot",
    "Claude-Web",
    "PerplexityBot",
    "Googlebot",
    "Google-Extended",
    "Bingbot",
    "anthropic-ai",
    "cohere-ai",
    "Bytespider",
)


def _parse_robots(
    text: str,
    bots: tuple[str, ...] = _AI_BOTS,
) -> dict[str, str]:
    """Return per-bot allow/block status from robots.txt content.

    A bot is "allowed" unless its section contains
    ``Disallow: /`` (full-site block).  More granular path rules
    are reported as "partially_restricted".
    """
    sections: dict[str, list[str]] = {}
    current_agents: list[str] = []
    seen_directive = False
    for raw_line in text.splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line:
            current_agents = []
            seen_directive = False
            continue
        if line.lower().startswith("user-agent:"):
            # Consecutive User-agent lines share one group; a
            # User-agent line after directives starts a new group even
            # without a blank line separator.
            if seen_directive:
                current_agents = []
                seen_directive = False
            agent = line.split(":", 1)[1].strip().lower()
            current_agents.append(agent)
            sections.setdefault(agent, [])
        elif current_agents:
            seen_directive = True
            for a in current_agents:
                sections.setdefault(a, []).append(line.lower())

    def _status(agent_name: str) -> str:
        key = agent_name.lower()
        directives = sections.get(key, sections.get("*", []))
        has_full_block = any(d.strip() == "disallow: /" for d in directives)
        has_partial = any(
            d.startswith("disallow:")
            and d.strip() != "disallow: /"
            and d.strip() != "disallow:"
            for d in directives
        )
        if has_full_block:
            return "blocked"
        if has_partial:
            return "partially_restricted"
        return "allowed"

    return {bot: _status(bot) for bot in bots}


async def fetch_robots_info(
    base_url: str,
    *,
    session: BrowserSession,
    timeout: float = 10.0,
) -> dict[str, Any]:
    """Fetch ``/robots.txt`` and return structured analysis."""
    robots_url = base_url.rstrip("/") + "/robots.txt"
    result: dict[str, Any] = {
        "url": robots_url,
        "exists": False,
        "bots": {},
        "sitemaps": [],
        "size_bytes": 0,
    }
    body = await fetch_text(robots_url, session=session, timeout=timeout)
    if body:
        result["exists"] = True
        result["size_bytes"] = len(body.encode())
        result["bots"] = _parse_robots(body)
        result["sitemaps"] = [
            line.split(":", 1)[1].strip()
            for line in body.splitlines()
            if line.strip().lower().startswith("sitemap:")
        ]
    return result


async def check_llms_txt(
    base_url: str,
    *,
    session: BrowserSession,
    timeout: float = 10.0,
    preview_chars: int = 500,
) -> dict[str, Any]:
    """Check for ``llms.txt`` and ``llms-full.txt``."""
    base = base_url.rstrip("/")
    result: dict[str, Any] = {
        "llms_txt": {
            "exists": False,
            "url": None,
            "size_bytes": 0,
            "content_preview": "",
        },
        "llms_full_txt": {
            "exists": False,
            "url": None,
            "size_bytes": 0,
            "content_preview": "",
        },
    }

    for key, paths in (
        ("llms_txt", ("/.well-known/llms.txt", "/llms.txt")),
        (
            "llms_full_txt",
            (
                "/.well-known/llms-full.txt",
                "/llms-full.txt",
            ),
        ),
    ):
        for path in paths:
            url = base + path
            body = await fetch_text(url, session=session, timeout=timeout)
            if body and _is_valid_llms_content(body):
                result[key] = {
                    "exists": True,
                    "url": url,
                    "size_bytes": len(body.encode()),
                    "content_preview": body[:preview_chars],
                }
                break

    return result
