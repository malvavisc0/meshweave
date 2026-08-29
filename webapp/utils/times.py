"""Timezone-aware datetime helpers.

SQLite returns naive datetimes for ``DateTime(timezone=True)`` columns even
though all values are written as UTC, while PostgreSQL returns aware ones.
Comparing a naive DB value against an aware ``datetime.now(UTC)`` raises
``TypeError``, so DB-loaded datetimes must be normalized before comparison.
"""

from datetime import UTC, datetime


def ensure_utc(value: datetime) -> datetime:
    """Return ``value`` with UTC tzinfo, assuming naive values are UTC.

    Args:
        value (datetime): A datetime loaded from the database or produced
            locally. Naive values are interpreted as UTC, matching how the
            application stores them.

    Returns:
        datetime: The same instant with ``tzinfo`` set to UTC.
    """
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value
