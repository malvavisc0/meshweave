"""Utilities for fetching fully-rendered HTML using a real browser engine.

Primary function:
    get_rendered_html(url: str, ...) -> str

This uses Playwright (Chromium) under the hood to load the page with JavaScript
enabled, wait for network activity to settle, and then returns the page's
current HTML (including any DOM mutations done by client-side scripts).

Installation (if not already available):
    pip install playwright
    playwright install --with-deps chromium

Notes:
- Uses safe defaults for containerized environments (no-sandbox flags).
- Provides options to tune wait behavior when pages are highly dynamic.
- Returns the HTML as rendered at the time of capture (page.content()).
- Includes anti-detection features, retry mechanisms, and performance monitoring.
"""

import hashlib
import inspect
import json
import logging
import os
import random
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Literal, Optional, Tuple, Union

# Import Playwright at module level for better error handling
try:
    from playwright.async_api import TimeoutError as PlaywrightTimeoutError
    from playwright.async_api import async_playwright
except ImportError:
    raise ImportError(
        "Playwright is required. Install with: pip install playwright && playwright install --with-deps chromium"
    )


@dataclass
class RenderMetrics:
    """Performance and diagnostic metrics for HTML rendering.

    Attributes:
        load_time: Total seconds from browser launch to HTML capture.
        network_requests: Number of network requests observed on the page.
        response_status: HTTP status code of the main navigation response (0 if unavailable).
        final_url: Final URL after any redirects.
        content_length: Length in characters of the captured HTML.
        errors: A list of non-fatal error messages encountered during rendering.
        retries_used: Number of retries used (currently always 0; reserved for future logic).
        screenshot_path: Absolute or relative path to the saved screenshot if capture_screenshot=True.
        cache_hit: True if the HTML was served from cache_dir instead of a fresh render.
    """

    load_time: float
    network_requests: int
    response_status: int
    final_url: str
    content_length: int
    errors: List[str]
    retries_used: int
    screenshot_path: Optional[str] = None
    cache_hit: bool = False


# Modern User-Agent strings for different devices
_USER_AGENTS = {
    "desktop_chrome": [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    ],
    "desktop_firefox": [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:122.0) Gecko/20100101 Firefox/122.0",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:122.0) Gecko/20100101 Firefox/122.0",
        "Mozilla/5.0 (X11; Linux x86_64; rv:122.0) Gecko/20100101 Firefox/122.0",
    ],
    "mobile_chrome": [
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_2 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) CriOS/131.0.0.0 Mobile/15E148 Safari/604.1",
        "Mozilla/5.0 (Linux; Android 14; SM-G991B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Mobile Safari/537.36",
    ],
    "mobile_safari": [
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_2 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Mobile/15E148 Safari/604.1",
        "Mozilla/5.0 (iPad; CPU OS 17_2 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Mobile/15E148 Safari/604.1",
    ],
}

_DEFAULT_USER_AGENT = _USER_AGENTS["desktop_chrome"][0]

# Common screen resolutions for stealth mode
_VIEWPORT_SIZES = {
    "desktop": [
        (1920, 1080),
        (1366, 768),
        (1440, 900),
        (1536, 864),
        (1280, 720),
    ],
    "mobile": [(375, 667), (414, 896), (390, 844), (360, 640), (412, 915)],
}

# Default resource types to block for faster loading
_DEFAULT_BLOCKED_RESOURCES = ["image"]


def _get_cache_path(cache_dir: str, url: str, params_hash: str) -> Path:
    """Generate the cache file path for a URL and rendering parameters.

    Parameters:
        cache_dir: Directory on disk used to store cached HTML files.
        url: The canonical URL being rendered.
        params_hash: Stable hash derived from the subset of parameters that affect rendering.

    Returns:
        Path object pointing to the HTML cache file within cache_dir.
    """
    url_hash = hashlib.md5(url.encode()).hexdigest()[:16]
    filename = f"{url_hash}_{params_hash}.html"
    return Path(cache_dir) / filename


def _get_params_hash(params: Dict[str, Any]) -> str:
    """Generate a short, stable hash for the provided parameter dictionary.

    Parameters:
        params: Dictionary of parameters that influence the rendering output.

    Returns:
        12-character hexadecimal MD5 digest string used as part of the cache key.
    """
    # Sort params to ensure consistent hashing
    param_str = json.dumps(params, sort_keys=True, default=str)
    return hashlib.md5(param_str.encode()).hexdigest()[:12]


def _select_user_agent(
    device_type: str, custom_ua: Optional[str], use_default: bool = False
) -> str:
    """Select a User-Agent string based on device type or explicit override.

    Parameters:
        device_type: One of "desktop_chrome", "desktop_firefox", "mobile_chrome", "mobile_safari".
        custom_ua: Explicit UA string. If provided, it is returned verbatim.
        use_default: When True, return the module default UA regardless of device_type.

    Returns:
        A User-Agent string to use when creating the browser context.
    """
    if custom_ua:
        return custom_ua

    # Use the default user agent if explicitly requested or device_type is default
    if use_default or device_type == "desktop_chrome":
        return _DEFAULT_USER_AGENT

    if device_type not in _USER_AGENTS:
        return _DEFAULT_USER_AGENT

    return random.choice(_USER_AGENTS[device_type])


def _select_viewport(
    device_type: str, custom_viewport: Optional[Tuple[int, int]]
) -> Tuple[int, int]:
    """Select an appropriate viewport size.

    Parameters:
        device_type: Device type used to choose from desktop or mobile presets.
        custom_viewport: Optional (width, height) override in pixels.

    Returns:
        Tuple of (width, height) in pixels.
    """
    if custom_viewport:
        return custom_viewport

    category = "mobile" if "mobile" in device_type else "desktop"
    return random.choice(_VIEWPORT_SIZES[category])


async def _apply_stealth_measures(page, stealth_mode: bool) -> None:
    """Apply simple anti-automation measures to reduce bot detection.

    Parameters:
        page: Playwright Page instance.
        stealth_mode: If False, do nothing; when True, injects small JS shims.

    Returns:
        None. Modifies the page environment in-place.
    """
    if not stealth_mode:
        return

    # Override navigator properties to appear more human-like
    # Add hardware concurrency and other properties for better stealth
    stealth_js = """
    // Hide webdriver
    Object.defineProperty(navigator, 'webdriver', {
        get: () => undefined,
    });
    
    // Fake plugins
    Object.defineProperty(navigator, 'plugins', {
        get: () => [1, 2, 3, 4, 5],
    });
    
    // Languages
    Object.defineProperty(navigator, 'languages', {
        get: () => ['en-US', 'en'],
    });
    
    // Fake chrome runtime
    window.chrome = {
        runtime: {},
    };
    
    // Fake permissions
    Object.defineProperty(navigator, 'permissions', {
        get: () => ({
            query: () => Promise.resolve({ state: 'granted' }),
        }),
    });

    // Add hardware concurrency for realism
    Object.defineProperty(navigator, 'hardwareConcurrency', {
        get: () => 8,
    });
    """

    try:
        await page.add_init_script(stealth_js)
    except Exception:
        # Log or handle init script error, but don't crash
        pass


async def _progressive_scroll(page, timeout_ms: int) -> None:
    """Progressively scroll through the page to trigger lazy loading.

    Parameters:
        page: Playwright Page instance.
        timeout_ms: Maximum time budget in milliseconds used to bound waits.

    Returns:
        None. Scrolls and waits to encourage content to load.
    """
    await page.evaluate(
        """
    (async () => {
        const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
        const scrollHeight = document.body.scrollHeight;
        const viewportHeight = window.innerHeight;
        const steps = Math.min(5, Math.ceil(scrollHeight / viewportHeight)); // Limit steps to 5
        
        for (let i = 0; i <= steps; i++) {
            const scrollTo = (i / steps) * scrollHeight;
            window.scrollTo(0, scrollTo);
            await delay(200); // Reduced wait time
        }
        
        // Scroll back to top
        window.scrollTo(0, 0);
        await delay(100);
    })();
    """
    )

    # Reduced wait time to prevent timeouts
    await page.wait_for_timeout(min(2000, timeout_ms // 4))


async def get_rendered_html(
    url: str,
    *,
    wait_until: (
        Literal["commit", "domcontentloaded", "load", "networkidle"] | None
    ) = "domcontentloaded",
    timeout: float = 30.0,
    render_wait: float = 0.0,
    user_agent: Optional[str] = None,
    device_type: str = "desktop_chrome",
    viewport: Optional[Tuple[int, int]] = None,
    extra_headers: Optional[Dict[str, str]] = None,
    wait_for_selector: Optional[str] = None,
    referer: Optional[str] = None,
    ignore_https_errors: bool = False,
    block_resources: Optional[List[str]] = None,
    scroll_for_lazy_load: bool = False,
    progressive_scroll: bool = False,
    stealth_mode: bool = True,
    execute_js: Optional[Union[str, List[str]]] = None,
    capture_screenshot: bool = False,
    screenshot_path: Optional[str] = None,
    return_metrics: bool = False,
    validate_content: Optional[Callable[[str], bool]] = None,
    cache_dir: Optional[str] = None,
    intercept_requests: Optional[Callable[[Any], None]] = None,
) -> Union[str, Tuple[str, RenderMetrics]]:
    """Render a URL in a real browser (Playwright/Chromium) and return the HTML.

    Parameters:
        url: HTTP(S) URL to navigate to.
        wait_until: Navigation readiness state. One of {"load", "domcontentloaded", "networkidle"}.
        timeout: Maximum time in seconds for navigation and waits.
        render_wait: Extra time in seconds to wait after the page signals ready.
        user_agent: Explicit User-Agent string to use. If None, derived from device_type.
        device_type: Preset that influences default UA and viewport: "desktop_chrome",
            "desktop_firefox", "mobile_chrome", "mobile_safari".
        viewport: Optional (width, height) pixels. If None, a preset is chosen by device_type.
        extra_headers: Additional HTTP request headers for the context.
        wait_for_selector: If provided, waits for the CSS selector to appear before capture.
        referer: Optional HTTP Referer header for initial navigation.
        ignore_https_errors: Continue navigation even if HTTPS errors occur.
        block_resources: Resource types to block for speed, e.g. ["image","stylesheet","font","media"].
            If None, defaults to ["image"].
        scroll_for_lazy_load: If True, scrolls once to the bottom to trigger lazy loading.
        progressive_scroll: If True, scrolls in steps to trigger more thorough lazy loading.
        stealth_mode: If True, applies small JS shims and launch flags to reduce automation fingerprints.
        execute_js: JavaScript string or list of strings to execute after load and before capture.
        capture_screenshot: If True, writes a full-page screenshot to screenshot_path.
        screenshot_path: Optional path for the screenshot. If None and capture_screenshot=True,
            a path is generated automatically.
        return_metrics: If True, returns a (html, RenderMetrics) tuple; otherwise returns only html.
        validate_content: Optional function to validate captured content (not enforced internally).
        cache_dir: If provided, the HTML is cached/read under this directory keyed by url and params.
        intercept_requests: Optional Playwright route handler to inspect/modify network requests.

    Returns:
        If return_metrics is False: the rendered HTML string.
        If return_metrics is True: a tuple of (html, RenderMetrics).

    Raises:
        ValueError: If url is empty/invalid or wait_until is unsupported.
        TimeoutError: If navigation/selector waits exceed timeout.
        RuntimeError: For other rendering failures.
        ImportError: If Playwright is not installed.

    Example:
        html, metrics = await get_rendered_html(
            "https://example.com",
            device_type="mobile_chrome",
            progressive_scroll=True,
            stealth_mode=True,
            execute_js="document.querySelector('.load-more')?.click()",
            return_metrics=True,
        )
    """
    if not isinstance(url, str) or not url.strip():
        raise ValueError("url must be a non-empty string")

    if not (url.startswith("http://") or url.startswith("https://")):
        raise ValueError("url must start with http:// or https://")

    if wait_until not in {"load", "domcontentloaded", "networkidle"}:
        raise ValueError(
            'wait_until must be one of {"load", "domcontentloaded", "networkidle"}'
        )

    # Initialize metrics
    metrics = RenderMetrics(
        load_time=0.0,
        network_requests=0,
        response_status=0,
        final_url=url,
        content_length=0,
        errors=[],
        retries_used=0,
    )

    # Setup parameters
    timeout_ms = int(max(timeout, 0) * 1000)
    extra_wait_ms = int(max(render_wait, 0) * 1000)
    # Use default UA when no custom device_type is specified and no custom user_agent
    use_default_ua = device_type == "desktop_chrome" and user_agent is None
    ua = _select_user_agent(device_type, user_agent, use_default_ua)

    if block_resources is None:
        block_resources = _DEFAULT_BLOCKED_RESOURCES

    start_time = time.time()
    logger = logging.getLogger(__name__)
    logger.info(f"Starting HTML rendering for URL: {url}")

    # Resolve effective viewport for consistency in cache keys and rendering
    vp = _select_viewport(device_type, viewport)

    # Check cache first (skip when a custom intercept_requests handler is provided)
    cache_hit = False
    cache_path = None
    if cache_dir and not intercept_requests:
        cache_params = {
            "wait_until": wait_until,
            "device_type": device_type,
            "viewport": vp,  # effective viewport used
            "user_agent": ua,  # resolved UA used
            "execute_js": execute_js,
            "progressive_scroll": progressive_scroll,
            "scroll_for_lazy_load": scroll_for_lazy_load,
            "timeout": timeout,
            "render_wait": render_wait,
            "extra_headers": extra_headers or {},
            "referer": referer,
            "ignore_https_errors": ignore_https_errors,
            "block_resources": (
                block_resources
                if block_resources is not None
                else _DEFAULT_BLOCKED_RESOURCES
            ),
            "stealth_mode": stealth_mode,
            "capture_screenshot": bool(capture_screenshot),
            "intercept_requests": False,
        }
        params_hash = _get_params_hash(cache_params)
        cache_path = _get_cache_path(cache_dir, url, params_hash)

        if cache_path.exists():
            try:
                html = cache_path.read_text(encoding="utf-8")
                metrics.cache_hit = True
                metrics.content_length = len(html)
                if return_metrics:
                    return html, metrics
                return html
            except Exception as e:
                metrics.errors.append(f"Cache read error: {e}")

    try:
        async with async_playwright() as p:
            logger.info("Playwright launched successfully")
            browser = None
            context = None
            try:
                # Launch Chromium with enhanced stealth flags
                browser_args = [
                    "--disable-gpu",
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-web-security",
                ]

                if stealth_mode:
                    browser_args.extend(
                        [
                            "--disable-blink-features=AutomationControlled",
                            "--exclude-switches=enable-automation",
                            "--disable-extensions",
                            "--no-first-run",
                            "--disable-default-apps",
                        ]
                    )

                browser = await p.chromium.launch(
                    headless=True,
                    args=browser_args,
                    slow_mo=int(os.getenv("MARKDOWNIFY_DEBUG_SLOWMO_MS", "0") or "0"),
                    devtools=False,  # Set to True for debugging if needed
                )
                logger.info("Browser launched successfully")

                context = await browser.new_context(
                    user_agent=ua,
                    viewport={"width": vp[0], "height": vp[1]},
                    java_script_enabled=True,
                    extra_http_headers=extra_headers or {},
                    ignore_https_errors=ignore_https_errors,
                    locale="en-US",
                    timezone_id="UTC",
                )
                logger.info("Browser context created successfully")

                page = await context.new_page()
                logger.info("New page created successfully")

                # Apply stealth measures
                await _apply_stealth_measures(page, stealth_mode)

                # Unified network routing: resource blocking and optional custom interception
                blocked_types = set(block_resources or [])

                async def _route_handler(route):
                    try:
                        req = route.request
                        if blocked_types and req.resource_type in blocked_types:
                            await route.abort()
                            return
                        if intercept_requests:
                            # Delegate to user-supplied handler (supports sync or async; 1 or 2 params)
                            try:
                                sig = None
                                try:
                                    sig = inspect.signature(intercept_requests)  # type: ignore[arg-type]
                                except Exception:
                                    sig = None
                                if sig and len(sig.parameters) >= 2:
                                    res = intercept_requests(route, req)  # type: ignore[misc]
                                else:
                                    res = intercept_requests(route)  # type: ignore[misc]
                                if inspect.isawaitable(res):
                                    await res  # type: ignore[func-returns-value]
                                return
                            except Exception as e:
                                metrics.errors.append(f"Intercept error: {e}")
                                # fall through to continue request
                        await route.continue_()
                    except Exception:
                        try:
                            await route.continue_()
                        except Exception:
                            pass

                await page.route("**/*", _route_handler)

                # Track network requests
                request_count = 0

                def count_requests(request):
                    nonlocal request_count
                    request_count += 1

                page.on("request", count_requests)

                # Navigate to page
                try:
                    response = await page.goto(
                        url,
                        wait_until=wait_until,
                        timeout=timeout_ms,
                        referer=referer,
                    )

                    if response:
                        metrics.response_status = response.status
                        metrics.final_url = response.url
                except PlaywrightTimeoutError as e:
                    metrics.errors.append(f"Navigation timeout: {e}")
                    raise TimeoutError(
                        f"Navigation to {url} timed out after {timeout}s"
                    ) from e

                # Wait for specific selector if requested
                if wait_for_selector:
                    try:
                        await page.wait_for_selector(
                            wait_for_selector,
                            timeout=timeout_ms,
                            state="attached",
                        )
                    except PlaywrightTimeoutError as e:
                        metrics.errors.append(f"Selector wait timeout: {e}")
                        raise TimeoutError(
                            f'Waiting for selector "{wait_for_selector}" timed out after {timeout}s'
                        ) from e

                # Progressive scrolling for lazy loading
                if progressive_scroll:
                    await _progressive_scroll(page, timeout_ms)
                elif scroll_for_lazy_load:
                    await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                    await page.wait_for_timeout(500)

                # Execute custom JavaScript
                if execute_js:
                    js_list = [execute_js] if isinstance(execute_js, str) else execute_js
                    for js_code in js_list:
                        try:
                            await page.evaluate(js_code)
                            await page.wait_for_timeout(
                                200
                            )  # Reduced wait for JS effects
                        except Exception as e:
                            metrics.errors.append(f"JavaScript execution error: {e}")

                # Additional wait time
                if extra_wait_ms > 0:
                    await page.wait_for_timeout(extra_wait_ms)

                # Capture screenshot if requested
                if capture_screenshot:
                    if not screenshot_path:
                        timestamp = int(time.time())
                        url_hash = hashlib.md5(url.encode()).hexdigest()[:8]
                        screenshot_path = f"screenshot_{url_hash}_{timestamp}.png"

                    try:
                        await page.screenshot(path=screenshot_path, full_page=True)
                        metrics.screenshot_path = screenshot_path
                    except Exception as e:
                        metrics.errors.append(f"Screenshot capture error: {e}")

                # Capture the fully rendered HTML
                logger.info("Capturing rendered HTML content")
                html = await page.content()
                metrics.content_length = len(html)
                metrics.network_requests = request_count
                metrics.retries_used = 0
                logger.info(
                    f"HTML captured successfully. Content length: {len(html)} characters, Network requests: {request_count}"
                )

                # Cache the result
                if cache_dir and cache_path and not cache_hit and not intercept_requests:
                    try:
                        Path(cache_dir).mkdir(parents=True, exist_ok=True)
                        cache_path.write_text(html, encoding="utf-8")
                    except Exception as e:
                        metrics.errors.append(f"Cache write error: {e}")

                metrics.load_time = time.time() - start_time
                logger.info(
                    f"HTML rendering completed successfully in {metrics.load_time:.2f}s"
                )

                if return_metrics:
                    return html, metrics
                return html

            finally:
                # Cleanup resources
                try:
                    if context:
                        await context.close()
                except Exception:
                    pass
                try:
                    if browser:
                        await browser.close()
                except Exception:
                    pass
    except Exception as e:
        logger.error(f"Failed to render HTML for {url}: {e}", exc_info=True)
        raise RuntimeError(f"Failed to render HTML for {url}: {e}") from e
