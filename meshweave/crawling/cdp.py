"""Resolve a Chromium CDP endpoint to a WebSocket URL Playwright can dial.

Chromium serves the browser target at ``/devtools/browser/<id>``, advertised
by ``/json/version``. Unlike LightPanda, it has no useful WebSocket URL at
the root path, and it rejects non-IP ``Host`` headers, so the endpoint host
is resolved to an IP literal before discovery. The advertised URL is then
rewritten onto that reachable IP and the endpoint's port, which also covers
fronted containers that expose a proxy port.
"""

from __future__ import annotations

import asyncio
import json
import socket
from typing import Any
from urllib.parse import SplitResult, urlsplit, urlunsplit
from urllib.request import urlopen


class CdpError(RuntimeError):
    """A CDP endpoint could not be resolved to a usable WebSocket URL."""


def _endpoint_port(parts: SplitResult) -> int:
    """Explicit or scheme-default port for an endpoint."""
    return parts.port or (443 if parts.scheme in {"https", "wss"} else 80)


def _ip_literal(host: str) -> str:
    """Resolve *host* to a reachable IP literal, preferring IPv4."""
    try:
        infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise CdpError(f"Cannot resolve CDP host {host!r}: {exc}") from exc
    for family, _, _, _, sockaddr in infos:
        if family == socket.AF_INET:
            return str(sockaddr[0])
    return str(infos[0][4][0])


def _read_version(host: str, port: int, timeout: float, *, tls: bool) -> dict[str, Any]:
    """Fetch ``/json/version`` from a Chromium CDP server."""
    scheme = "https" if tls else "http"
    try:
        with urlopen(f"{scheme}://{host}:{port}/json/version", timeout=timeout) as resp:
            payload = json.load(resp)
    except (OSError, ValueError) as exc:
        raise CdpError(
            f"Cannot read Chromium CDP discovery from {host}:{port}: {exc}"
        ) from exc
    if not isinstance(payload, dict):
        raise CdpError(f"Unexpected CDP discovery payload from {host}:{port}")
    return payload


def _rewrite_ws(advertised: str, host: str, port: int, *, tls: bool) -> str:
    """Rewrite an advertised WebSocket URL onto a reachable host and port."""
    parts = urlsplit(advertised)
    netloc = f"[{host}]:{port}" if ":" in host else f"{host}:{port}"
    return urlunsplit(
        ("wss" if tls else "ws", netloc, parts.path or "/", parts.query, "")
    )


async def resolve_chromium_ws_url(endpoint: str, *, timeout: float = 5.0) -> str:
    """Return the browser WebSocket URL for a Chromium CDP *endpoint*.

    ``ws(s)://`` endpoints pass through verbatim. ``http(s)://`` endpoints
    are resolved through ``/json/version``; the advertised target is rewritten
    to the endpoint's IP literal and port so it is reachable across containers.
    """
    parts = urlsplit(endpoint)
    if parts.scheme in {"ws", "wss"}:
        return endpoint
    if parts.scheme not in {"http", "https"}:
        raise CdpError(f"Unsupported CDP endpoint scheme: {endpoint!r}")
    if not parts.hostname:
        raise CdpError(f"Invalid CDP endpoint: {endpoint!r}")

    try:
        port = _endpoint_port(parts)
    except ValueError as exc:
        raise CdpError(f"Invalid CDP endpoint port: {endpoint!r}") from exc
    ip = await asyncio.to_thread(_ip_literal, parts.hostname)
    version = await asyncio.to_thread(
        _read_version, ip, port, timeout, tls=parts.scheme == "https"
    )
    advertised = version.get("webSocketDebuggerUrl")
    if not isinstance(advertised, str) or not advertised:
        raise CdpError(f"CDP endpoint {endpoint!r} advertised no webSocketDebuggerUrl")
    return _rewrite_ws(advertised, ip, port, tls=parts.scheme == "https")
