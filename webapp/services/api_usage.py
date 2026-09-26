"""Per-key daily API usage accounting.

Feeds the deferred rate-limit decision (``no_free_api_limits_yet``):
upserts one row per key per UTC day per call, counting calls and
admitted/rejected URLs. Recording is best-effort — usage accounting must
never break the API call itself — and uses a server-side upsert so
concurrent requests cannot lose counts.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy import text

from webapp.db import get_session

logger = logging.getLogger(__name__)


def record_usage(
    api_key_id: str | None,
    *,
    calls: int = 0,
    urls_admitted: int = 0,
    urls_rejected: int = 0,
) -> None:
    """Upsert today's usage counters for one API key (best-effort).

    SQLite and PostgreSQL both: INSERT .. ON CONFLICT (api_key_id, day)
    DO UPDATE with server-side additions, so concurrent calls accumulate.
    """
    if not api_key_id:
        return
    try:
        today = datetime.now(UTC).date()
        with get_session() as s:
            s.execute(
                text(
                    "INSERT INTO api_key_usage_daily "
                    "(api_key_id, day, calls, urls_admitted, urls_rejected, updated_at) "
                    "VALUES (:key_id, :day, :calls, :adm, :rej, :now) "
                    "ON CONFLICT (api_key_id, day) DO UPDATE SET "
                    "calls = calls + excluded.calls, "
                    "urls_admitted = urls_admitted + excluded.urls_admitted, "
                    "urls_rejected = urls_rejected + excluded.urls_rejected, "
                    "updated_at = excluded.updated_at"
                ),
                {
                    "key_id": api_key_id,
                    "day": today,
                    "calls": calls,
                    "adm": urls_admitted,
                    "rej": urls_rejected,
                    "now": datetime.now(UTC),
                },
            )
    except Exception:
        logger.debug("api usage recording failed", exc_info=True)
