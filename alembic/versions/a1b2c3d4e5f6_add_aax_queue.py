"""Add AAX queue columns to crawls

Revision ID: a1b2c3d4e5f6
Revises: 89c33b114a5b
Create Date: 2026-08-30 21:15:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, None] = "89c33b114a5b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # AAX queue state: pending → running → completed/failed/disabled
    op.add_column(
        "crawls",
        sa.Column("aax_status", sa.String(length=10), nullable=False, server_default="pending"),
    )
    # When AAX processing started (for stale detection)
    op.add_column(
        "crawls",
        sa.Column("aax_started_at", sa.DateTime(timezone=True), nullable=True),
    )
    # Index for the worker query: find pending AAX jobs efficiently
    op.create_index(
        "ix_crawls_aax_status",
        "crawls",
        ["aax_status", "updated_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_crawls_aax_status", table_name="crawls")
    op.drop_column("crawls", "aax_started_at")
    op.drop_column("crawls", "aax_status")
