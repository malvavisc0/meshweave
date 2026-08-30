"""Detection of bot-protection interstitials served instead of the site.

When a site (or its edge provider) blocks the crawler, the renderer
receives a challenge page — a Vercel Security Checkpoint, a Cloudflare
"Just a moment…" interstitial, an HTTP 403/429/503 response — rather
than the actual site. Nothing in the render pipeline raises on that:
the challenge HTML is extracted, scored, and persisted as a successful
crawl, and the report then judges the interstitial as if it were the
site (AEO 0, "no schema", "no content" — all describing the blocker).

This module centralizes three layers of defense:

- ``start_page_blocked_reason`` / ``CrawlBlockedError``: used by
  ``core.crawl`` right after rendering the start page, so a blocked
  site aborts before sitemap discovery and the multi-page BFS burn
  the crawl budget on challenged URLs.
- ``find_blocked_pages``: used after the BFS to strip challenge-shaped
  sub-pages from the crawl payload, so a partial block (start page
  fine, sub-pages challenged) does not pollute content-based scoring
  as artificially "thin" pages.
- ``blocked_render_reason``: post-hoc detection over a finished
  payload, used by the webapp crawl services as defense in depth.
"""

from __future__ import annotations

from typing import Any

# Main-document statuses that mean "the site refused to serve us".
# The renderer still returns HTML for these (the challenge markup), so
# the pipeline cannot rely on an exception to catch them.
_BLOCKED_STATUSES: frozenset[int] = frozenset({401, 403, 429, 503})

# Title fragments of common bot-protection interstitials. Matched
# case-insensitively against the rendered page's <title> — challenge
# pages sometimes arrive with HTTP 200, so the status alone is not
# enough (and vice versa: a 429 from a rate limiter serves a plain
# error page, not a recognizable title).
_CHALLENGE_TITLE_FRAGMENTS: tuple[str, ...] = (
    "vercel security checkpoint",
    "just a moment",
    "attention required",
    "checking your browser",
    "verify you are human",
    "verifying you are human",
    "security challenge",
    "ddos protection",
    "access denied",
    "robot check",
    "request blocked",
    "unusual traffic",
    "captcha",
    "enable javascript and cookies",
)

# Words of markdown a page must carry to count as real content. Matches
# the AAX precondition threshold for "meaningful text" (50 words).
_SUBSTANTIVE_WORD_COUNT = 50


class CrawlBlockedError(Exception):
    """The crawl's start page was a bot-protection interstitial.

    Raised by ``core.crawl`` before any multi-page crawling happens, so
    the caller can fail fast (and cheaply) with an honest error instead
    of burning the crawl budget on challenged pages and scoring the
    blocker as if it were the site.
    """


def blocked_error(reason: str) -> str:
    """Format the user-facing error message for a blocked crawl."""
    return f"Crawl blocked: {reason}. Retry later or from a different network."


def start_page_blocked_reason(
    *,
    status: int,
    title: str,
    markdown: str,
) -> str | None:
    """Return the block reason for a freshly rendered start page.

    Args:
        status: HTTP status of the main document response.
        title: The rendered page's <title>.
        markdown: The extracted markdown of the rendered page.

    Returns:
        A human-readable reason when the start page is a bot-protection
        interstitial or a refusal status, else None.

    A refusal status is decisive regardless of the body. A challenge
    title alone is not — a legitimate site can mention "captcha" in its
    own title — so a title match also requires that the extracted
    markdown is thin, which challenge pages always are.
    """
    if status in _BLOCKED_STATUSES:
        detail = f" — '{title}'" if title else ""
        return f"site refused the crawl request (HTTP {status}{detail})"

    if _matches_challenge_title(title) and _is_thin(markdown):
        return f"bot-protection interstitial served instead of the site ('{title}')"
    return None


def find_blocked_pages(markdowns: dict[str, Any]) -> list[str]:
    """URLs of crawled pages that are bot-protection interstitials.

    A page counts when its title matches a challenge fragment AND its
    extracted markdown is thin. Real content pages that merely mention
    challenge keywords in their titles carry substantive markdown and
    are kept.
    """
    blocked: list[str] = []
    for url, data in markdowns.items():
        if not isinstance(data, dict):
            continue
        page = data.get("page") or {}
        title = str(page.get("title") or "").strip()
        markdown = data.get("markdown") or ""
        if _matches_challenge_title(title) and _is_thin(markdown):
            blocked.append(url)
    return blocked


def blocked_render_reason(payload: dict[str, Any]) -> str | None:
    """Return why the crawl was blocked, or None when it was not.

    Args:
        payload: A crawl result payload (JSON-parsed), as produced by
            ``meshweave.core.crawl``.

    Returns:
        A human-readable reason string when the rendered start page
        was a bot-protection interstitial or a refusal status, else
        None. Callers should treat a returned reason as "the site was
        never actually read" — not score the payload.

    Detection rules, in order:

    - A refusal status on the main document (401/403/429/503) is
      decisive regardless of the body: the site refused the request.
    - A challenge-page title alone is NOT decisive — a legitimate site
      can mention "captcha" or "access denied" in its own title (e.g.
      a captcha-solving service). A title match only counts when the
      crawl also extracted no substantive content anywhere: challenge
      pages carry almost no text, so "title matches AND every crawled
      page is thin" is a block, while "title matches AND real content
      exists" is a normal site.
    """
    if not isinstance(payload, dict):
        return None
    render = (payload.get("metrics") or {}).get("render") or {}
    try:
        status = int(render.get("response_status") or 0)
    except TypeError, ValueError:
        status = 0

    title = _page_title(payload)

    if status in _BLOCKED_STATUSES:
        detail = f" — '{title}'" if title else ""
        return f"site refused the crawl request (HTTP {status}{detail})"

    if _matches_challenge_title(title) and not _has_substantive_content(payload):
        return f"bot-protection interstitial served instead of the site ('{title}')"
    return None


def _matches_challenge_title(title: str) -> bool:
    """True when the title contains a challenge-page fragment."""
    return any(f in title.lower() for f in _CHALLENGE_TITLE_FRAGMENTS)


def _is_thin(markdown: str) -> bool:
    """True when a page's markdown carries less than substantive text."""
    return len((markdown or "").split()) < _SUBSTANTIVE_WORD_COUNT


def _has_substantive_content(payload: dict[str, Any]) -> bool:
    """True when any crawled page yielded meaningful markdown text.

    A full block produces thin or absent markdown on every page; a real
    site whose title merely resembles a challenge page still extracts
    substantial content from its homepage.
    """
    markdowns = payload.get("markdowns") or {}
    for data in markdowns.values():
        if not isinstance(data, dict):
            continue
        md = data.get("markdown") or ""
        if not _is_thin(md):
            return True
    return False


def _page_title(payload: dict[str, Any]) -> str:
    """The rendered start page's <title>, blank when absent."""
    page = payload.get("page") or {}
    title = page.get("title")
    return str(title).strip() if isinstance(title, str) else ""
