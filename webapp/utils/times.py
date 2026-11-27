"""Timezone-aware datetime helpers.

SQLite returns naive datetimes for ``DateTime(timezone=True)`` columns even
though all values are written as UTC, while PostgreSQL returns aware ones.
Comparing a naive DB value against an aware ``datetime.now(UTC)`` raises
``TypeError``, so DB-loaded datetimes must be normalized before comparison.
"""

import math
from datetime import UTC, datetime, timedelta


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


def cooldown(updated_at: datetime, min_age_minutes: int) -> tuple[bool, str]:
    """Return ``(can_proceed, eta_label)`` for a row's age-based cooldown.

    The eta label never reads "0m" while a run is still blocked.

    Args:
        updated_at (datetime): When the row last ran or updated.
        min_age_minutes (int): Minimum age before another run is allowed.

    Returns:
        tuple[bool, str]: Whether a new run may start, and the human eta
        label while it may not.
    """
    now = datetime.now(UTC)
    next_eligible = ensure_utc(updated_at) + timedelta(minutes=min_age_minutes)
    can_proceed = now >= next_eligible
    remaining_seconds = (next_eligible - now).total_seconds()
    minutes = max(1, math.ceil(remaining_seconds / 60))
    return can_proceed, "" if can_proceed else f"{minutes}m"
