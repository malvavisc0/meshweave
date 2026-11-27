"""add visual_audits table

One stored visual CTA assessment per crawl: the assessment JSON, capture
metadata, and the screenshot/attention-overlay PNGs, so the result page,
the report export, and the API can re-serve a capture without re-running
Chromium. A re-run replaces the row; the FK cascade removes it with the
crawl.

Revision ID: c4f2a8e1b9d0
Revises: b7c4e1a9d2f3
Create Date: 2026-11-27 00:55:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c4f2a8e1b9d0"
down_revision: str | None = "b7c4e1a9d2f3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "visual_audits",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("crawl_id", sa.String(length=36), nullable=False),
        sa.Column("url", sa.Text(), nullable=False, server_default=""),
        sa.Column("assessment_json", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("attention_model", sa.Boolean(), nullable=False),
        sa.Column("viewport", sa.JSON(), nullable=False),
        sa.Column("element_count", sa.Integer(), nullable=False),
        sa.Column("screenshot_png", sa.LargeBinary(), nullable=False),
        sa.Column("overlay_png", sa.LargeBinary(), nullable=True),
        sa.Column("capture_version", sa.String(length=16), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["crawl_id"],
            ["crawls.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("crawl_id", name="uq_visual_audits_crawl_id"),
    )
    op.create_index(
        "ix_visual_audits_created_at",
        "visual_audits",
        ["created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_visual_audits_created_at", table_name="visual_audits")
    op.drop_table("visual_audits")
