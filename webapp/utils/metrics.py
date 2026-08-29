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

# Homepage metrics
homepage_analyze_submits = Counter(
    "homepage_analyze_submits_total",
    "Homepage Analyze submissions",
    ["authed", "public"],  # authed: true|false, public: true|false
)
homepage_signin_cta_clicks = Counter(
    "homepage_signin_cta_clicks_total",
    "Homepage Sign-in CTA clicks",
)


# Phase 1B API metrics (owner-scoped resources)
prospects_upsert = Counter(
    "prospects_upsert_total", "Prospects upsert/create operations"
)
prospects_patch = Counter(
    "prospects_patch_total", "Prospects partial update operations"
)
contacts_create = Counter("contacts_create_total", "Prospect contacts created")

# Stale finalization metrics
stale_finalize_attempts = Counter(
    "stale_finalize_attempts_total",
    "Attempts to finalize stale running jobs into a terminal state",
    ["scope"],  # page|site
)
stale_finalize_finished = Counter(
    "stale_finalize_finished_total",
    "Finalize outcomes for stale running jobs",
    ["scope", "outcome"],  # outcome: ok|race|noop|err
)


def metrics_body() -> bytes:
    """Return Prometheus metrics exposition as bytes."""
    latest: bytes = generate_latest()
    return latest


def metrics_content_type() -> str:
    """Return the Prometheus exposition content type string.

    Returns:
        str: Content type for Prometheus metrics (e.g., text/plain; version=0.0.4; charset=utf-8).
    """
    content_type: str = CONTENT_TYPE_LATEST
    return content_type
