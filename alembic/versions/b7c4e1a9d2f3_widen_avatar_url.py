"""widen users.avatar_url to text

Google profile picture URLs exceed the old varchar(1024) limit, so OAuth
registration failed on insert.

Revision ID: b7c4e1a9d2f3
Revises: 880764c4c4b4
Create Date: 2026-11-04 15:45:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b7c4e1a9d2f3"
down_revision: str | None = "880764c4c4b4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("users") as batch_op:
        batch_op.alter_column(
            "avatar_url",
            existing_type=sa.String(length=1024),
            type_=sa.Text(),
            existing_nullable=True,
        )


def downgrade() -> None:
    with op.batch_alter_table("users") as batch_op:
        batch_op.alter_column(
            "avatar_url",
            existing_type=sa.Text(),
            type_=sa.String(length=1024),
            existing_nullable=True,
        )