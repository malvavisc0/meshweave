"""Fetch fully-rendered HTML via a CDP browser (LightPanda)."""

import asyncio
import hashlib
import ipaddress
import json
import logging
import os
import socket
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, overload
from urllib.parse import urlsplit

from playwright.async_api import (
    Browser,
    BrowserContext,
    Playwright,
    async_playwright,
)

__all__ = [
    "BrowserSession",
    "RenderMetrics",
    "fetch_text",
    "get_rendered_html",
    "render_metrics_to_dict",
]


CDP_ENDPOINT_MISSING_MSG = (
    "MESHWEAVE_CDP_ENDPOINT is not set. MeshWeave renders pages "
    "through a remote CDP browser (e.g. LightPanda). Start one "
    "and set MESHWEAVE_CDP_ENDPOINT, e.g. "
    "http://localhost:9222 — see docker-compose.yaml "
    "(service 'lightpanda')."
)


def _cdp_endpoint() -> str | None:
    """Remote CDP endpoint to connect to, from ``MESHWEAVE_CDP_ENDPOINT``."""
    return os.environ.get("MESHWEAVE_CDP_ENDPOINT") or None


async def _resolve_cdp_ws_url(endpoint: str) -> str:
    """Resolve *endpoint* to a direct ``ws://`` URL Playwright can dial.

    LightPanda's ``/json/version`` advertises ``ws://127.0.0.1:9222/`` for a
    wildcard bind, which is unreachable from other containers, and its
    WebSocket handshake rejects non-IP-literal Host headers (403). Resolving
    the endpoint host to an IP literal and dialing ``ws://<ip>:<port>/``
    directly bypasses discovery and satisfies the Host check.
    """
    parts = urlsplit(endpoint)
    if parts.scheme in {"ws", "wss"}:
        ws_host = parts.hostname or ""
        try:
            ipaddress.ip_address(ws_host)
            return endpoint  # already an IP literal
        except ValueError:
            pass  # resolve below
    host = parts.hostname
    if not host:
        raise RuntimeError(f"Invalid MESHWEAVE_CDP_ENDPOINT: {endpoint!r}")
    port = parts.port or (443 if parts.scheme in {"https", "wss"} else 80)
    infos = await asyncio.get_running_loop().getaddrinfo(
        host, port, type=socket.SOCK_STREAM
    )
    # Prefer IPv4; bracket IPv6 literals for URL formatting.
    ip: str = str(infos[0][4][0])
    for family, _, _, _, sockaddr in infos:
        if family == socket.AF_INET:
            ip = str(sockaddr[0])
            break
    host_part = f"[{ip}]" if ":" in ip else ip
    scheme = "wss" if parts.scheme in {"https", "wss"} else "ws"
    return f"{scheme}://{host_part}:{port}/"


logger = logging.getLogger(__name__)


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


def render_metrics_to_dict(metrics: Any) -> dict[str, Any]:
    """Extract render metrics into a plain dict."""
    return {
        "final_url": str(getattr(metrics, "final_url", "")),
        "response_status": int(getattr(metrics, "response_status", 0)),
        "network_requests": int(getattr(metrics, "network_requests", 0)),
        "content_length": int(getattr(metrics, "content_length", 0)),
        "load_time_ms": round(
            float(getattr(metrics, "load_time", 0.0)) * 1000,
            2,
        ),
        "cache_hit": bool(getattr(metrics, "cache_hit", False)),
        "errors": list(getattr(metrics, "errors", [])),
    }


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
    """Reusable browser context for a crawl session, connected via CDP.

    Requires a remote CDP endpoint (e.g. the LightPanda container) from
    ``MESHWEAVE_CDP_ENDPOINT`` or the ``cdp_endpoint`` argument. Connects
    lazily on first use.

    Usage::

        async with BrowserSession() as session:
            html, metrics = await get_rendered_html(
                "https://...", session=session
            )
            html2, m2 = await get_rendered_html(
                "https://...", session=session
            )
            # Same context, different pages.
    """

    def __init__(self, cdp_endpoint: str | None = None) -> None:
        self._cdp_endpoint = cdp_endpoint or _cdp_endpoint()
        self._ctx: BrowserContext | None = None
        self._browser: Browser | None = None
        self._pw: Playwright | None = None
        self._connected = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        await self.disconnect()

    async def disconnect(self) -> None:
        if self._ctx:
            await self._ctx.close()
            self._ctx = None
        if self._browser:
            await self._browser.close()
            self._browser = None
        if self._pw:
            await self._pw.stop()
            self._pw = None
        self._connected = False

    async def ensure_connected(self) -> None:
        """Connect to the CDP endpoint on first use (idempotent).

        Raises:
            RuntimeError: If no CDP endpoint is configured.
        """
        if self._connected:
            return
        endpoint = self._cdp_endpoint
        if not endpoint:
            raise RuntimeError(CDP_ENDPOINT_MISSING_MSG)
        try:
            ws_url = await _resolve_cdp_ws_url(endpoint)
            self._pw = await async_playwright().start()
            self._browser = await self._pw.chromium.connect_over_cdp(ws_url)
            self._ctx = await self._browser.new_context()
        except Exception:
            # Don't leak a started Playwright driver / partial connection.
            await self.disconnect()
            raise
        self._connected = True

    @property
    def context(self) -> BrowserContext:
        if not self._connected:
            raise RuntimeError(
                "BrowserSession not connected; call ensure_connected() first."
            )
        ctx = self._ctx
        if ctx is None:
            raise RuntimeError(
                "BrowserSession not connected; call ensure_connected() first."
            )
        return ctx


@overload
async def get_rendered_html(
    url: str,
    *,
    session: BrowserSession,
    wait_until: Literal["load", "domcontentloaded", "networkidle"] = "networkidle",
    timeout: float = 30.0,
    progressive_scroll: bool = False,
    return_metrics: Literal[False] = False,
    cache_dir: str | None = None,
) -> str: ...


@overload
async def get_rendered_html(
    url: str,
    *,
    session: BrowserSession,
    wait_until: Literal["load", "domcontentloaded", "networkidle"] = "networkidle",
    timeout: float = 30.0,
    progressive_scroll: bool = False,
    return_metrics: Literal[True],
    cache_dir: str | None = None,
) -> tuple[str, RenderMetrics]: ...


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
    """Render a URL via a CDP browser and return the HTML.

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

    await session.ensure_connected()
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


# HTML wrapper tags that headless browsers wrap around plain-text/XML content.
_BROWSER_STRIP_TAGS = (
    "<html>",
    "</html>",
    "<head>",
    "</head>",
    "<body>",
    "</body>",
    "<pre>",
    "</pre>",
)


async def fetch_text(
    url: str,
    *,
    session: BrowserSession,
    timeout: float = 10.0,
) -> str | None:
    """Fetch a URL via the browser session and return body text, or None.

    Plain-text/XML responses are wrapped in minimal HTML tags
    by the browser, which are stripped before returning.
    """
    try:
        html = await get_rendered_html(
            url=url,
            session=session,
            progressive_scroll=False,
            return_metrics=False,
            timeout=timeout,
            wait_until="domcontentloaded",
        )
        text = html
        for tag in _BROWSER_STRIP_TAGS:
            text = text.replace(tag, "")
        return text.strip()
    except Exception as exc:
        logger.debug("Failed to fetch %s: %s", url, exc)
    return None
