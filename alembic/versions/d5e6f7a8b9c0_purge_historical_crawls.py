"""Purge every historical crawl and drop the dead crawl AI-analysis column.

The site-side model change (scoring 1.3) makes every historical score and
report misleading if it stays reachable, and no historical score is
converted to the new model. Deleting the crawl rows is the whole purge:

- ``submissions.crawl_id`` and ``score_snapshots.crawl_id`` are ON DELETE
  CASCADE, so crawl-derived score and revision data dies with the run.
- ``funnel_events.crawl_id`` and ``prospects.crawl_id`` are ON DELETE SET
  NULL, so known-user funnel history and prospect records survive with
  their crawl reference nulled.

Accounts, API keys, key usage, prospects, prospect contacts, funnel
events, per-user funnel state, and funnel actor domains are untouched —
never recomputed from crawls, so users who saw or dismissed a gate offer
are never re-pitched. Revision history, domain score history, and
re-check series start empty.

``crawls.ai_analysis_json`` is dropped with the rows: it was written and
read by nothing.

Revision ID: d5e6f7a8b9c0
Revises: b4c5d6e7f8a9
Create Date: 2026-09-26
"""

from collections.abc import Sequence

from alembic import op

revision: str = "d5e6f7a8b9c0"
down_revision: str | Sequence[str] | None = "b4c5d6e7f8a9"

# The purge: every crawl row goes; DB cascades clear the crawl-derived
# rows and null the surviving crawl references.
PURGE_CRAWLS_SQL = "DELETE FROM crawls"


def upgrade() -> None:
    op.execute(PURGE_CRAWLS_SQL)
    op.drop_column("crawls", "ai_analysis_json")


def downgrade() -> None:
    # The purge cannot be undone: the deleted rows are gone and were
    # never converted. Refuse loudly instead of pretending to reverse.
    raise RuntimeError(
        "downgrade refused: the historical-crawl purge (d5e6f7a8b9c0) is irreversible"
    )
