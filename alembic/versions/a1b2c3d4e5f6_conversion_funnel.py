"""conversion funnel tables and supporting indexes

Revision ID: a1b2c3d4e5f6
Revises: 4c6f9c5ed377
Create Date: 2026-09-13

New tables: funnel_events, funnel_state, funnel_actor_domains.
New columns: api_keys.first_used_at (nullable).
New indexes: crawls(anonymous_user_id), crawls(user_id, created_at, id).
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, None] = "4c6f9c5ed377"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "funnel_events",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column(
            "user_id", sa.String(length=36), nullable=False
        ),
        sa.Column("event_type", sa.String(length=32), nullable=False),
        sa.Column("crawl_id", sa.String(length=36), nullable=True),
        sa.Column("domain", sa.String(length=255), nullable=True),
        sa.Column(
            "payload",
            sa.JSON().with_variant(
                sa.dialects.postgresql.JSONB(), "postgresql"
            ),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["crawl_id"], ["crawls.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_funnel_events_user", "funnel_events", ["user_id", "created_at"]
    )
    op.create_index(
        "ix_funnel_events_type", "funnel_events", ["event_type", "created_at"]
    )

    op.create_table(
        "funnel_state",
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("stage", sa.String(length=16), nullable=False),
        sa.Column("segment", sa.String(length=16), nullable=False),
        sa.Column("analyses_count", sa.Integer(), nullable=False),
        sa.Column("distinct_domains", sa.Integer(), nullable=False),
        sa.Column("last_event_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "dismissed_nudges",
            sa.JSON().with_variant(
                sa.dialects.postgresql.JSONB(), "postgresql"
            ),
            nullable=False,
        ),
        sa.Column(
            "gates_seen",
            sa.JSON().with_variant(
                sa.dialects.postgresql.JSONB(), "postgresql"
            ),
            nullable=False,
        ),
        sa.Column(
            "gates_taken",
            sa.JSON().with_variant(
                sa.dialects.postgresql.JSONB(), "postgresql"
            ),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id"),
    )

    op.create_table(
        "funnel_actor_domains",
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("domain", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id", "domain", name="pk_funnel_actor_domains"),
    )

    op.add_column(
        "api_keys",
        sa.Column("first_used_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_index(
        "ix_crawls_anonymous_user_id", "crawls", ["anonymous_user_id"]
    )
    op.create_index(
        "ix_crawls_user_created", "crawls", ["user_id", "created_at", "id"]
    )


def downgrade() -> None:
    op.drop_index("ix_crawls_user_created", table_name="crawls")
    op.drop_index("ix_crawls_anonymous_user_id", table_name="crawls")
    op.drop_column("api_keys", "first_used_at")
    op.drop_table("funnel_actor_domains")
    op.drop_table("funnel_state")
    op.drop_index("ix_funnel_events_type", table_name="funnel_events")
    op.drop_index("ix_funnel_events_user", table_name="funnel_events")
    op.drop_table("funnel_events")
