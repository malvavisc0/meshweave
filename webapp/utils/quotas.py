import os
from datetime import UTC, datetime

from fastapi import HTTPException
from starlette import status

from webapp.db import get_session
from webapp.models import Crawl
from webapp.utils.metrics import rate_limit_hits


def _now() -> datetime:
    """Current UTC datetime.

    Returns:
        datetime: Now in UTC.
    """
    return datetime.now(UTC)


def _start_of_utc_day(dt: datetime) -> datetime:
    """Start of day (00:00:00) in UTC for a given datetime.

    Args:
        dt (datetime): Input datetime.

    Returns:
        datetime: The same date at 00:00:00 with UTC tzinfo.
    """
    return dt.replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=UTC)


def _int_env(name: str, default: int) -> int:
    """Read an integer environment variable with fallback.

    Args:
        name (str): Environment variable name.
        default (int): Fallback value when unset or invalid.

    Returns:
        int: Parsed integer value or the provided default.
    """
    try:
        return int(os.getenv(name, str(default)))
    except Exception:
        return default


def _daily_site_limit() -> int:
    """Per-user daily quota for site crawls.

    Returns:
        int: Limit from AUTH_USER_DAILY_SITE_CRAWLS, default 10.
    """
    # Default 10 per docs
    return _int_env("AUTH_USER_DAILY_SITE_CRAWLS", 10)


def _concurrent_jobs_limit() -> int:
    """Per-user concurrent jobs limit across scopes.

    Returns:
        int: Limit from AUTH_USER_CONCURRENT_JOBS, default 3.
    """
    # Default 3 per docs
    return _int_env("AUTH_USER_CONCURRENT_JOBS", 3)


def _count_user_daily_site_crawls(user_id: str) -> int:
    """Count site crawls created today (UTC) for a user."""
    now = _now()
    start_day = _start_of_utc_day(now)
    with get_session() as s:
        return (
            s.query(Crawl)
            .filter(
                Crawl.user_id == user_id,
                Crawl.crawl_params.isnot(None),
                Crawl.created_at >= start_day,
            )
            .count()
        )


def _count_user_concurrent_jobs(user_id: str) -> int:
    """Count current pending/running jobs for a user across scopes."""
    with get_session() as s:
        return (
            s.query(Crawl)
            .filter(
                Crawl.user_id == user_id,
                Crawl.status.in_(("pending", "running")),
            )
            .count()
        )


def enforce_daily_site_crawl_limit(user_id: str) -> None:
    """Raise 429 if user exceeded daily site crawl limit."""
    used = _count_user_daily_site_crawls(user_id)
    limit = _daily_site_limit()
    if used >= limit:
        try:
            rate_limit_hits.labels("daily_site").inc()
        except Exception:
            pass
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Daily site crawl quota exceeded",
        )


def enforce_concurrent_jobs_limit(user_id: str) -> None:
    """Raise 429 if user exceeded concurrent job limit (user-owned jobs only)."""
    used = _count_user_concurrent_jobs(user_id)
    limit = _concurrent_jobs_limit()
    if used >= limit:
        try:
            rate_limit_hits.labels("concurrent_jobs").inc()
        except Exception:
            pass
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many concurrent jobs",
        )
