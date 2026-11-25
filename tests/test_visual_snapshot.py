"""Chromium CDP resolution and visual snapshot normalization."""

from __future__ import annotations

from typing import Any

import pytest

from meshweave.crawling import cdp, visual


@pytest.mark.asyncio
async def test_ws_endpoint_passes_through() -> None:
    endpoint = "ws://10.0.0.5:9222/devtools/browser/abc"
    assert await _resolve(endpoint) == endpoint


async def _resolve(endpoint: str) -> str:
    return await cdp.resolve_chromium_ws_url(endpoint)


@pytest.mark.asyncio
async def test_http_endpoint_uses_discovered_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cdp, "_ip_literal", lambda host: "10.0.0.5")
    monkeypatch.setattr(
        cdp,
        "_read_version",
        lambda host, port, timeout, *, tls: {
            "webSocketDebuggerUrl": "ws://127.0.0.1:9223/devtools/browser/xyz"
        },
    )
    assert await _resolve("http://chromium:9222") == (
        "ws://10.0.0.5:9222/devtools/browser/xyz"
    )


@pytest.mark.asyncio
async def test_missing_web_socket_url_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cdp, "_ip_literal", lambda host: "10.0.0.5")
    monkeypatch.setattr(cdp, "_read_version", lambda host, port, timeout, *, tls: {})
    with pytest.raises(cdp.CdpError):
        await _resolve("http://chromium:9222")


@pytest.mark.asyncio
async def test_unsupported_scheme_raises() -> None:
    with pytest.raises(cdp.CdpError):
        await _resolve("ftp://chromium:9222")


def test_rewrite_ws_preserves_path_and_query() -> None:
    assert (
        cdp._rewrite_ws(
            "ws://127.0.0.1:9223/devtools/browser/abc?x=1",
            "10.0.0.5",
            9222,
            tls=False,
        )
        == "ws://10.0.0.5:9222/devtools/browser/abc?x=1"
    )


def test_rewrite_ws_brackets_ipv6() -> None:
    assert (
        cdp._rewrite_ws("ws://[::1]:9223/devtools/browser/abc", "::1", 9222, tls=False)
        == "ws://[::1]:9222/devtools/browser/abc"
    )


@pytest.mark.asyncio
async def test_capture_rejects_non_http_url() -> None:
    with pytest.raises(ValueError):
        await visual.capture_visual_snapshot("ftp://example.com")


@pytest.mark.asyncio
async def test_capture_requires_visual_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MESHWEAVE_VISUAL_CDP_ENDPOINT", raising=False)
    with pytest.raises(RuntimeError, match="MESHWEAVE_VISUAL_CDP_ENDPOINT"):
        await visual.capture_visual_snapshot("https://example.com")


def test_snapshot_dict_shape() -> None:
    snapshot = visual.VisualSnapshot(
        url="https://example.com",
        final_url="https://example.com/",
        viewport_width=1366,
        viewport_height=768,
        scroll_height=1200,
        screenshot_png=b"png",
        elements=(_element(),),
    )
    payload = snapshot.to_dict()
    assert payload["viewport"] == {"width": 1366, "height": 768}
    assert payload["screenshot_bytes"] == 3
    assert payload["elements"][0]["name"] == "Start free audit"


@pytest.mark.asyncio
async def test_invalid_port_raises() -> None:
    with pytest.raises(cdp.CdpError):
        await _resolve("http://chromium:99999")


def test_elements_ignore_junk() -> None:
    data: dict[str, Any] = {"elements": [{"name": "a"}, "junk"], "scroll_height": 900}
    elements = visual._elements(data)
    assert [element.name for element in elements] == ["a"]


def _element() -> visual.VisualElement:
    return visual.VisualElement(
        tag="button",
        role="",
        name="Start free audit",
        text="Start free audit",
        href="",
        input_type="",
        x=10,
        y=20,
        width=200,
        height=54,
        above_fold=True,
        in_viewport=True,
        disabled=False,
        focusable=True,
        has_accessible_name=True,
        occluded=False,
        has_own_background=True,
        background_color="rgb(11, 95, 255)",
        background_estimated=False,
        surrounding_color="rgb(255, 255, 255)",
        styles={"color": "rgb(255, 255, 255)"},
    )
