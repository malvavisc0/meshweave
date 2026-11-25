"""Screenshot and interactive-element geometry from a real browser.

The text crawl runs through LightPanda, which renders no pixels and has no
layout engine. Visual diagnostics need both, so this module talks to a
separate Chromium CDP endpoint (compose service ``chromium``) with images
enabled and returns what a human or a screenshot-driven agent actually sees.
"""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

from playwright.async_api import Browser, async_playwright

from .cdp import resolve_chromium_ws_url

logger = logging.getLogger(__name__)

DEFAULT_VIEWPORT: tuple[int, int] = (1366, 768)
DEFAULT_SETTLE_MS = 300
MAX_ELEMENTS = 300

# Scroll back to the top so document coordinates line up with the viewport
# screenshot (anchors and load-time scripts can scroll), then drop the
# scrollbar gutter. CSSOM, not an injected <style>: CSP style-src does not
# block it, and Chromium keeps the page scrollable.
_PREPARE_JS = (
    "window.scrollTo(0, 0); document.documentElement.style.scrollbarWidth = 'none'"
)

VISUAL_ENDPOINT_MISSING_MSG = (
    "MESHWEAVE_VISUAL_CDP_ENDPOINT is not set. Screenshot and geometry "
    "checks need a Chromium CDP browser; start the compose service "
    "'chromium' and set MESHWEAVE_VISUAL_CDP_ENDPOINT, e.g. "
    "http://chromium:9222."
)

_COLLECT_TEMPLATE = r"""() => {
  const sel = 'a[href],button,input,select,textarea,summary,' +
    '[role="button"],[role="link"],[role="menuitem"],[onclick]';
  const vw = window.innerWidth;
  const vh = window.innerHeight;
  const maxLayers = 12;
  const maxElements = __MAX_ELEMENTS__;

  // Computed styles keep oklch()/lab()/color() verbatim while canvas colours
  // are always sRGB, so a fillStyle round trip converts every colour the
  // browser accepts into one downstream format.
  const cvx = document.createElement('canvas').getContext('2d');
  const toRgb = (value) => {
    const s = String(value || '');
    if (/^#|^rgba?\(/i.test(s)) return s;
    if (window.CSS && CSS.supports('color', s)) {
      cvx.fillStyle = s;
      return String(cvx.fillStyle);
    }
    return s;
  };

  const parse = (value) => {
    const s = toRgb(value);
    let m = /^#([0-9a-f]{3,8})$/i.exec(s);
    if (m) {
      let d = m[1];
      if (d.length === 3 || d.length === 4) d = d.replace(/(.)/g, '$1$1');
      if (d.length !== 6 && d.length !== 8) return null;
      const n = d.match(/../g).map((x) => parseInt(x, 16));
      return {r: n[0], g: n[1], b: n[2], a: n.length > 3 ? n[3] / 255 : 1};
    }
    m = /rgba?\(([^)]+)\)/.exec(s);
    if (!m) return null;
    const p = m[1].replace('/', ',').split(/[\s,]+/).filter(Boolean).map(Number);
    if (p.length < 3 || p.slice(0, 3).some((n) => !Number.isFinite(n))) return null;
    return {r: p[0], g: p[1], b: p[2], a: p.length > 3 ? p[3] : 1};
  };

  const over = (top, bottom) => {
    const a = top.a + bottom.a * (1 - top.a);
    if (a === 0) return {r: 0, g: 0, b: 0, a: 0};
    return {
      r: (top.r * top.a + bottom.r * bottom.a * (1 - top.a)) / a,
      g: (top.g * top.a + bottom.g * bottom.a * (1 - top.a)) / a,
      b: (top.b * top.a + bottom.b * bottom.a * (1 - top.a)) / a,
      a: a
    };
  };

  const css = (c) =>
    'rgb(' + Math.round(c.r) + ', ' + Math.round(c.g) + ', ' +
    Math.round(c.b) + ')';

  // Composite every ancestor background onto white, stopping at an opaque
  // layer. Gradients, images, and a DOM deeper than maxLayers all make the
  // result an estimate.
  const effectiveBg = (el, skipSelf) => {
    const layers = [];
    let estimated = false;
    let node = skipSelf && el.parentElement ? el.parentElement : el;
    for (; node; node = node.parentElement) {
      const cs = getComputedStyle(node);
      if (cs.backgroundImage !== 'none') estimated = true;
      const c = parse(cs.backgroundColor);
      if (c && c.a > 0) layers.push(c);
      if (layers.length >= maxLayers) { estimated = true; break; }
    }
    let out = {r: 255, g: 255, b: 255, a: 1};
    for (let i = layers.length - 1; i >= 0; i--) out = over(layers[i], out);
    return {color: css(out), estimated: estimated};
  };

  const out = [];
  for (const el of document.querySelectorAll(sel)) {
    const r = el.getBoundingClientRect();
    if (r.width < 2 || r.height < 2) continue;
    const cs = getComputedStyle(el);
    if (cs.visibility === 'hidden' || cs.display === 'none' ||
        Number(cs.opacity) === 0) continue;

    const own = parse(cs.backgroundColor);
    const bg = effectiveBg(el, false);
    const surrounding = effectiveBg(el, true);

    let occluded = false;
    let hit = null;
    const cx = r.left + r.width / 2;
    const cy = r.top + r.height / 2;
    if (cx >= 0 && cy >= 0 && cx < vw && cy < vh) {
      hit = document.elementFromPoint(cx, cy);
      occluded = Boolean(hit) && hit !== el && !el.contains(hit);
    }

    // A styled inner <span> carries the label's real colour and type size;
    // use the hit element's styles when it is a text-bearing descendant.
    const inner = hit && hit !== el && el.contains(hit) &&
      (hit.innerText || '').trim() ? hit : null;
    const labelCs = inner ? getComputedStyle(inner) : cs;

    const label = (el.getAttribute('aria-label') || '').trim();
    const text = (el.innerText || el.value || '').trim()
      .replace(/\s+/g, ' ').slice(0, 120);
    const title = (el.getAttribute('title') || '').trim();

    out.push({
      tag: el.tagName.toLowerCase(),
      role: el.getAttribute('role') || '',
      name: label || text || title,
      text: text,
      href: el.getAttribute('href') || '',
      input_type: el.getAttribute('type') || '',
      x: Math.round(r.x + window.scrollX),
      y: Math.round(r.y + window.scrollY),
      width: Math.round(r.width),
      height: Math.round(r.height),
      above_fold: r.top >= 0 && r.bottom <= vh,
      in_viewport: r.bottom > 0 && r.right > 0 && r.top < vh && r.left < vw,
      disabled: Boolean(el.disabled) ||
        el.getAttribute('aria-disabled') === 'true',
      focusable: el.tabIndex >= 0 && !el.disabled,
      has_accessible_name: Boolean(label || text || title),
      occluded: occluded,
      has_own_background: Boolean(own && own.a > 0),
      background_color: bg.color,
      background_estimated: bg.estimated,
      surrounding_color: surrounding.color,
      styles: {
        color: toRgb(labelCs.color),
        background_color: toRgb(cs.backgroundColor),
        font_size: labelCs.fontSize,
        font_weight: labelCs.fontWeight,
        opacity: cs.opacity
      }
    });
    if (out.length >= maxElements) break;
  }
  return {
    elements: out,
    scroll_height: document.documentElement.scrollHeight
  };
}"""

_COLLECT_SCRIPT = _COLLECT_TEMPLATE.replace("__MAX_ELEMENTS__", str(MAX_ELEMENTS))


@dataclass(frozen=True)
class VisualElement:
    """One visible interactive element and its rendered box and styles."""

    tag: str
    role: str
    name: str
    text: str
    href: str
    input_type: str
    x: int
    y: int
    width: int
    height: int
    above_fold: bool
    in_viewport: bool
    disabled: bool
    focusable: bool
    has_accessible_name: bool
    occluded: bool
    has_own_background: bool
    background_color: str
    background_estimated: bool
    surrounding_color: str
    styles: dict[str, str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "tag": self.tag,
            "role": self.role,
            "name": self.name,
            "text": self.text,
            "href": self.href,
            "input_type": self.input_type,
            "x": self.x,
            "y": self.y,
            "width": self.width,
            "height": self.height,
            "above_fold": self.above_fold,
            "in_viewport": self.in_viewport,
            "disabled": self.disabled,
            "focusable": self.focusable,
            "has_accessible_name": self.has_accessible_name,
            "occluded": self.occluded,
            "has_own_background": self.has_own_background,
            "background_color": self.background_color,
            "background_estimated": self.background_estimated,
            "surrounding_color": self.surrounding_color,
            "styles": dict(self.styles),
        }


@dataclass(frozen=True)
class VisualSnapshot:
    """A page screenshot plus the geometry of its interactive elements."""

    url: str
    final_url: str
    viewport_width: int
    viewport_height: int
    scroll_height: int
    screenshot_png: bytes
    elements: tuple[VisualElement, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "final_url": self.final_url,
            "viewport": {
                "width": self.viewport_width,
                "height": self.viewport_height,
            },
            "scroll_height": self.scroll_height,
            "screenshot_bytes": len(self.screenshot_png),
            "elements": [element.to_dict() for element in self.elements],
        }


def _as_str(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _as_int(value: Any) -> int:
    return int(value) if isinstance(value, (int, float)) else 0


def _as_bool(value: Any) -> bool:
    return bool(value)


def _as_element(raw: dict[str, Any]) -> VisualElement:
    styles = raw.get("styles")
    return VisualElement(
        tag=_as_str(raw.get("tag")),
        role=_as_str(raw.get("role")),
        name=_as_str(raw.get("name")),
        text=_as_str(raw.get("text")),
        href=_as_str(raw.get("href")),
        input_type=_as_str(raw.get("input_type")),
        x=_as_int(raw.get("x")),
        y=_as_int(raw.get("y")),
        width=_as_int(raw.get("width")),
        height=_as_int(raw.get("height")),
        above_fold=_as_bool(raw.get("above_fold")),
        in_viewport=_as_bool(raw.get("in_viewport")),
        disabled=_as_bool(raw.get("disabled")),
        focusable=_as_bool(raw.get("focusable")),
        has_accessible_name=_as_bool(raw.get("has_accessible_name")),
        occluded=_as_bool(raw.get("occluded")),
        has_own_background=_as_bool(raw.get("has_own_background")),
        background_color=_as_str(raw.get("background_color")),
        background_estimated=_as_bool(raw.get("background_estimated")),
        surrounding_color=_as_str(raw.get("surrounding_color")),
        styles={
            str(key): _as_str(value)
            for key, value in (styles.items() if isinstance(styles, dict) else ())
        },
    )


def _elements(data: dict[str, Any]) -> tuple[VisualElement, ...]:
    raw = data.get("elements")
    if not isinstance(raw, list):
        return ()
    return tuple(_as_element(item) for item in raw if isinstance(item, dict))


def _snapshot(
    url: str,
    final_url: str,
    viewport: tuple[int, int],
    png: bytes,
    data: dict[str, Any],
) -> VisualSnapshot:
    return VisualSnapshot(
        url=url,
        final_url=final_url,
        viewport_width=viewport[0],
        viewport_height=viewport[1],
        scroll_height=_as_int(data.get("scroll_height")),
        screenshot_png=png,
        elements=_elements(data),
    )


@asynccontextmanager
async def _connected(ws_url: str, timeout_ms: int) -> AsyncIterator[Browser]:
    playwright = await async_playwright().start()
    try:
        browser = await playwright.chromium.connect_over_cdp(ws_url, timeout=timeout_ms)
        try:
            yield browser
        finally:
            await browser.close()
    finally:
        await playwright.stop()


async def _capture(
    browser: Browser,
    url: str,
    viewport: tuple[int, int],
    timeout: float,
) -> VisualSnapshot:
    width, height = viewport
    context = await browser.new_context(viewport={"width": width, "height": height})
    try:
        context.set_default_timeout(int(timeout * 1000))
        page = await context.new_page()
        response = await page.goto(url, wait_until="load", timeout=int(timeout * 1000))
        await page.evaluate(_PREPARE_JS)
        await page.wait_for_timeout(DEFAULT_SETTLE_MS)
        png = await page.screenshot(type="png")
        data = await page.evaluate(_COLLECT_SCRIPT)
        final_url = response.url if response else url
        return _snapshot(
            url,
            str(final_url),
            viewport,
            png,
            data if isinstance(data, dict) else {},
        )
    finally:
        await context.close()


async def capture_visual_snapshot(
    url: str,
    *,
    cdp_endpoint: str | None = None,
    viewport: tuple[int, int] = DEFAULT_VIEWPORT,
    timeout: float = 30.0,
) -> VisualSnapshot:
    """Capture *url* through Chromium and return its visual snapshot.

    Images load (unlike the text crawl, which blocks them) so the screenshot
    is representative, the page is scrolled back to the top, and the browser
    scrollbar gutter is hidden. Element ``x``/``y`` coordinates are
    document-relative and line up with the first-viewport screenshot.
    """
    if not url.startswith(("http://", "https://")):
        raise ValueError("url must be an http(s) URL")
    if timeout <= 0:
        raise ValueError("timeout must be positive")
    endpoint = cdp_endpoint or os.environ.get("MESHWEAVE_VISUAL_CDP_ENDPOINT")
    if not endpoint:
        raise RuntimeError(VISUAL_ENDPOINT_MISSING_MSG)
    ws_url = await resolve_chromium_ws_url(endpoint)
    async with _connected(ws_url, int(timeout * 1000)) as browser:
        snapshot = await _capture(browser, url, viewport, timeout)
    logger.debug(
        "visual snapshot %s: %d elements, %d screenshot bytes",
        snapshot.final_url,
        len(snapshot.elements),
        len(snapshot.screenshot_png),
    )
    return snapshot
