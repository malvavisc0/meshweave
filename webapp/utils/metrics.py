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


def metrics_body() -> bytes:
    """Return Prometheus metrics exposition as bytes."""
    return generate_latest()


def metrics_content_type() -> str:
    """Return the Prometheus exposition content type string.

    Returns:
        str: Content type for Prometheus metrics (e.g., text/plain; version=0.0.4; charset=utf-8).
    """
    return CONTENT_TYPE_LATEST
