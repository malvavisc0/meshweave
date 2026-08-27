import base64
import uuid
from urllib.parse import parse_qsl, urlencode, urlparse

from fastapi import Request


def normalize_domain(url: str) -> str:
    """Normalize a URL's domain by lowercasing and stripping a leading 'www.'.

    Args:
        url (str): Input URL.

    Returns:
        str: Normalized domain, or empty string on error.
    """
    try:
        parsed = urlparse(url or "")
        host = (parsed.netloc or "").lower()
        return host[4:] if host.startswith("www.") else host
    except Exception:
        return ""


def _normalize_path(p: str) -> str:
    """Normalize a URL path.

    Ensures leading '/', trims trailing '/' (except root), and treats blank as '/'.

    Args:
        p (str): Raw path.

    Returns:
        str: Normalized path.
    """
    p = (p or "").strip()
    if not p or p == "":
        return "/"
    if not p.startswith("/"):
        p = "/" + p
    if p != "/" and p.endswith("/"):
        p = p.rstrip("/")
    return p


def _normalize_query(q: str) -> str:
    """Normalize and sort a query string.

    Parses the query, sorts pairs by (key, value), preserves duplicates and blanks.

    Args:
        q (str): Raw query string (with or without leading '?').

    Returns:
        str: Normalized query string without a leading '?'.
    """
    try:
        pairs = parse_qsl(q or "", keep_blank_values=True)
        pairs.sort(key=lambda kv: (kv[0], kv[1]))
        return urlencode(pairs, doseq=True)
    except Exception:
        return (q or "").lstrip("?").strip()


def canonicalize_url(url: str) -> tuple[str, str, str, str]:
    """Canonicalize a URL into components and a normalized absolute form.

    Returns a tuple (domain, path, query, canonical_url), where:
      - domain: normalized by stripping leading 'www.' and lowercasing
      - path: ensured leading '/', trailing '/' trimmed except for root
      - query: sorted, normalized query string without leading '?'
      - canonical_url: https://{domain}{path}{?query}

    Args:
        url (str): Input URL.

    Returns:
        Tuple[str, str, str, str]: (domain, path, query, canonical_url)
    """
    parsed = urlparse(url or "")
    domain = (parsed.netloc or "").lower()
    domain = domain[4:] if domain.startswith("www.") else domain
    path = _normalize_path(parsed.path or "")
    query = _normalize_query(parsed.query or "")
    canon = f"https://{domain}{path}"
    if query:
        canon += f"?{query}"
    return domain, path, query, canon


def _get_base_url(request: Request) -> str:
    """Compute the base URL for absolute links and SEO metadata.

    Prefers SITE_BASE_URL if set, otherwise infers from request scheme and host.

    Args:
        request (Request): Incoming request.

    Returns:
        str: Base URL string, without trailing slash.
    """
    # app.state attributes live in Starlette's State._state — a plain
    # __dict__ lookup always misses them.
    env_base = getattr(request.app.state, "SITE_BASE_URL_OVERRIDE", None)
    if isinstance(env_base, str) and env_base:
        return env_base.rstrip("/")
    scheme = getattr(request.url, "scheme", None) or "http"
    host = request.headers.get("host") or "localhost"
    return f"{scheme}://{host}"


def _abs_url(request: Request, path: str) -> str:
    """Build an absolute URL from a relative path.

    Args:
        request (Request): Incoming request used to infer base URL.
        path (str): Relative path (with or without leading '/').

    Returns:
        str: Absolute URL string.
    """
    base = _get_base_url(request)
    if not path.startswith("/"):
        path = "/" + path
    return f"{base}{path}"


def generate_short_key() -> str:
    """Generate a short, URL-safe key derived from UUID4 bytes.

    Returns roughly 22 URL-safe base64 characters (without padding).

    Returns:
        str: Generated short key.
    """
    raw = uuid.uuid4().bytes
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
