from prometheus_client import (
    CONTENT_TYPE_LATEST,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)

# Authentication metrics
auth_attempts = Counter(
    "auth_attempts_total", "Total authentication attempts", ["provider", "result"]
)
active_sessions = Gauge(
    "active_sessions_count", "Current number of active (unexpired) auth sessions"
)

# Rate limiting / quotas
rate_limit_hits = Counter(
    "rate_limit_hits_total", "Rate limit/Quota enforcement hits", ["type"]
)

# Crawl jobs
job_duration = Histogram(
    "crawl_job_duration_seconds",
    "Crawl job duration in seconds",
    ["scope", "status"],  # scope: page|site; status: succeeded|failed
)

# Homepage and sharing metrics
homepage_analyze_submits = Counter(
    "homepage_analyze_submits_total",
    "Homepage Analyze submissions",
    ["authed", "public"],  # authed: true|false, public: true|false
)
homepage_advanced_toggle_clicks = Counter(
    "homepage_advanced_toggle_clicks_total",
    "Homepage Advanced toggle clicks",
    ["action"],  # action: open|close
)
homepage_signin_cta_clicks = Counter(
    "homepage_signin_cta_clicks_total",
    "Homepage Sign-in CTA clicks",
)
result_share_clicks = Counter(
    "result_share_clicks_total",
    "Result page share/copy clicks",
    ["type"],  # type: copy|link|other
)


def metrics_body() -> bytes:
    """Return Prometheus metrics exposition as bytes."""
    return generate_latest()


def metrics_content_type() -> str:
    """Return the Prometheus exposition content type string.

    Returns:
        str: Content type for Prometheus metrics (e.g., text/plain; version=0.0.4; charset=utf-8).
    """
    return CONTENT_TYPE_LATEST
