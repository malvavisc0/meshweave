"""Fetch fully-rendered HTML using CloakBrowser (Chromium)."""

import hashlib
import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from cloakbrowser import launch_context_async

__all__ = [
    "BrowserSession",
    "RenderMetrics",
    "get_rendered_html",
]

_INSTALL_HINT = "CloakBrowser binary not found. Install it with: cloakbrowser install"

logger = logging.getLogger(__name__)


def _ensure_binary() -> None:
    """Pre-flight check that the CloakBrowser binary is available."""
    from cloakbrowser import ensure_binary

    try:
        ensure_binary()
    except (SystemExit, Exception) as exc:
        raise RuntimeError(_INSTALL_HINT) from exc


@dataclass
class RenderMetrics:
    """Diagnostics for a rendering operation."""

    load_time: float = 0.0
    network_requests: int = 0
    response_status: int = 0
    final_url: str = ""
    content_length: int = 0
    errors: list[str] = field(default_factory=list)
    cache_hit: bool = False


def _cache_path(cache_dir: str, url: str, params: dict[str, Any]) -> Path:
    """Deterministic cache file path for a URL + params."""
    url_h = hashlib.md5(url.encode()).hexdigest()[:16]
    p_h = hashlib.md5(
        json.dumps(params, sort_keys=True, default=str).encode()
    ).hexdigest()[:12]
    return Path(cache_dir) / f"{url_h}_{p_h}.html"


async def _progressive_scroll(page, timeout_ms: int) -> None:
    """Scroll in steps to trigger lazy-loaded content."""
    await page.evaluate("""(async () => {
        const delay = ms => new Promise(r => setTimeout(r, ms));
        const h = document.body.scrollHeight;
        const vh = window.innerHeight;
        const steps = Math.min(5, Math.ceil(h / vh));
        for (let i = 0; i <= steps; i++) {
            window.scrollTo(0, (i / steps) * h);
            await delay(200);
        }
        window.scrollTo(0, 0);
        await delay(100);
    })();""")
    await page.wait_for_timeout(min(2000, timeout_ms // 4))


class BrowserSession:
    """Reusable CloakBrowser context for a crawl session.

    Usage::

        async with BrowserSession() as session:
            html, metrics = await get_rendered_html(
                "https://...", session=session
            )
            html2, m2 = await get_rendered_html(
                "https://...", session=session
            )
            # Same context, different pages -- TLS fingerprint preserved.
    """

    def __init__(self):
        self._ctx = None

    async def __aenter__(self):
        _ensure_binary()
        self._ctx = await launch_context_async()
        return self

    async def __aexit__(self, *exc):
        if self._ctx:
            await self._ctx.close()
            self._ctx = None

    @property
    def context(self):
        return self._ctx


async def get_rendered_html(
    url: str,
    *,
    session: BrowserSession,
    wait_until: Literal["load", "domcontentloaded", "networkidle"] = "networkidle",
    timeout: float = 30.0,
    progressive_scroll: bool = False,
    return_metrics: bool = False,
    cache_dir: str | None = None,
) -> str | tuple[str, RenderMetrics]:
    """Render a URL in Chromium and return the HTML.

    Returns html string, or (html, RenderMetrics) when
    return_metrics is True.
    """
    if not url or not url.startswith(("http://", "https://")):
        raise ValueError("url must be an http(s) URL")

    valid = {"load", "domcontentloaded", "networkidle"}
    if wait_until not in valid:
        raise ValueError(f"wait_until must be one of {valid}")

    metrics = RenderMetrics(final_url=url)
    timeout_ms = int(max(timeout, 0) * 1000)
    start = time.time()

    # Cache check
    c_params = {
        "wait_until": wait_until,
        "progressive_scroll": progressive_scroll,
        "timeout": timeout,
    }
    cp = _cache_path(cache_dir, url, c_params) if cache_dir else None
    if cp and cp.exists():
        try:
            html = cp.read_text(encoding="utf-8")
            metrics.cache_hit = True
            metrics.content_length = len(html)
            return (html, metrics) if return_metrics else html
        except Exception as e:
            metrics.errors.append(f"Cache read: {e}")

    ctx = session.context
    page = await ctx.new_page()
    try:
        # Block images to speed up rendering
        async def _block(route):
            if route.request.resource_type == "image":
                await route.abort()
            else:
                await route.continue_()

        await page.route("**/*", _block)

        req_count = 0

        def _count(req):
            nonlocal req_count
            req_count += 1

        page.on("request", _count)

        resp = await page.goto(
            url,
            wait_until=wait_until,
            timeout=timeout_ms,
        )
        if resp:
            metrics.response_status = resp.status
            metrics.final_url = resp.url

        if progressive_scroll:
            await _progressive_scroll(page, timeout_ms)

        html = await page.content()
        metrics.content_length = len(html)
        metrics.network_requests = req_count
        metrics.load_time = time.time() - start

        # Write cache
        if cache_dir and cp:
            try:
                Path(cache_dir).mkdir(parents=True, exist_ok=True)
                cp.write_text(html, encoding="utf-8")
            except Exception as e:
                metrics.errors.append(f"Cache write: {e}")

        return (html, metrics) if return_metrics else html
    except Exception as e:
        raise RuntimeError(f"Failed to render {url}: {e}") from e
    finally:
        await page.close()
