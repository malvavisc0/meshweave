from fastapi import Request


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
            xff = headers.get("x-forwarded-for") or headers.get("X-Forwarded-For")
            if xff:
                ip = xff.split(",")[0].strip()
        if not ip:
            ip = headers.get("x-real-ip") or headers.get("X-Real-IP") or ""
        if not ip:
            # Starlette provides client as (host, port)
            client = getattr(request, "client", None)
            if client and getattr(client, "host", None):
                ip = client.host
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
