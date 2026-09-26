"""durable crawl queue columns on crawls

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-09-13

Adds queue_status / queue_started_at to crawls for the durable bulk-crawl
queue (Track D1), plus the worker poll index. NULL queue_status means the
crawl is not queue-managed (form submissions ride BackgroundTasks).
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b2c3d4e5f6a7"
down_revision: Union[str, None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "crawls",
        sa.Column("queue_status", sa.String(length=10), nullable=True),
    )
    op.add_column(
        "crawls",
        sa.Column("queue_started_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_crawls_queue_status", "crawls", ["queue_status", "created_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_crawls_queue_status", table_name="crawls")
    op.drop_column("crawls", "queue_started_at")
    op.drop_column("crawls", "queue_status")
