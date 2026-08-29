from fastapi import Request
from starlette.datastructures import Headers


def _normalize_ip(ip: str) -> str:
    """Normalize an IP string by stripping IPv6-mapped IPv4 prefix.

    Args:
        ip (str): IP address string.

    Returns:
        str: Normalized IP string (e.g., '::ffff:1.2.3.4' -> '1.2.3.4').
    """
    ip = (ip or "").strip()
    if ip.startswith("::ffff:"):
        return ip[len("::ffff:") :]
    return ip


def _forwarded_for_ip(headers: Headers) -> str:
    """Extract the first X-Forwarded-For entry, if any.

    Args:
        headers (Headers): Request headers mapping.

    Returns:
        str: The first X-Forwarded-For IP, or empty string when absent.
    """
    xff = headers.get("x-forwarded-for") or headers.get("X-Forwarded-For")
    if xff:
        return xff.split(",")[0].strip()
    return ""


def _real_ip_header(headers: Headers) -> str:
    """Read the X-Real-IP header, if any.

    Args:
        headers (Headers): Request headers mapping.

    Returns:
        str: The X-Real-IP value, or empty string when absent.
    """
    return headers.get("x-real-ip") or headers.get("X-Real-IP") or ""


def _client_host(request: Request) -> str:
    """Read the connection client host, if any.

    Args:
        request (Request): Incoming request.

    Returns:
        str: The client host, or empty string when unavailable.
    """
    # Starlette provides client as (host, port)
    client = getattr(request, "client", None)
    host: str | None = getattr(client, "host", None) if client else None
    return host or ""


def _client_ip_from_request(request: Request, trust_proxy: bool) -> str:
    """Extract client IP from a request, honoring proxy headers if configured.

    When trust_proxy is True, uses X-Forwarded-For/X-Real-IP headers; otherwise,
    falls back to the connection client host.

    Args:
        request (Request): Incoming FastAPI/Starlette request.
        trust_proxy (bool): Whether to trust proxy headers.

    Returns:
        str: Normalized client IP or empty string if unavailable.
    """
    headers = request.headers
    ip = ""
    try:
        if trust_proxy:
            ip = _forwarded_for_ip(headers)
        if not ip:
            ip = _real_ip_header(headers)
        if not ip:
            ip = _client_host(request)
    except Exception:
        pass
    return _normalize_ip(ip)


def _collect_headers_subset(request: Request) -> dict:
    """Collect a subset of request headers for logging.

    Args:
        request (Request): Incoming request.

    Returns:
        dict: Selected header names mapped to their values (None omitted).
    """
    h = request.headers
    subset = {
        "user-agent": h.get("user-agent"),
        "accept-language": h.get("accept-language"),
        "referer": h.get("referer"),
        "origin": h.get("origin"),
        "host": h.get("host"),
        "x-request-id": h.get("x-request-id"),
        "x-correlation-id": h.get("x-correlation-id"),
        "x-forwarded-for": h.get("x-forwarded-for"),
        "x-real-ip": h.get("x-real-ip"),
    }
    # Drop None values
    return {k: v for k, v in subset.items() if v is not None}
