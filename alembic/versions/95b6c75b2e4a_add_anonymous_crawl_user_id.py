"""add anonymous crawl user id

Revision ID: 95b6c75b2e4a
Revises: 5d9d5219d656
Create Date: 2026-08-30 17:58:00.000000
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "95b6c75b2e4a"
down_revision: Union[str, None] = "5d9d5219d656"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "crawls", sa.Column("anonymous_user_id", sa.String(length=41), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("crawls", "anonymous_user_id")
