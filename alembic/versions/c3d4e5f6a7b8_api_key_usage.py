"""per-key daily API usage counters

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-09-14

New table: api_key_usage_daily (one row per key per UTC day, upserted by
the v1 API middleware). Feeds the deferred rate-limit decision: p50/p95
calls and admitted URLs per key per day, queryable per key and per day.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c3d4e5f6a7b8"
down_revision: Union[str, None] = "b2c3d4e5f6a7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "api_key_usage_daily",
        sa.Column("api_key_id", sa.String(length=36), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("calls", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("urls_admitted", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("urls_rejected", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["api_key_id"], ["api_keys.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("api_key_id", "day"),
    )
    op.create_index(
        "ix_api_key_usage_daily_day",
        "api_key_usage_daily",
        ["day"],
    )
    op.create_index(
        "ix_api_key_usage_daily_key_day",
        "api_key_usage_daily",
        ["api_key_id", "day"],
    )


def downgrade() -> None:
    op.drop_index("ix_api_key_usage_daily_key_day", table_name="api_key_usage_daily")
    op.drop_index("ix_api_key_usage_daily_day", table_name="api_key_usage_daily")
    op.drop_table("api_key_usage_daily")
